"""
sensors.py — Realistic Oil & Gas Sensor Models

This module defines sensor classes that generate realistic industrial
process data. Each sensor type simulates a specific physical measurement
you'd find on an offshore separation platform.

WHY REALISTIC DATA MATTERS:
Random numbers (e.g., random.uniform(0, 100)) are useless for testing
industrial systems because they don't exhibit the patterns that cause
real problems: gradual drift, daily cycling, sudden spikes, sensor
freeze, and pre-failure degradation signatures.

HOW TO READ THIS FILE:
- Each sensor class inherits from BaseSensor
- BaseSensor handles: timestamps, quality codes, alarm checking
- Each subclass implements generate_value() with realistic behavior
- The SensorFactory creates sensors from configuration dictionaries
"""

import math        # For sine waves (cycling) and mathematical functions
import random      # For noise injection and random anomaly timing
import time        # For timestamps
from enum import Enum     # For quality codes and alarm states
from dataclasses import dataclass, field  # For clean data structures


# ============================================================================
# DATA TYPES
# ============================================================================

class QualityCode(str, Enum):
    """
    OPC-UA Quality Codes — standard way to indicate data reliability.
    
    In industrial systems, EVERY sensor reading has a quality flag.
    A reading of 72.3°C with quality "Bad" means the transmitter
    is malfunctioning — the value cannot be trusted.
    
    A reading of 72.3°C with quality "Good" that hasn't changed
    in 8 hours might actually be a FROZEN sensor — the transmitter
    is stuck. This is why we also track "last_changed" timestamps.
    """
    GOOD = "Good"                # Value is reliable
    BAD = "Bad"                  # Sensor failure, value unreliable
    UNCERTAIN = "Uncertain"      # Value might be okay but confidence is low


class AlarmState(str, Enum):
    """
    Standard 4-level alarm system used in O&G process control.
    
    NORMAL:    Value is within operating range
    LOW/HIGH:  Value is outside normal range but not dangerous
               → Operator should investigate
    LOLO/HIHI: Value is at dangerous level
               → Automatic shutdown may trigger
    """
    NORMAL = "Normal"
    LOW = "Low"
    HIGH = "High"
    LOLO = "LoLo"       # Low-Low: critically low
    HIHI = "HiHi"       # High-High: critically high


@dataclass
class SensorReading:
    """
    One data point from one sensor at one moment in time.
    
    This is the fundamental unit of industrial data.
    Every reading has: what was measured, when, by which device,
    what the value was, what unit it's in, whether it's trustworthy,
    and whether it's in alarm.
    
    dataclass automatically creates __init__, __repr__, and other
    methods from the field definitions. It's a clean way to define
    data structures without writing boilerplate code.
    """
    device_id: str          # Which device produced this reading (e.g., "SEP-V100")
    tag_name: str           # Which measurement (e.g., "inlet_pressure")
    value: float            # The measured value (e.g., 850.3)
    unit: str               # Engineering unit (e.g., "PSI", "°C", "bbl/day")
    quality: QualityCode    # Is this reading trustworthy?
    alarm_state: AlarmState # Is this value within acceptable range?
    timestamp: str          # ISO 8601 format (e.g., "2026-02-14T10:30:00.000Z")
    
    def to_dict(self) -> dict:
        """
        Convert to a dictionary for JSON serialization.
        
        The REST API returns JSON. Python's json module can't serialize
        dataclasses or Enums directly, so we convert to a plain dict
        with string values.
        """
        return {
            "device_id": self.device_id,
            "tag_name": self.tag_name,
            "value": round(self.value, 4),   # 4 decimal places is enough precision
            "unit": self.unit,
            "quality": self.quality.value,    # .value gets the string from the Enum
            "alarm_state": self.alarm_state.value,
            "timestamp": self.timestamp,
        }


# ============================================================================
# BASE SENSOR CLASS
# ============================================================================

class BaseSensor:
    """
    Base class for all sensor types.
    
    Handles common functionality:
    - Alarm limit checking
    - Quality code management
    - Timestamp generation
    - Anomaly injection (simulating pre-failure behavior)
    - Sensor freeze simulation
    
    Subclasses only need to implement generate_value() to define
    how the physical measurement behaves over time.
    """
    
    def __init__(self, config: dict):
        """
        Initialize a sensor from a configuration dictionary.
        
        Args:
            config: Dictionary with sensor parameters. Example:
                {
                    "device_id": "SEP-V100",
                    "tag_name": "inlet_pressure",
                    "unit": "PSI",
                    "nominal": 850.0,        # Normal operating value
                    "noise": 2.0,            # Random noise amplitude (±2 PSI)
                    "drift_rate": 0.001,     # Gradual drift per second
                    "alarm_low": 700.0,
                    "alarm_high": 1000.0,
                    "alarm_lolo": 600.0,
                    "alarm_hihi": 1100.0,
                }
        """
        # Identity — which device and measurement this sensor represents
        self.device_id = config["device_id"]
        self.tag_name = config["tag_name"]
        self.unit = config["unit"]
        
        # Operating parameters
        self.nominal = config["nominal"]     # The "normal" value we oscillate around
        self.noise = config.get("noise", 1.0)  # Random noise amplitude
        # .get("noise", 1.0) means: use config["noise"] if it exists, else use 1.0
        
        # Alarm limits — these define the acceptable operating envelope
        self.alarm_low = config.get("alarm_low")     # None if not set
        self.alarm_high = config.get("alarm_high")
        self.alarm_lolo = config.get("alarm_lolo")
        self.alarm_hihi = config.get("alarm_hihi")
        
        # Internal state
        self._current_value = self.nominal   # Start at the nominal value
        self._quality = QualityCode.GOOD     # Start healthy
        self._start_time = time.time()       # When the sensor was created
        self._reading_count = 0              # How many readings generated
        
        # Anomaly injection state
        self._anomaly_active = False         # Is an anomaly currently happening?
        self._anomaly_start = 0.0            # When did the current anomaly start?
        self._anomaly_duration = 0.0         # How long will this anomaly last?
        self._next_anomaly_time = time.time() + random.uniform(300, 600)
        # First anomaly happens 5-10 minutes after startup
        # random.uniform(a, b) returns a random float between a and b
        
        # Sensor freeze simulation
        # A "frozen" sensor keeps reporting the same value — a common failure mode
        # It's dangerous because the quality code stays "Good" even though
        # the sensor is actually broken. The value just... stops changing.
        self._freeze_probability = config.get("freeze_probability", 0.001)
        # 0.1% chance per reading of entering freeze mode
        self._is_frozen = False
        self._frozen_value = 0.0
    
    def read(self) -> SensorReading:
        """
        Generate one sensor reading.
        
        This is the main method called by the data collection system.
        It handles the full pipeline:
        1. Check if sensor should freeze or unfreeze
        2. Check if anomaly should start or stop
        3. Generate the physical value (delegated to subclass)
        4. Add noise
        5. Apply anomaly effects if active
        6. Check alarm limits
        7. Package into a SensorReading with timestamp and quality
        
        Returns:
            SensorReading with all fields populated
        """
        self._reading_count += 1
        now = time.time()
        elapsed = now - self._start_time  # Seconds since sensor started
        
        # --- Sensor freeze simulation ---
        # Check if we should enter freeze mode (random probability)
        if not self._is_frozen and random.random() < self._freeze_probability:
            # random.random() returns 0.0 to 1.0
            # If freeze_probability is 0.001, there's a 0.1% chance per reading
            self._is_frozen = True
            self._frozen_value = self._current_value
            self._quality = QualityCode.UNCERTAIN
            # We set UNCERTAIN, not BAD, because the sensor THINKS it's fine
            # A frozen sensor is hard to detect — the value looks plausible
        
        # Unfreeze after 30-120 seconds (simulating intermittent failure)
        if self._is_frozen:
            if random.random() < 0.02:  # ~2% chance per reading to unfreeze
                self._is_frozen = False
                self._quality = QualityCode.GOOD
            else:
                # Return the frozen value (same value every time)
                return self._make_reading(self._frozen_value)
        
        # --- Anomaly management ---
        # Check if it's time to start a new anomaly
        if not self._anomaly_active and now >= self._next_anomaly_time:
            self._anomaly_active = True
            self._anomaly_start = now
            self._anomaly_duration = random.uniform(120, 360)  # 2-6 minutes
        
        # Check if current anomaly should end
        if self._anomaly_active:
            if now - self._anomaly_start >= self._anomaly_duration:
                self._anomaly_active = False
                # Schedule next anomaly: 5-15 minutes from now
                self._next_anomaly_time = now + random.uniform(300, 900)
        
        # --- Generate the base value ---
        # This is where subclasses provide their specific behavior
        # (pressure drift, temperature cycling, vibration patterns, etc.)
        base_value = self.generate_value(elapsed)
        
        # --- Add random noise ---
        # Every real sensor has some noise. A pressure transmitter rated
        # for ±0.1% accuracy at 1000 PSI has ±1 PSI of noise.
        noise = random.gauss(0, self.noise * 0.3)
        # random.gauss(mean, std_dev) generates normally distributed noise
        # Using gaussian (bell curve) because real sensor noise follows
        # a normal distribution, not uniform distribution
        
        value = base_value + noise
        
        # --- Apply anomaly effects ---
        if self._anomaly_active:
            value = self.apply_anomaly(value, now - self._anomaly_start)
        
        # --- Occasional bad quality reading ---
        # Real sensors occasionally return bad data (communication error,
        # electrical interference, sensor recalibration)
        if random.random() < 0.005:  # 0.5% chance
            self._quality = QualityCode.BAD
            # On a bad reading, the value might be wildly wrong
            value = self.nominal + random.uniform(-50, 50)
        elif self._quality == QualityCode.BAD:
            # Recover from bad quality on next reading
            self._quality = QualityCode.GOOD
        
        self._current_value = value
        return self._make_reading(value)
    
    def generate_value(self, elapsed_seconds: float) -> float:
        """
        Generate the base physical value. Override in subclasses.
        
        Args:
            elapsed_seconds: Time since sensor started, for time-dependent behavior
            
        Returns:
            The raw physical value before noise and anomalies
        """
        return self.nominal  # Default: constant value (boring but correct)
    
    def apply_anomaly(self, value: float, anomaly_elapsed: float) -> float:
        """
        Apply anomaly effects to the value. Override in subclasses
        for specific failure signatures.
        
        Default anomaly: gradually increasing offset (drift acceleration)
        
        Args:
            value: The current value before anomaly
            anomaly_elapsed: Seconds since anomaly started
            
        Returns:
            Modified value with anomaly effects
        """
        # Default: value drifts upward at an accelerating rate
        # This simulates a process condition getting progressively worse
        drift = anomaly_elapsed * 0.05  # 0.05 units per second acceleration
        return value + drift
    
    def _check_alarm(self, value: float) -> AlarmState:
        """
        Check the value against alarm limits.
        
        Alarm priority (checked from most critical to least):
        HiHi/LoLo (critical) → High/Low (warning) → Normal
        
        Returns:
            The highest priority alarm state that applies
        """
        # Check critical alarms first
        if self.alarm_hihi is not None and value >= self.alarm_hihi:
            return AlarmState.HIHI
        if self.alarm_lolo is not None and value <= self.alarm_lolo:
            return AlarmState.LOLO
        # Then warning alarms
        if self.alarm_high is not None and value >= self.alarm_high:
            return AlarmState.HIGH
        if self.alarm_low is not None and value <= self.alarm_low:
            return AlarmState.LOW
        return AlarmState.NORMAL
    
    def _make_reading(self, value: float) -> SensorReading:
        """
        Package a value into a complete SensorReading.
        
        Adds: timestamp, quality code, alarm state
        """
        from datetime import datetime, timezone
        
        return SensorReading(
            device_id=self.device_id,
            tag_name=self.tag_name,
            value=value,
            unit=self.unit,
            quality=self._quality,
            alarm_state=self._check_alarm(value),
            # ISO 8601 timestamp with UTC timezone
            # Example: "2026-02-14T10:30:00.000Z"
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") +
                      f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z",
        )


# ============================================================================
# SPECIFIC SENSOR TYPES
# ============================================================================

class PressureSensor(BaseSensor):
    """
    Simulates a pressure transmitter on a separator vessel.
    
    Real behavior:
    - Gradual drift over hours (process conditions change)
    - Small oscillations from control valve action (controller hunting)
    - Occasional spikes from slug flow (liquid slugs hitting the separator)
    
    Anomaly: Pressure oscillation — the control valve is sticking.
    The controller overcorrects, causing pressure to swing back and forth
    with increasing amplitude. This is a classic control loop problem.
    """
    
    def __init__(self, config: dict):
        super().__init__(config)
        # super().__init__(config) calls BaseSensor.__init__(config)
        # This is how inheritance works: the child class adds its own
        # initialization ON TOP of the parent's initialization
        
        self.drift_rate = config.get("drift_rate", 0.001)  # PSI per second drift
        self.oscillation_period = config.get("oscillation_period", 120)  # seconds
        self.oscillation_amplitude = config.get("oscillation_amplitude", 3.0)  # PSI
    
    def generate_value(self, elapsed: float) -> float:
        """Generate realistic pressure behavior."""
      
        drift = self.drift_rate * elapsed * math.sin(elapsed / 3600)
        
        oscillation = self.oscillation_amplitude * math.sin(
            2 * math.pi * elapsed / self.oscillation_period
        )
      
        return self.nominal + drift + oscillation
    
    def apply_anomaly(self, value: float, anomaly_elapsed: float) -> float:
        """
        Anomaly: Sticking control valve causing growing oscillations.
        
        The oscillation amplitude increases over time as the controller
        tries harder to correct. A normal 3 PSI oscillation grows to
        15-20 PSI. This is something an operator would notice on the
        trend display.
        """
        # Amplitude grows with time (gets worse)
        growing_amplitude = 5.0 + anomaly_elapsed * 0.05
        # After 60 seconds: 5 + 3 = 8 PSI swing
        # After 120 seconds: 5 + 6 = 11 PSI swing
        # After 300 seconds: 5 + 15 = 20 PSI swing
        
        oscillation = growing_amplitude * math.sin(
            2 * math.pi * anomaly_elapsed / 15  # Fast 15-second oscillation
        )
        return value + oscillation


class TemperatureSensor(BaseSensor):
    """
    Simulates a temperature transmitter.
    
    Real behavior:
    - Daily thermal cycling (ambient temperature affects process)
    - Slow response time (temperature changes lag behind pressure changes)
    
    Anomaly: Heat exchanger fouling — temperature gradually rises because
    heat transfer efficiency has decreased. The fouling builds up over the
    anomaly duration, showing as a persistent upward trend.
    """
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.daily_amplitude = config.get("daily_amplitude", 5.0)  # °C daily swing
    
    def generate_value(self, elapsed: float) -> float:
        """Generate realistic temperature with daily cycling."""
        # Daily thermal cycle: temperature rises during "day" and falls at "night"
        # 86400 seconds = 24 hours
        daily_cycle = self.daily_amplitude * math.sin(
            2 * math.pi * elapsed / 86400
        )
        
        # Very slow drift (temperature trends change over days, not minutes)
        slow_drift = 0.0005 * elapsed * math.sin(elapsed / 7200)
        
        return self.nominal + daily_cycle + slow_drift
    
    def apply_anomaly(self, value: float, anomaly_elapsed: float) -> float:
        """Anomaly: Heat exchanger fouling — temperature rises steadily."""
        # Temperature increases by 0.03°C per second during anomaly
        # Over 5 minutes that's +9°C — noticeable on a trend display
        fouling_effect = anomaly_elapsed * 0.03
        return value + fouling_effect


class FlowSensor(BaseSensor):
    """
    Simulates a flow transmitter (measuring oil or gas flow rate).
    
    Real behavior:
    - Production decline curve (flow slowly decreases over time as
      reservoir pressure drops — fundamental to oil production)
    - Slugging (periodic surges of liquid in gas flow lines)
    
    Anomaly: Slug flow event — large liquid slugs cause dramatic
    flow spikes. This is a real operational problem on offshore
    platforms that can trip separators and cause shutdowns.
    """
    
    def __init__(self, config: dict):
        super().__init__(config)
        # Decline rate: how fast production decreases
        # 0.00001 means ~1% decline per day at 86400 seconds
        self.decline_rate = config.get("decline_rate", 0.00001)
    
    def generate_value(self, elapsed: float) -> float:
        """Generate flow with production decline curve."""
        # Exponential decline: Q(t) = Q0 * e^(-decline_rate * t)
        # This is the fundamental equation of reservoir engineering
        # math.exp(-x) = e^(-x), which starts at 1.0 and gradually decreases
        decline = math.exp(-self.decline_rate * elapsed)
        
        # Small flow variations (pump pulsation, valve adjustments)
        pulsation = 10.0 * math.sin(2 * math.pi * elapsed / 30)  # 30-second cycle
        
        return self.nominal * decline + pulsation
    
    def apply_anomaly(self, value: float, anomaly_elapsed: float) -> float:
        """Anomaly: Slug flow — sudden surges in flow rate."""
        # Slug flow creates sharp spikes that repeat every 10-20 seconds
        slug_cycle = 12.0  # seconds between slugs
        # math.fmod returns the remainder of division (like % but for floats)
        phase = math.fmod(anomaly_elapsed, slug_cycle) / slug_cycle
        
        if phase < 0.3:  # 30% of the cycle is the spike
            # Sharp spike: flow jumps 50-100% above normal
            spike = self.nominal * 0.5 * math.sin(math.pi * phase / 0.3)
            return value + spike
        return value  # Rest of cycle: normal flow


class VibrationSensor(BaseSensor):
    """
    Simulates a vibration sensor on a rotating machine (pump, compressor).
    
    Real behavior:
    - Baseline vibration level (depends on machine speed and condition)
    - Random variation (normal for rotating equipment)
    
    Anomaly: Bearing degradation — the most common and most important
    predictive maintenance signature in O&G. A failing bearing shows:
    1. Increasing overall vibration amplitude
    2. Harmonic frequencies appearing (multiples of rotation speed)
    3. Intermittent spikes as damaged surfaces contact
    
    This is what condition monitoring systems look for. If your
    gateway can capture this pattern, maintenance teams can replace
    the bearing during a planned shutdown instead of an emergency.
    """
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.baseline = config.get("baseline", 1.2)  # mm/s RMS baseline
    
    def generate_value(self, elapsed: float) -> float:
        """Generate baseline vibration level."""
        # Small random variation around baseline (normal machine behavior)
        variation = 0.1 * math.sin(2 * math.pi * elapsed / 45)
        return self.baseline + variation
    
    def apply_anomaly(self, value: float, anomaly_elapsed: float) -> float:
        """
        Anomaly: Bearing inner race defect — classic degradation signature.
        
        The vibration increases gradually with intermittent spikes.
        This is textbook predictive maintenance: if you detect this
        early, you schedule a bearing replacement. If you miss it,
        the bearing seizes and destroys the pump.
        """
        # Overall amplitude increase (gets worse over time)
        amplitude_increase = anomaly_elapsed * 0.01  # 0.01 mm/s per second
       
        spike = 0.0
        if random.random() < 0.15:  # 15% chance per reading
            spike = random.uniform(0.5, 2.0)  # Spike magnitude
        
        # Harmonic content (vibration at 2x and 3x base frequency)
        harmonic = 0.3 * amplitude_increase * math.sin(
            2 * math.pi * anomaly_elapsed * 2 / 45  # 2x frequency
        )
        
        return value + amplitude_increase + spike + harmonic


class LevelSensor(BaseSensor):
    """
    Simulates a level transmitter on a separator vessel.
    
    Real behavior:
    - Level oscillates as liquid enters and exits the vessel
    - Control system maintains level at setpoint (typically 50%)
    - Slow changes when inlet flow rate changes
    
    Anomaly: Level control failure — the level dump valve is stuck
    and level rises uncontrolled. This is a serious operational
    concern because high level in a separator means liquid
    carryover to the gas outlet (very bad).
    """
    
    def __init__(self, config: dict):
        super().__init__(config)
        self.control_period = config.get("control_period", 60)  # seconds
    
    def generate_value(self, elapsed: float) -> float:
        """Generate level with control system behavior."""
        # Level controller keeps level oscillating slightly around setpoint
        control_action = 2.0 * math.sin(2 * math.pi * elapsed / self.control_period)
        
        # Slow drift from changing inlet conditions
        inlet_effect = 1.0 * math.sin(elapsed / 1800)  # 30-minute cycle
        
        return self.nominal + control_action + inlet_effect
    
    def apply_anomaly(self, value: float, anomaly_elapsed: float) -> float:
        """Anomaly: Stuck dump valve — level rises uncontrolled."""
        # Level rises at 0.1% per second (10% per 100 seconds)
        level_rise = anomaly_elapsed * 0.1
        return value + level_rise


# ============================================================================
# SENSOR FACTORY
# ============================================================================

# This maps sensor type names to their classes.
# When the config says "type": "pressure", we create a PressureSensor.
SENSOR_TYPES = {
    "pressure": PressureSensor,
    "temperature": TemperatureSensor,
    "flow": FlowSensor,
    "vibration": VibrationSensor,
    "level": LevelSensor,
}


def create_sensor(config: dict) -> BaseSensor:
    """
    Factory function — creates the right sensor type from a config dict.
    
    A "factory" is a common design pattern: instead of the caller knowing
    which class to instantiate, the factory decides based on the input.
    
    Args:
        config: Must include "type" key matching SENSOR_TYPES
        
    Returns:
        An instance of the appropriate sensor subclass
        
    Raises:
        ValueError: If sensor type is not recognized
    """
    sensor_type = config.get("type", "").lower()
    
    if sensor_type not in SENSOR_TYPES:
        raise ValueError(
            f"Unknown sensor type: '{sensor_type}'. "
            f"Available types: {list(SENSOR_TYPES.keys())}"
        )
    
    # SENSOR_TYPES[sensor_type] returns the CLASS (e.g., PressureSensor)
    # Adding (config) CALLS the class constructor, creating an instance
    return SENSOR_TYPES[sensor_type](config)
