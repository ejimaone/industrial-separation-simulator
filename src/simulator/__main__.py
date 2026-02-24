"""
__main__.py — Application Entry Point


"""

import json        # For loading the sensor configuration file
import logging     # For structured logging throughout the application
import os          # For reading environment variables
import signal      # For handling SIGTERM (docker stop) and SIGINT (Ctrl+C)
import sys         # For sys.exit()
import time        # For sleep in the main loop
from pathlib import Path  # For clean file path handling

# Import our modules
from .sensors import create_sensor, BaseSensor  # Sensor factory and base type
from .storage import SensorStorage               # SQLite database
from .api import start_api_server                # REST API

# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(level: str = "INFO") -> None:
   
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        # getattr(logging, "INFO") returns logging.INFO (the integer 20)
        # getattr(logging, "DEBUG") returns logging.DEBUG (the integer 10)
        # This converts the string "INFO" to the logging constant
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
       
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
     
    )

logger = logging.getLogger(__name__)



class SensorManager:
   
    
    def __init__(self, config_path: str, storage: SensorStorage):
       
        self.storage = storage
        self.sensors: list[BaseSensor] = []  # List of sensor instances
        self.is_collecting = False            # Health check flag
        self._running = False                 # Controls the main loop
        self._start_time = time.time()        # For uptime tracking
        
        # Load sensor configuration
        self._load_config(config_path)
    
    def _load_config(self, config_path: str) -> None:
       
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                # json.load(f) reads the file and parses JSON into Python dicts/lists
            
            # Create sensor instances from configuration
            for sensor_config in config.get("sensors", []):
                try:
                    sensor = create_sensor(sensor_config)
                    self.sensors.append(sensor)
                    logger.info(
                        f"Created sensor: {sensor.device_id}/{sensor.tag_name} "
                        f"({sensor.__class__.__name__})"
                    )
                    # sensor.__class__.__name__ gives us "PressureSensor",
                    # "TemperatureSensor", etc.
                except (ValueError, KeyError) as e:
                    logger.error(f"Failed to create sensor: {e}. Config: {sensor_config}")
                    # Don't crash the whole app if one sensor config is bad
                    # Log the error and continue with the other sensors
            
            logger.info(f"Loaded {len(self.sensors)} sensors from {config_path}")
        
        except FileNotFoundError:
            logger.error(f"Config file not found: {config_path}")
            sys.exit(1)  # Can't run without configuration  exit with error
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file: {e}")
            sys.exit(1)
    
    @property
    def uptime(self) -> float:
       
        return round(time.time() - self._start_time, 1)
    
    def run(self, poll_interval_ms: int = 1000, cleanup_interval_hours: float = 24.0):
       
        self._running = True
        self.is_collecting = True
        poll_interval = poll_interval_ms / 1000.0  # Convert ms to seconds
        
        logger.info(
            f"Starting data collection: {len(self.sensors)} sensors, "
            f"{poll_interval_ms}ms interval"
        )
        
        cycle_count = 0
        last_cleanup = time.time()
        cleanup_interval = cleanup_interval_hours * 3600  # Convert to seconds
        
        while self._running:
            cycle_start = time.time()
            cycle_count += 1
            
            # ---- Collect readings from all sensors ----
            readings = []
            for sensor in self.sensors:
                try:
                    reading = sensor.read()
                    readings.append(reading)
                except Exception as e:
                    logger.error(
                        f"Error reading sensor {sensor.device_id}/{sensor.tag_name}: {e}"
                    )
                    # Don't let one broken sensor stop all collection
            
            # ---- Store readings in batch ----
            if readings:
                stored = self.storage.store_batch(readings)
                
                # Log alarm states (but not every cycle — too noisy)
                if cycle_count % 30 == 0:  # Every 30 cycles (~30 seconds)
                    alarms = [r for r in readings if r.alarm_state != AlarmState.NORMAL]
                    if alarms:
                        for r in alarms:
                            logger.warning(
                                f"ALARM [{r.alarm_state.value}] "
                                f"{r.device_id}/{r.tag_name}: "
                                f"{r.value:.2f} {r.unit}"
                            )
                    # Log collection summary
                    logger.info(
                        f"Cycle {cycle_count}: {stored}/{len(readings)} readings stored, "
                        f"{len(alarms)} alarms active"
                    )
            
            # ---- Periodic cleanup ----
            if time.time() - last_cleanup >= cleanup_interval:
                self.storage.cleanup(max_hours=cleanup_interval_hours)
                last_cleanup = time.time()
            
            # ---- Wait for next cycle ----
            # Calculate how long the collection took and sleep the remainder
            elapsed = time.time() - cycle_start
            sleep_time = max(0, poll_interval - elapsed)
            # max(0, ...) ensures we don't sleep negative time
            # if collection took longer than the interval, we skip sleeping
            
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif elapsed > poll_interval * 2:
                # Collection took more than 2x the interval — warn about it
                logger.warning(
                    f"Collection cycle took {elapsed:.2f}s "
                    f"(interval is {poll_interval}s) — falling behind!"
                )
        
        self.is_collecting = False
        logger.info(f"Data collection stopped after {cycle_count} cycles")
    
    def stop(self):
        """Signal the collection loop to stop."""
        logger.info("Stopping data collection...")
        self._running = False


# We need this import for the alarm check in the run loop
from .sensors import AlarmState




def main():
    
    # ---- Read configuration from environment variables ----
    # os.environ.get("KEY", "default") reads the env var, or uses default
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    api_host = os.environ.get("API_HOST", "0.0.0.0")
    api_port = int(os.environ.get("API_PORT", "8080"))
    db_path = os.environ.get("DB_PATH", "/data/sensors.db")
    config_path = os.environ.get("CONFIG_PATH", "/app/config/sensors.json")
    poll_interval_ms = int(os.environ.get("POLL_INTERVAL_MS", "1000"))
    cleanup_hours = float(os.environ.get("CLEANUP_HOURS", "24"))
    
    # ---- Setup logging ----
    setup_logging(log_level)
    logger.info("=" * 60)
    logger.info("IronClad Sensor Simulator Starting")
    logger.info(f"  Log Level:     {log_level}")
    logger.info(f"  API:           {api_host}:{api_port}")
    logger.info(f"  Database:      {db_path}")
    logger.info(f"  Config:        {config_path}")
    logger.info(f"  Poll Interval: {poll_interval_ms}ms")
    logger.info(f"  Cleanup After: {cleanup_hours}h")
    logger.info("=" * 60)
    
    # ---- Initialize components ----
    storage = SensorStorage(db_path)
    manager = SensorManager(config_path, storage)
    
    if not manager.sensors:
        logger.error("No sensors configured! Check your config file.")
        sys.exit(1)
    
    # ---- Start REST API (background thread) ----
    api_server = start_api_server(api_host, api_port, storage, manager)
    
   
    
    def shutdown_handler(signum, frame):
       
        signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.info(f"Received {signal_name} — shutting down gracefully...")
        
        # Stop the data collection loop
        manager.stop()
        
        # Stop the API server
        api_server.shutdown()
        
        # Close the database connection (flushes any pending writes)
        storage.close()
        
        logger.info("Shutdown complete. Goodbye!")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    
    # ---- Start data collection (runs forever until signal) ----
    try:
        manager.run(
            poll_interval_ms=poll_interval_ms,
            cleanup_interval_hours=cleanup_hours,
        )
    except Exception as e:
        logger.error(f"Fatal error in collection loop: {e}", exc_info=True)
        # exc_info=True includes the full stack trace in the log
        storage.close()
        sys.exit(1)

if __name__ == "__main__":
    main()
