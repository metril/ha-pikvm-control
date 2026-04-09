"""Constants for the PiKVM Control integration."""

DOMAIN = "pikvm"

# Config entry data keys
CONF_PIKVM_URL = "url"
CONF_PIKVM_USER = "username"
CONF_PIKVM_PASS = "password"
CONF_PIKVM_TOTP_SECRET = "totp_secret"
CONF_VERIFY_SSL = "verify_ssl"

# Options flow keys and defaults
CONF_HDD_HOLD_TIME = "hdd_hold_time"
DEFAULT_HDD_HOLD_TIME = 5  # seconds

CONF_WS_RECONNECT_DELAY = "ws_reconnect_delay"
DEFAULT_WS_RECONNECT_DELAY = 5  # seconds

CONF_HTTP_TIMEOUT = "http_timeout"
DEFAULT_HTTP_TIMEOUT = 10  # seconds
