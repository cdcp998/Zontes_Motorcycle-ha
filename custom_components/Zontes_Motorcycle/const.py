"""Constants for the Zontes Smart Motorcycle integration."""
DOMAIN = "zontes_motorcycle"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_COORD_TYPE = "coord_type"

DEFAULT_SCAN_INTERVAL = 30
DEFAULT_COORD_TYPE = "GCJ-02"

COORD_TYPES = {
    "wgs84": "WGS84",
    "gcj02": "GCJ-02",
}

ATTR_MOTOR_CODE = "motor_code"
ATTR_ACCESS_TOKEN = "access_token"
ATTR_USER_INFO = "user_info"

# Error keys
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_NOT_REGISTERED = "not_registered"
ERROR_WRONG_PASSWORD = "wrong_password"