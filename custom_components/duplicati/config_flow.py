"""Config flow for Duplicati integration."""

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
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import ApiResponseError, DuplicatiBackendAPI
from .auth_interface import InvalidAuth
from .auth_strategies import JWTAuthStrategy
from .const import CONF_BACKUPS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .flow_base import BackupsError, DuplicatiFlowHandlerBase
from .http_client import CannotConnect, HttpClient
from .options_flow import DuplicatiOptionsFlowHandler

_LOGGER = logging.getLogger(__name__)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_VERIFY_SSL, default=vol.Coerce(bool)(False)): bool,
    }
)


class DuplicatiConfigFlowHandler(ConfigFlow, DuplicatiFlowHandlerBase, domain=DOMAIN):
    """Handle the config flow for Duplicati."""

    VERSION = 3
    title: str
    data: dict[str, Any]

    def __create_api(
        self,
        url: str,
        verify_ssl: bool,
        password: str,
    ) -> DuplicatiBackendAPI:
        """Create an instance of DuplicatiBackendAPI."""

        # Create http client
        http_client = HttpClient(verify_ssl)
        # Create auth strategy
        auth_strategy = JWTAuthStrategy(url, verify_ssl, http_client=http_client)
        # Create API instance
        return DuplicatiBackendAPI(
            url, verify_ssl, password, auth_strategy, http_client
        )

    async def __async_validate_user_step_input(self, data: dict[str, Any]) -> tuple:
        """Process user input and create new or update existing config entry."""
        try:
            base_url = data[CONF_URL]
            password = data[CONF_PASSWORD]
            verify_ssl = data[CONF_VERIFY_SSL]
            # Create API instance
            self.api = self.__create_api(base_url, verify_ssl, password)
            # Get the list of available backups
            backups = await self.api.get_backups()
            # Check if backups are available
            self._validate_backup_definitions(backups)
            # Define scan interval
            data[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
        except aiohttp.ClientConnectionError as e:
            raise CannotConnect(str(e)) from e
        except aiohttp.ClientError as e:
            raise CannotConnect(str(e)) from e
        else:
            return (data, backups)

    def __validate_backups_step_input(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate user input."""
        # Validate backups
        backups = data[CONF_BACKUPS]
        if len(backups) == 0:
            raise BackupsError("No backups selected")
        return {
            CONF_BACKUPS: backups,
        }

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
                    if entry.data[CONF_URL] == user_input[CONF_URL]:
                        return self.async_abort(reason="already_configured")
                # Validate input
                (
                    self.data,
                    self.available_backup_definitions,
                ) = await self.__async_validate_user_step_input(user_input)
                # Define entry title
                host = urllib.parse.urlparse(user_input[CONF_URL]).netloc
                self.title = host
                # Show backups form
                return await self.async_step_backups()
            except CannotConnect as e:
                _LOGGER.error("Failed to connect: %s", str(e))
                errors["base"] = "cannot_connect"
            except InvalidAuth as e:
                _LOGGER.error("Authentication failed: %s", str(e))
                errors["base"] = "invalid_auth"
            except ApiResponseError as e:
                _LOGGER.error("API response error: %s", str(e))
                errors["base"] = "api_response"
            except BackupsError as e:
                _LOGGER.error("Backups error: %s", str(e))
                errors["base"] = "no_backups"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
        # Show form
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            last_step=False,
        )

    async def async_step_backups(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the backups step."""
        errors: dict[str, str] = {}

        # Get available backups
        available_backups = {
            backup_definition.backup.id: backup_definition.backup.name
            for backup_definition in self.available_backup_definitions
        }
        # Set default selection
        default_selection = list(available_backups.keys())

        # Process user input if provided
        if user_input is not None:
            try:
                # Validate input
                config_input = self.__validate_backups_step_input(user_input)

                # Get selected backups
                selected_backups = {
                    backup_definition.backup.id: backup_definition.backup.name
                    for backup_definition in self.available_backup_definitions
                    if backup_definition.backup.id in config_input[CONF_BACKUPS]
                }

                # Set new entry data
                self.data[CONF_BACKUPS] = selected_backups
                # Create entry
                return self.async_create_entry(title=self.title, data=self.data)
            except BackupsError as e:
                _LOGGER.error("Invalid input: %s", str(e))
                errors["base"] = "backup_selection"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            finally:
                default_selection = user_input.get(CONF_BACKUPS, [])
        # Define data schema
        available_backup_select_options_list = self._get_backup_select_options_list(
            available_backups
        )
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BACKUPS,
                    description={"suggested_value": default_selection},
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=available_backup_select_options_list,
                        translation_key=CONF_BACKUPS,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            },
            extra=vol.ALLOW_EXTRA,
        )
        # Show form
        return self.async_show_form(
            step_id="backups", data_schema=data_schema, errors=errors, last_step=True
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return DuplicatiOptionsFlowHandler(config_entry)
