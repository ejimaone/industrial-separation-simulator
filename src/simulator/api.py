

import json                          # For JSON serialization
import logging                       # For structured logging
import threading                     # To run the server in a background thread
from http.server import HTTPServer, BaseHTTPRequestHandler  # Built-in HTTP server
from urllib.parse import urlparse, parse_qs  # For parsing URL paths and query strings

logger = logging.getLogger(__name__)


class SensorAPIHandler(BaseHTTPRequestHandler):
    
    
    def do_GET(self):
      
        # Parse the URL path
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")  # Remove trailing slash
        # parse_qs parses query parameters: "?hours=2" → {"hours": ["2"]}
        query_params = parse_qs(parsed.query)
        
        # Route to the appropriate handler
        # We use a simple if/elif chain instead of a routing framework
        try:
            if path == "/health":
                self._handle_health()
            
            elif path == "/readings":
                self._handle_readings()
            
            elif path.startswith("/readings/"):
                # Path like: /readings/SEP-V100/inlet_pressure
                parts = path.split("/")
                # split("/") on "/readings/SEP-V100/inlet_pressure" gives:
                # ["", "readings", "SEP-V100", "inlet_pressure"]
                if len(parts) == 4:
                    device_id = parts[2]
                    tag_name = parts[3]
                    self._handle_reading_detail(device_id, tag_name)
                else:
                    self._send_error(400, "Expected /readings/{device_id}/{tag_name}")
            
            elif path.startswith("/history/"):
                parts = path.split("/")
                if len(parts) == 4:
                    device_id = parts[2]
                    tag_name = parts[3]
                    # Get 'hours' from query params, default to 1.0
                    hours = float(query_params.get("hours", ["1.0"])[0])
                    self._handle_history(device_id, tag_name, hours)
                else:
                    self._send_error(400, "Expected /history/{device_id}/{tag_name}")
            
            elif path == "/stats":
                self._handle_stats()
            
            elif path == "/alarms":
                self._handle_alarms()
            
            else:
                self._send_error(404, f"Unknown endpoint: {path}")
        
        except Exception as e:
            logger.error(f"Error handling {self.path}: {e}")
            self._send_error(500, f"Internal server error: {str(e)}")
    
    def _handle_health(self):
        
        storage = self.server.storage
        sensor_manager = self.server.sensor_manager
        
        # Check each health component
        collecting = sensor_manager.is_collecting if sensor_manager else False
        db_writable = storage.is_writable() if storage else False
        stats = storage.get_stats() if storage else {}
        
        health = {
            "status": "healthy" if (collecting and db_writable) else "degraded",
            "collecting": collecting,
            "database_writable": db_writable,
            "sensor_count": stats.get("sensor_count", 0),
            "total_readings": stats.get("total_readings", 0),
            "active_alarms": stats.get("active_alarms", 0),
            "database_size_mb": stats.get("database_size_mb", 0.0),
            "uptime_seconds": sensor_manager.uptime if sensor_manager else 0,
        }
        
        status_code = 200 if health["status"] == "healthy" else 503
        self._send_json(health, status_code)
    
    def _handle_readings(self):
        
        readings = self.server.storage.get_latest()
        self._send_json({"readings": readings, "count": len(readings)})
    
    def _handle_reading_detail(self, device_id: str, tag_name: str):
       
        readings = self.server.storage.get_latest()
        # Filter for the specific sensor
        matching = [
            r for r in readings
            if r["device_id"] == device_id and r["tag_name"] == tag_name
        ]
        # List comprehension: filters the list to only matching readings
        
        if matching:
            self._send_json(matching[0])
        else:
            self._send_error(404, f"No readings for {device_id}/{tag_name}")
    
    def _handle_history(self, device_id: str, tag_name: str, hours: float):
       
        readings = self.server.storage.get_history(device_id, tag_name, hours)
        self._send_json({
            "device_id": device_id,
            "tag_name": tag_name,
            "hours": hours,
            "readings": readings,
            "count": len(readings),
        })
    
    def _handle_stats(self):
        """GET /stats — Database and system statistics."""
        stats = self.server.storage.get_stats()
        self._send_json(stats)
    
    def _handle_alarms(self):
        
        readings = self.server.storage.get_latest()
        alarms = [r for r in readings if r["alarm_state"] != "Normal"]
        self._send_json({"alarms": alarms, "count": len(alarms)})
    
    # ---- Helper Methods ----
    
    def _send_json(self, data: dict, status_code: int = 200):
        """Send a JSON response with proper headers."""
        response_body = json.dumps(data, indent=2).encode("utf-8")
        # json.dumps: Convert dict to JSON string
        # indent=2: Pretty-print with 2-space indentation
        # .encode("utf-8"): Convert string to bytes (HTTP sends bytes)
        
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)
    
    def _send_error(self, status_code: int, message: str):
        """Send an error response."""
        self._send_json({"error": message, "status_code": status_code}, status_code)
    
    def log_message(self, format, *args):
        
        message = format % args
        # Don't log health check requests (they're too frequent)
        if "/health" not in message:
            logger.debug(f"API: {message}")


def start_api_server(host: str, port: int, storage, sensor_manager) -> HTTPServer:
   
    server = HTTPServer((host, port), SensorAPIHandler)
    
    # Attach our storage and manager to the server instance
    # The request handler accesses these via self.server.storage
    server.storage = storage
    server.sensor_manager = sensor_manager
    
    # Run the server in a background thread
    # daemon=True means the thread dies when the main program exits
    # Without daemon=True, the thread would keep the program alive forever
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    logger.info(f"API server started on {host}:{port}")
    return server
