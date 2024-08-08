"""Config flow for Duplicati integration."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ID, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback

from .api import ApiResponseError, CannotConnect, DuplicatiBackendAPI, InvalidAuth
from .const import DOMAIN
from .options_flow import DuplicatiOptionsFlowHandler

_LOGGER = logging.getLogger(__name__)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_ID): str,
        vol.Optional(CONF_VERIFY_SSL, default=vol.Coerce(bool)(False)): bool,
    }
)


class DuplicatiConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Duplicati."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        # Process user input if provided
        if user_input is not None:
            try:
                # Check if an entry already exists with the same host
                for entry in self._async_current_entries():
                    if (
                        entry.data[CONF_URL] == user_input[CONF_URL]
                        and entry.data[CONF_ID] == user_input[CONF_ID]
                    ):
                        return self.async_abort(reason="already_configured")
                # Validate input
                backup_info = await self._async_validate_input(user_input)
                # Extract backup name
                if "data" in backup_info:
                    backup_name = backup_info["data"]["Backup"]["Name"]
                else:
                    raise ApiResponseError("Unable to get the backup name")
                # Define entry title
                host = urllib.parse.urlparse(user_input[CONF_URL]).netloc
                title = f"{backup_name} Backup (host={host}, id={user_input[CONF_ID]})"
                # Create entry
                return self.async_create_entry(title=title, data=user_input)
            except CannotConnect as e:
                _LOGGER.error("Failed to connect: %s", str(e))
                errors["base"] = "cannot_connect"
            except InvalidAuth as e:
                _LOGGER.error("Authentication failed: %s", str(e))
                errors["base"] = "invalid_auth"
            except ApiResponseError as e:
                _LOGGER.error("API response error: %s", str(e))
                errors["base"] = "invalid_id"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
        # Show form
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def _async_validate_input(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process user input and create new or update existing config entry."""
        try:
            base_url = data[CONF_URL]
            backup_id = data[CONF_ID]
            verify_ssl = data[CONF_VERIFY_SSL]
            password = None  # Duplicati UI PW is not yet supported
            # Create API instance
            api = DuplicatiBackendAPI(base_url, verify_ssl, password)
            # Connect to the provided base URL and check if backup_id is a valid number and exists
            backup_info = await api.get_backup(backup_id)
            if "Error" in backup_info:
                raise ApiResponseError(backup_info["Error"])
        except aiohttp.ClientConnectionError as e:
            raise CannotConnect(str(e)) from e
        else:
            return backup_info

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return DuplicatiOptionsFlowHandler(config_entry)
