"""Options flow for Duplicati integration."""

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import selector

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class DuplicatiOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for the Duplicati integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        configured_scan_interval = self.config_entry.data.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        # Process user input if provided
        if user_input is not None:
            try:
                # Validate input
                config_input = self._validate_input(user_input)
                # Update scan interval
                self._update_scan_interval(config_input[CONF_SCAN_INTERVAL])
                # Set entry data
                data = self.config_entry.data.copy()
                data[CONF_SCAN_INTERVAL] = config_input[CONF_SCAN_INTERVAL]
                # Update entry
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data, options=self.config_entry.options
                )
                # Create entry
                return self.async_create_entry(title=None, data={})
            except ValueError as e:
                _LOGGER.error("Invalid input: %s", str(e))
                errors["base"] = "invalid_input"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            finally:
                configured_scan_interval = user_input[CONF_SCAN_INTERVAL]
        # Define data schema
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=vol.Coerce(int)(configured_scan_interval),
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

    def _validate_input(self, data: dict[str, Any]) -> dict[str, Any]:
        """Process user input and create new or update existing config entry."""
        scan_interval = data.get(CONF_SCAN_INTERVAL)
        if scan_interval is None or not isinstance(scan_interval, (float, int)):
            raise ValueError("Invalid or missing scan interval")
        return {CONF_SCAN_INTERVAL: scan_interval}

    def _update_scan_interval(self, new_scan_interval: int) -> None:
        """Update the scan interval if it has changed."""
        current_scan_interval = int(self.config_entry.data[CONF_SCAN_INTERVAL])
        if new_scan_interval != current_scan_interval:
            coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id][
                "coordinator"
            ]
            coordinator.update_interval = timedelta(seconds=new_scan_interval)
            _LOGGER.info("Updated update interval to %s seconds", new_scan_interval)
