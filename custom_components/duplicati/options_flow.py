"""Options flow for Duplicati integration."""

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
)
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    selector,
)

from .api import ApiProcessingError, DuplicatiBackendAPI
from .auth_interface import InvalidAuth
from .const import CONF_BACKUPS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .flow_base import BackupsError, DuplicatiFlowHandlerBase
from .http_client import CannotConnect
from .manager import DuplicatiEntityManager

_LOGGER = logging.getLogger(__name__)


class DuplicatiOptionsFlowHandler(OptionsFlow, DuplicatiFlowHandlerBase):
    """Options flow handler for the Duplicati integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    def __validate_input(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process user input and create new or update existing config entry."""
        # Validate backups
        backups = data.get(CONF_BACKUPS, [])
        if len(backups) == 0:
            raise BackupsError("No backups selected")
        # Validate scan interval
        scan_interval = data.get(CONF_SCAN_INTERVAL)
        if scan_interval is None or not isinstance(scan_interval, (float, int)):
            raise ValueError("Invalid or missing scan interval")
        return {CONF_BACKUPS: backups, CONF_SCAN_INTERVAL: scan_interval}

    async def __async_update_backups(self, selected_backups: dict[str, Any]) -> None:
        """Update the list of backups if it has changed."""
        # Remove unselected backups
        removed_backups = []
        for backup_id in self.config_entry.data[CONF_BACKUPS]:
            if backup_id not in selected_backups:
                removed = await self.entity_manager.remove_entities(backup_id)
                if removed:
                    removed_backups.append(backup_id)
        if len(removed_backups) > 0:
            _LOGGER.info("Removed backups %s from Home Assistant", removed_backups)
        # Add newly selelected backups
        added_backups = []
        for backup_id, backup_name in selected_backups.items():
            if backup_id not in self.config_entry.data[CONF_BACKUPS]:
                added = await self.entity_manager.add_entities(backup_id, backup_name)
                if added:
                    added_backups.append(backup_id)
        if len(added_backups) > 0:
            _LOGGER.info("Added backups %s to Home Assistant", added_backups)

    def __update_scan_interval(self, new_scan_interval: int) -> None:
        """Update the scan interval if it has changed."""
        current_scan_interval = int(self.config_entry.data[CONF_SCAN_INTERVAL])
        if new_scan_interval != current_scan_interval:
            for coordinator in self.hass.data[DOMAIN][self.config_entry.entry_id][
                "coordinators"
            ].values():
                coordinator.update_interval = timedelta(seconds=new_scan_interval)
            _LOGGER.info(
                "Updated scan interval for all coordinators to %s seconds",
                new_scan_interval,
            )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        try:
            errors: dict[str, str] = {}

            # Get currently configured values
            currently_configured_backups = self.config_entry.data.get(CONF_BACKUPS, {})
            currently_configured_scan_interval = self.config_entry.data.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            )
            # Extract currently configured backup IDs
            currently_configured_backup_ids = list(currently_configured_backups.keys())

            # Get backup entity manager
            self.entity_manager: DuplicatiEntityManager = self.hass.data[DOMAIN][
                self.config_entry.entry_id
            ]["entity_manager"]
            # Get backup API
            self.api: DuplicatiBackendAPI = self.hass.data[DOMAIN][
                self.config_entry.entry_id
            ]["api"]

            # Set currently configured backup as available backups (fallback in case of backup retrieval errors)
            available_backups = currently_configured_backups
            # Get available backup definitions
            response = await self.api.get_backups()
            backup_definitions = self._validate_backup_definitions(response)
            self.available_backup_definitions = backup_definitions
            # Get available backups
            available_backups = {
                backup_definition.backup.id: backup_definition.backup.name
                for backup_definition in self.available_backup_definitions
            }

        except CannotConnect as e:
            _LOGGER.error("Failed to connect: %s", str(e))
            errors["base"] = "cannot_connect"
        except InvalidAuth as e:
            _LOGGER.error("Authentication failed: %s", str(e))
            errors["base"] = "invalid_auth"
        except ApiProcessingError as e:
            _LOGGER.error("API response error: %s", str(e))
            errors["base"] = "api_response"
        except BackupsError as e:
            _LOGGER.error("Backups error: %s", str(e))
            errors["base"] = "no_backups"
        except Exception:
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        finally:
            currently_configured_backup_select_options_list = (
                self._get_backup_select_options_list(available_backups)
            )

        # Process user input if provided
        if user_input is not None:
            try:
                # Validate input
                config_input = self.__validate_input(user_input)

                # Get selected backups
                selected_backups = {
                    backup_definition.backup.id: backup_definition.backup.name
                    for backup_definition in self.available_backup_definitions
                    if backup_definition.backup.id in config_input[CONF_BACKUPS]
                }
                # Update backups
                await self.__async_update_backups(selected_backups)
                # Update scan interval
                self.__update_scan_interval(config_input[CONF_SCAN_INTERVAL])

                # Set new entry data
                data = self.config_entry.data.copy()
                data[CONF_BACKUPS] = selected_backups
                data[CONF_SCAN_INTERVAL] = config_input[CONF_SCAN_INTERVAL]
                # Update entry
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data, options=self.config_entry.options
                )
                # Create entry
                return self.async_create_entry(title=None, data={})
            except ValueError as e:
                _LOGGER.error("Invalid input: %s", str(e))
                errors["base"] = "scan_interval"
            except BackupsError as e:
                _LOGGER.error("Invalid input: %s", str(e))
                errors["base"] = "backup_selection"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            finally:
                currently_configured_backup_ids = user_input[CONF_BACKUPS]
                currently_configured_scan_interval = user_input[CONF_SCAN_INTERVAL]
        # Define data schema
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BACKUPS,
                    description={"suggested_value": currently_configured_backup_ids},
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=currently_configured_backup_select_options_list,
                        translation_key=CONF_BACKUPS,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    description={"suggested_value": currently_configured_scan_interval},
                ): selector(
                    {
                        "number": {
                            "mode": "box",
                            "min": 1,
                            "max": 86400,
                            "step": 1,
                        }
                    }
                ),
            },
            extra=vol.ALLOW_EXTRA,
        )
        # Show form
        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )
