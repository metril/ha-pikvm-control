"""Config flow for PiKVM Control integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithConfigEntry,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import PikvmApiClient, PikvmAuthError, PikvmConnectionError
from .const import (
    CONF_HDD_HOLD_TIME,
    CONF_HTTP_TIMEOUT,
    CONF_PIKVM_PASS,
    CONF_PIKVM_TOTP_SECRET,
    CONF_PIKVM_URL,
    CONF_PIKVM_USER,
    CONF_VERIFY_SSL,
    CONF_WS_RECONNECT_DELAY,
    DEFAULT_HDD_HOLD_TIME,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_WS_RECONNECT_DELAY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PIKVM_URL): str,
        vol.Required(CONF_PIKVM_USER, default="admin"): str,
        vol.Required(CONF_PIKVM_PASS): str,
        vol.Required(CONF_PIKVM_TOTP_SECRET): str,
        vol.Optional(CONF_VERIFY_SSL, default=False): bool,
    }
)


class PikvmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PiKVM Control."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> PikvmOptionsFlowHandler:
        """Get the options flow handler."""
        return PikvmOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_PIKVM_URL] = user_input[CONF_PIKVM_URL].rstrip("/")

            error = await self._test_connection(user_input)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(user_input[CONF_PIKVM_URL])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"PiKVM ({user_input[CONF_PIKVM_URL]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when credentials become invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth confirmation."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            updated_data = {**reauth_entry.data, **user_input}
            error = await self._test_connection(updated_data)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data=updated_data
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PIKVM_USER, default=reauth_entry.data[CONF_PIKVM_USER]): str,
                    vol.Required(CONF_PIKVM_PASS): str,
                    vol.Required(CONF_PIKVM_TOTP_SECRET): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            user_input[CONF_PIKVM_URL] = user_input[CONF_PIKVM_URL].rstrip("/")
            error = await self._test_connection(user_input)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    reconfigure_entry, data=user_input
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PIKVM_URL, default=reconfigure_entry.data[CONF_PIKVM_URL]): str,
                    vol.Required(CONF_PIKVM_USER, default=reconfigure_entry.data[CONF_PIKVM_USER]): str,
                    vol.Required(CONF_PIKVM_PASS): str,
                    vol.Required(CONF_PIKVM_TOTP_SECRET): str,
                    vol.Optional(CONF_VERIFY_SSL, default=reconfigure_entry.data.get(CONF_VERIFY_SSL, False)): bool,
                }
            ),
            errors=errors,
        )

    async def _test_connection(self, data: dict[str, Any]) -> str | None:
        """Test the connection to PiKVM."""
        verify_ssl = data.get(CONF_VERIFY_SSL, False)
        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)

        client = PikvmApiClient(
            session=session,
            url=data[CONF_PIKVM_URL],
            username=data[CONF_PIKVM_USER],
            password=data[CONF_PIKVM_PASS],
            totp_secret=data[CONF_PIKVM_TOTP_SECRET],
            verify_ssl=verify_ssl,
        )

        try:
            await client.test_connection()
        except PikvmAuthError:
            return "invalid_auth"
        except PikvmConnectionError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during PiKVM connection test")
            return "unknown"
        return None


class PikvmOptionsFlowHandler(OptionsFlowWithConfigEntry):
    """Handle PiKVM options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HDD_HOLD_TIME,
                        default=options.get(CONF_HDD_HOLD_TIME, DEFAULT_HDD_HOLD_TIME),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=30, step=1, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(
                        CONF_WS_RECONNECT_DELAY,
                        default=options.get(CONF_WS_RECONNECT_DELAY, DEFAULT_WS_RECONNECT_DELAY),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=30, step=1, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(
                        CONF_HTTP_TIMEOUT,
                        default=options.get(CONF_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=5, max=60, step=1, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                }
            ),
        )
