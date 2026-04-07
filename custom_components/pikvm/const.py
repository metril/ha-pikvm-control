"""Constants for the PiKVM Control integration."""

DOMAIN = "pikvm"

# Config entry data keys
CONF_PIKVM_URL = "url"
CONF_PIKVM_USER = "username"
CONF_PIKVM_PASS = "password"
CONF_PIKVM_TOTP_SECRET = "totp_secret"
CONF_VERIFY_SSL = "verify_ssl"

# Reconnect delay for WebSocket
WS_RECONNECT_DELAY = 5  # seconds
