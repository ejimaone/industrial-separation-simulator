

import sqlite3     # Built-in Python SQLite library
import logging     # For structured logging
import time        # For timestamps in cleanup
from pathlib import Path  # For clean file path handling
from typing import Optional  # For type hints with optional values

from .sensors import SensorReading


logger = logging.getLogger(__name__)


class SensorStorage:
    """
    Persistent storage for sensor readings using SQLite.
    
    Thread safety: SQLite in WAL mode (Write-Ahead Logging) allows
    multiple readers and one writer simultaneously. Good enough for
    our use case where one thread writes readings and the API thread reads.
    """
    
    def __init__(self, db_path: str = "/data/sensors.db"):
        """
        Initialize the database connection and create the table.
        
        Args:
            db_path: File path for the SQLite database.
                     Default is /data/sensors.db — this will be on a
                     Docker named volume so it survives container restarts.
        """
        self.db_path = db_path
        
       
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Connect to SQLite database (creates file if it doesn't exist)
        self.conn = sqlite3.connect(
            db_path,
            check_same_thread=False,  # Allow access from multiple threads
            # Our API runs in a different thread than the data collection loop.
            # SQLite with WAL mode handles this safely.
        )
        
        # Enable WAL mode for better concurrent read/write performance
        self.conn.execute("PRAGMA journal_mode=WAL")
       
        
        # Enable foreign keys (good practice, even if we don't use them yet)
        self.conn.execute("PRAGMA foreign_keys=ON")
        
        # Create the readings table if it doesn't exist
        self._create_tables()
        
        logger.info(f"Database initialized at {db_path}")
    
    def _create_tables(self):
        
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                tag_name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                quality TEXT NOT NULL,
                alarm_state TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)
        
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_readings_device_tag 
            ON sensor_readings(device_id, tag_name)
        """)
        
        # Index on created_at for efficient cleanup queries
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_readings_created 
            ON sensor_readings(created_at)
        """)
        
        self.conn.commit()
       
    
    def store(self, reading: SensorReading) -> None:
       
        try:
            self.conn.execute(
                """
                INSERT INTO sensor_readings 
                    (device_id, tag_name, value, unit, quality, alarm_state, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reading.device_id,
                    reading.tag_name,
                    reading.value,
                    reading.unit,
                    reading.quality.value,       # .value gets string from Enum
                    reading.alarm_state.value,
                    reading.timestamp,
                )
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to store reading: {e}")
            # Don't crash the app on storage failure — log it and continue
            # The next reading will try again. This is defensive programming.
    
    def store_batch(self, readings: list[SensorReading]) -> int:
        
        try:
            self.conn.executemany(
                """
                INSERT INTO sensor_readings 
                    (device_id, tag_name, value, unit, quality, alarm_state, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (r.device_id, r.tag_name, r.value, r.unit,
                     r.quality.value, r.alarm_state.value, r.timestamp)
                    for r in readings
                ]
            )
            # The list comprehension above creates a list of tuples from readings
            # executemany() inserts all of them in one operation
            self.conn.commit()
            return len(readings)
        except sqlite3.Error as e:
            logger.error(f"Failed to store batch of {len(readings)} readings: {e}")
            return 0
    
    def get_latest(self) -> list[dict]:
        
        cursor = self.conn.execute("""
            SELECT device_id, tag_name, value, unit, quality, alarm_state, timestamp
            FROM sensor_readings
            WHERE id IN (
                SELECT MAX(id) FROM sensor_readings GROUP BY device_id, tag_name
            )
            ORDER BY device_id, tag_name
        """)
        
        # cursor.fetchall() returns a list of tuples
        # We convert each tuple to a dict for JSON serialization
        columns = ["device_id", "tag_name", "value", "unit", 
                    "quality", "alarm_state", "timestamp"]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
        # zip(columns, row) pairs column names with values:
        # ("device_id", "SEP-V100"), ("tag_name", "inlet_pressure"), ...
        # dict() converts those pairs into a dictionary
    
    def get_history(self, device_id: str, tag_name: str, 
                     hours: float = 1.0) -> list[dict]:
        
        cutoff = time.time() - (hours * 3600)  # Convert hours to seconds
        
        cursor = self.conn.execute(
            """
            SELECT device_id, tag_name, value, unit, quality, alarm_state, timestamp
            FROM sensor_readings
            WHERE device_id = ? AND tag_name = ? AND created_at >= ?
            ORDER BY id ASC
            """,
            (device_id, tag_name, cutoff)
        )
        
        columns = ["device_id", "tag_name", "value", "unit",
                    "quality", "alarm_state", "timestamp"]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def get_stats(self) -> dict:
        
        # Total reading count
        total = self.conn.execute(
            "SELECT COUNT(*) FROM sensor_readings"
        ).fetchone()[0]
        # .fetchone() returns one row as a tuple: (12345,)
        # [0] gets the first (and only) element: 12345
        
        # Active alarms (most recent reading per sensor that's in alarm)
        alarms = self.conn.execute("""
            SELECT COUNT(*) FROM sensor_readings
            WHERE id IN (
                SELECT MAX(id) FROM sensor_readings GROUP BY device_id, tag_name
            ) AND alarm_state != 'Normal'
        """).fetchone()[0]
        
        # Database file size
        try:
            db_size_bytes = Path(self.db_path).stat().st_size
            db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
        except OSError:
            db_size_mb = 0.0
        
        # Distinct sensors (how many unique device+tag combinations)
        sensor_count = self.conn.execute(
            "SELECT COUNT(DISTINCT device_id || '.' || tag_name) FROM sensor_readings"
        ).fetchone()[0]
        # || is SQLite string concatenation
        # DISTINCT counts only unique combinations
        
        return {
            "total_readings": total,
            "active_alarms": alarms,
            "sensor_count": sensor_count,
            "database_size_mb": db_size_mb,
        }
    
    def cleanup(self, max_hours: float = 24.0) -> int:
        
        cutoff = time.time() - (max_hours * 3600)
        
        cursor = self.conn.execute(
            "DELETE FROM sensor_readings WHERE created_at < ?",
            (cutoff,)
        )
        deleted = cursor.rowcount  # How many rows were deleted
        self.conn.commit()
        
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} readings older than {max_hours}h")
            # VACUUM reclaims disk space after deleting rows
            # Without VACUUM, SQLite keeps the space allocated for future use
            # On a disk-constrained edge device, we want the space back
            self.conn.execute("VACUUM")
        
        return deleted
    
    def is_writable(self) -> bool:
        
        try:
            self.conn.execute(
                "INSERT INTO sensor_readings "
                "(device_id, tag_name, value, unit, quality, alarm_state, timestamp) "
                "VALUES ('_health', '_check', 0, '', 'Good', 'Normal', '')"
            )
            self.conn.execute(
                "DELETE FROM sensor_readings WHERE device_id = '_health'"
            )
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False
    
    def close(self):
        """Close the database connection cleanly."""
        try:
            self.conn.close()
            logger.info("Database connection closed")
        except sqlite3.Error as e:
            logger.error(f"Error closing database: {e}")
