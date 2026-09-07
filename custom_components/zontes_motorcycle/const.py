"""Constants for the Zontes Smart Motorcycle integration."""

DOMAIN = "zontes_motorcycle"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_COORD_TYPE = "coord_type"

DEFAULT_SCAN_INTERVAL = 30
DEFAULT_COORD_TYPE = "gcj02"

COORD_TYPES = {
    "gcj02": "GCJ-02",
    "wgs84": "WGS84",
}

MANUFACTURER = "Zontes"

# Error keys
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_NOT_REGISTERED = "not_registered"
ERROR_WRONG_PASSWORD = "wrong_password"
