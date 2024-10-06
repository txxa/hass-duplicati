"""Options flow for Duplicati integration."""

import logging
from datetime import timedelta
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    selector,
)

from custom_components.duplicati.coordinator import DuplicatiDataUpdateCoordinator

from .api import ApiResponseError, CannotConnect, DuplicatiBackendAPI, InvalidAuth
from .button import create_backup_buttons
from .const import CONF_BACKUPS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .sensor import create_backup_sensors

_LOGGER = logging.getLogger(__name__)


class DuplicatiOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler for the Duplicati integration."""

    backups: dict

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.api = self._create_api()

    def _create_api(self) -> DuplicatiBackendAPI:
        """Create an instance of DuplicatiBackendAPI."""
        base_url = self.config_entry.data[CONF_URL]
        password = self.config_entry.data.get(CONF_PASSWORD)
        verify_ssl = self.config_entry.data[CONF_VERIFY_SSL]
        # Create an instance of DuplicatiBackendAPI
        return DuplicatiBackendAPI(base_url, verify_ssl, password)

    def _get_platform(self, type: str) -> EntityPlatform:
        platforms = self.hass.data["entity_platform"][DOMAIN]
        for platform in platforms:
            if (
                platform.config_entry.entry_id == self.config_entry.entry_id
                and platform.domain == type
            ):
                return platform
        _LOGGER.error(
            "No platform found for config entry %s",
            self.config_entry.entry_id,
        )
        raise HomeAssistantError(
            "No platform found for config entry %s",
            self.config_entry.entry_id,
        )

    def _get_available_backups(self) -> dict[str, str]:
        """Return a dictionary of available backup names."""
        backups = {}
        for backup in self.backups:
            backup_id = backup["Backup"]["ID"]
            backup_name = backup["Backup"]["Name"]
            backups[backup_id] = backup_name
        return backups

    def _get_backup_select_options_list(
        self, backups: dict[str, str]
    ) -> list[SelectOptionDict]:
        """Return a dictionary of available backup names."""
        return [
            SelectOptionDict(
                label=value,
                value=key,
            )
            for key, value in backups.items()
        ]

    def _get_integration_device_entries(self) -> list[DeviceEntry]:
        """Get device entries for the config entry."""
        device_entries = []
        device_registry = self.hass.data[dr.DATA_REGISTRY]
        for device_entry in device_registry.devices.data.values():
            for config_entry in device_entry.config_entries:
                if config_entry == self.config_entry.entry_id:
                    device_entries.append(device_entry)
                    break
        if len(device_entries) == 0:
            _LOGGER.error(
                "No devices found for config entry %s",
                self.config_entry.entry_id,
            )
        return device_entries

    def _get_backup_id_from_serial_number(
        self, serial_number: str | None
    ) -> str | None:
        """Get backup ID from serial number."""
        if not isinstance(serial_number, str):
            return None
        if "/" in serial_number:
            return serial_number.split("/", 1)[1]
        return None

    async def _async_get_backups(self) -> dict:
        """Get available backups."""
        try:
            response = await self.api.list_backups()
            if "Error" in response:
                raise ApiResponseError(response["Error"])
            # Check if backups are available
            if len(response) == 0:
                raise BackupsError(
                    f"No backups found for server '{self.api.get_api_host()}'"
                )
        except aiohttp.ClientConnectionError as e:
            raise CannotConnect(str(e)) from e
        return response

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        try:
            errors: dict[str, str] = {}
            configured_backups = list(
                self.config_entry.data.get(CONF_BACKUPS, {}).keys()
            )
            configured_scan_interval = self.config_entry.data.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            )
            available_backups = self.config_entry.data.get(CONF_BACKUPS, {})
            self.backups = await self._async_get_backups()
            available_backups = self._get_available_backups()
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
        finally:
            available_backups_list = self._get_backup_select_options_list(
                available_backups
            )

        # Process user input if provided
        if user_input is not None:
            try:
                # Validate input
                config_input = self._validate_input(user_input)
                # Get platforms
                self.sensor_platform = self._get_platform(Platform.SENSOR)
                self.button_platform = self._get_platform(Platform.BUTTON)
                # Update backups
                await self._async_update_backups(config_input[CONF_BACKUPS])
                # Update scan interval
                self._update_scan_interval(config_input[CONF_SCAN_INTERVAL])
                # Set entry data
                backups = {}
                for backup_id, backup_name in available_backups.items():
                    if backup_id in config_input[CONF_BACKUPS]:
                        if backup_id not in backups:
                            backups[backup_id] = backup_name
                data = self.config_entry.data.copy()
                data[CONF_BACKUPS] = backups
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
                configured_backups = user_input[CONF_BACKUPS]
                configured_scan_interval = user_input[CONF_SCAN_INTERVAL]
        # Define data schema
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BACKUPS,
                    description={"suggested_value": configured_backups},
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=available_backups_list,
                        translation_key=CONF_BACKUPS,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    description={"suggested_value": configured_scan_interval},
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
        # Validate backups
        backups = data.get(CONF_BACKUPS, [])
        if len(backups) == 0:
            raise BackupsError("No backups selected")
        # Validate scan interval
        scan_interval = data.get(CONF_SCAN_INTERVAL)
        if scan_interval is None or not isinstance(scan_interval, (float, int)):
            raise ValueError("Invalid or missing scan interval")
        return {CONF_BACKUPS: backups, CONF_SCAN_INTERVAL: scan_interval}

    async def _async_update_backups(self, backups: list[str]) -> None:
        """Update the list of backups if it has changed."""
        # Remove unselected backups
        removed_backups = []
        device_entries = self._get_integration_device_entries()
        for device in device_entries:
            # backup_id = self._get_backup_id_from_serial_number(device.serial_number)
            for backup in self.config_entry.data[CONF_BACKUPS]:
                # if backup == backup_id:
                #     break
                if backup not in backups:
                    removed = await self._async_remove_backup_from_hass(device, backup)
                    if removed:
                        removed_backups.append(backup)
            if removed_backups:
                _LOGGER.info(
                    "Removed resources %s from Home Assistant", removed_backups
                )
        # Add newly selelected backups
        added_backups = []
        for backup in backups:
            if backup not in self.config_entry.data[CONF_BACKUPS]:
                added = await self._async_add_backup_to_hass(backup)
                if added:
                    added_backups.append(backup)
        if added_backups:
            _LOGGER.info("Added backups %s to Home Assistant", added_backups)

    def _update_scan_interval(self, new_scan_interval: int) -> None:
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

    async def _async_remove_backup_from_hass(
        self, device: DeviceEntry, backup_id: str
    ) -> bool:
        """Remove a backup from Home Assistant."""
        device_registry = self.hass.data[dr.DATA_REGISTRY]
        # for device in device_registry.devices.data.values():
        for config_entry in device.config_entries:
            if (
                config_entry == self.config_entry.entry_id
                and self._get_backup_id_from_serial_number(device.serial_number)
                == backup_id
            ):
                if (
                    backup_id
                    in self.hass.data[DOMAIN][self.config_entry.entry_id][
                        "coordinators"
                    ]
                ):
                    # Unregister coordinator
                    coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id][
                        "coordinators"
                    ][backup_id]
                    host = self.hass.data[DOMAIN][self.config_entry.entry_id]["host"]
                    service = self.hass.data[DOMAIN][host]["service"]
                    service.unregister_coordinator(coordinator)
                    # Remove coordinator
                    self.hass.data[DOMAIN][self.config_entry.entry_id][
                        "coordinators"
                    ].pop(backup_id)
                else:
                    _LOGGER.debug("Coordinator for resource %s not found", backup_id)
                # Remove device including its entities
                device_registry.async_remove_device(device.id)
                _LOGGER.debug("Removed device registry entry: %s.%s", DOMAIN, backup_id)
                return True
        return False

    async def _async_add_backup_to_hass(self, backup_id: str) -> bool:
        """Add a backup to Home Assistant."""
        device_registry = self.hass.data[dr.DATA_REGISTRY]
        for backup in self.backups:
            b_id = backup["Backup"]["ID"]
            b_name = backup["Backup"]["Name"]
            if b_id == backup_id:
                # Create coordinator
                coordinator = DuplicatiDataUpdateCoordinator(
                    self.hass,
                    api=self.api,
                    backup_id=backup_id,
                    update_interval=int(self.config_entry.data[CONF_SCAN_INTERVAL]),
                )
                # Create sensors
                sensors = create_backup_sensors(
                    self.hass,
                    self.config_entry,
                    {"id": b_id, "name": b_name},
                    coordinator,
                )
                # Create buttons
                buttons = create_backup_buttons(
                    self.hass, self.config_entry, {"id": b_id, "name": b_name}
                )
                # Register device
                device_entry = device_registry.async_get_or_create(
                    config_entry_id=self.config_entry.entry_id,
                    name=sensors[0].device_info["name"],
                    model=sensors[0].device_info["model"],
                    manufacturer=sensors[0].device_info["manufacturer"],
                    sw_version=sensors[0].device_info["sw_version"],
                    identifiers=sensors[0].device_info["identifiers"],
                    entry_type=sensors[0].device_info["entry_type"],
                )
                # Link sensors to device
                for sensor in sensors:
                    sensor.device_entry = device_entry
                # Link buttons to device
                for button in buttons:
                    button.device_entry = device_entry
                # Add sensors to hass
                await self.sensor_platform.async_add_entities(sensors)
                # Add buttons to hass
                await self.button_platform.async_add_entities(buttons)
                # Add coordinator to config entry
                self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinators"][
                    b_id
                ] = coordinator
                # Register coordinator
                host = self.hass.data[DOMAIN][self.config_entry.entry_id]["host"]
                service = self.hass.data[DOMAIN][host]["service"]
                service.register_coordinator(coordinator)
                # Add backup to config entry
                self.hass.data[DOMAIN][self.config_entry.entry_id]["backups"][b_id] = (
                    b_name
                )
                return True
        return False


class BackupsError(HomeAssistantError):
    """Error to indicate there is an error with backups."""
