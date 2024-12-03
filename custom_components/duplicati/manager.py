"""Backup management for Duplicati integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.entity_platform import EntityPlatform

from .api import DuplicatiBackendAPI
from .binary_sensor import create_binary_sensors
from .button import create_buttons
from .const import DOMAIN
from .coordinator import DuplicatiDataUpdateCoordinator
from .sensor import create_sensors
from .service import DuplicatiService

_LOGGER = logging.getLogger(__name__)


class DuplicatiEntityManager:
    """Manages backup operations for Duplicati integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: DuplicatiBackendAPI,
    ) -> None:
        """Initialize backup manager."""
        self.hass = hass
        self.config_entry = config_entry
        self.__api = api
        self.__device_registry = self.hass.data[dr.DATA_REGISTRY]

    def __get_backup_id_from_serial_number(
        self, serial_number: str | None
    ) -> str | None:
        """Get backup ID from serial number."""
        if not isinstance(serial_number, str):
            return None
        if "/" in serial_number:
            return serial_number.split("/", 1)[1]
        return None

    def __get_platform(self, platform_type: str) -> EntityPlatform:
        """Get platform for given type."""
        platforms = self.hass.data["entity_platform"][DOMAIN]
        for platform in platforms:
            if (
                platform.config_entry.entry_id == self.config_entry.entry_id
                and platform.domain == platform_type
            ):
                return platform
        raise ValueError(
            f"No platform found for config entry {self.config_entry.entry_id}"
        )

    def __get_integration_device_entries(self) -> list[DeviceEntry]:
        """Get device entries for the config entry."""
        device_entries = []
        for device_entry in self.__device_registry.devices.data.values():
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

    def __register_coordinator(
        self, backup_id: str, coordinator: DuplicatiDataUpdateCoordinator
    ) -> None:
        """Register coordinator for backup."""
        self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinators"][
            backup_id
        ] = coordinator
        host = self.hass.data[DOMAIN][self.config_entry.entry_id]["host"]
        service = self.hass.data[DOMAIN][host]["service"]
        service.register_coordinator(coordinator)

    def __unregister_coordinator(self, backup_id: str) -> None:
        """Unregister coordinator for backup."""
        if (
            backup_id
            in self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinators"]
        ):
            coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id][
                "coordinators"
            ][backup_id]
            host = self.hass.data[DOMAIN][self.config_entry.entry_id]["host"]
            service: DuplicatiService = self.hass.data[DOMAIN][host]["service"]
            service.unregister_coordinator(coordinator)
            self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinators"].pop(
                backup_id
            )

    async def add_entities(self, backup_id: str, backup_name: str) -> bool:
        """Add a backup to Home Assistant."""
        try:
            # Create coordinator
            coordinator = DuplicatiDataUpdateCoordinator(
                self.hass,
                api=self.__api,
                backup_id=backup_id,
                update_interval=int(self.config_entry.data[CONF_SCAN_INTERVAL]),
            )

            # Create entities
            sensors = create_sensors(
                self.hass,
                self.config_entry,
                {
                    "id": backup_id,
                    "name": backup_name,
                },
                coordinator,
            )
            binary_sensors = create_binary_sensors(
                self.hass,
                self.config_entry,
                {
                    "id": backup_id,
                    "name": backup_name,
                },
                coordinator,
            )
            buttons = create_buttons(
                self.hass,
                self.config_entry,
                {
                    "id": backup_id,
                    "name": backup_name,
                },
            )

            # Register device
            device_entry = self.__device_registry.async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                name=sensors[0].device_info["name"],
                model=sensors[0].device_info["model"],
                manufacturer=sensors[0].device_info["manufacturer"],
                sw_version=sensors[0].device_info["sw_version"],
                identifiers=sensors[0].device_info["identifiers"],
                entry_type=sensors[0].device_info["entry_type"],
            )

            # Link entities to device
            for entity in [*sensors, *binary_sensors, *buttons]:
                entity.device_entry = device_entry

            # Add entities to platforms
            await self.__get_platform(Platform.SENSOR).async_add_entities(sensors)
            await self.__get_platform(Platform.BINARY_SENSOR).async_add_entities(
                binary_sensors
            )
            await self.__get_platform(Platform.BUTTON).async_add_entities(buttons)

            # Register coordinator
            self.__register_coordinator(backup_id, coordinator)

            # Refresh sensor data
            await coordinator.async_refresh()

        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to add backup %s: %s", backup_id, str(err))
            return False
        else:
            return True

    async def remove_entities(self, backup_id: str) -> bool:
        """Remove a backup from Home Assistant."""
        try:
            device_entries = self.__get_integration_device_entries()
            for device in device_entries:
                for config_entry in device.config_entries:
                    if (
                        config_entry == self.config_entry.entry_id
                        and self.__get_backup_id_from_serial_number(
                            device.serial_number
                        )
                        == backup_id
                    ):
                        self.__unregister_coordinator(backup_id)
                        self.__device_registry.async_remove_device(device.id)
                        _LOGGER.debug("Removed device: %s.%s", DOMAIN, backup_id)
                        return True
            return False  # noqa: TRY300

        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to remove backup %s: %s", backup_id, str(err))
            return False
