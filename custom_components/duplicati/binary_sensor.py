"""Module for Duplicati binary sensors."""

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    METRIC_LAST_STATUS,
    MODEL,
)

BINARY_SENSORS = {
    METRIC_LAST_STATUS: BinarySensorEntityDescription(
        key=METRIC_LAST_STATUS,
        icon="mdi:shield-check",
        device_class=BinarySensorDeviceClass.PROBLEM,
        translation_key=METRIC_LAST_STATUS,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Duplicati sensors based on a config entry."""
    backups: dict[str, str] = hass.data[DOMAIN][entry.entry_id]["backups"]
    coordinators = hass.data[DOMAIN][entry.entry_id]["coordinators"]
    for backup_id, backup_name in backups.items():
        coordinator = coordinators[backup_id]
        backup = {"id": backup_id, "name": backup_name}
        sensors = create_backup_sensors(hass, entry, backup, coordinator)
        # Add sensors to hass
        async_add_entities(sensors)


def create_backup_sensors(
    hass: HomeAssistant, entry: ConfigEntry, backup, coordinator
) -> list[Any]:
    """Create sensor entities for the given resource."""
    sensors = []
    host = hass.data[DOMAIN][entry.entry_id]["host"]
    version_info = hass.data[DOMAIN][entry.entry_id]["version_info"]
    url = entry.data[CONF_URL]
    unique_id = f"{host}/{backup["id"]}"

    device_info = DeviceInfo(
        name=f"{backup["name"]} Backup",
        model=MODEL,
        manufacturer=MANUFACTURER,
        configuration_url=url,
        sw_version=version_info.get("server_version"),
        serial_number=unique_id,
        identifiers={(DOMAIN, unique_id)},
        entry_type=DeviceEntryType.SERVICE,
    )

    for description in BINARY_SENSORS.values():
        sensor = DuplicatiBinarySensor(coordinator, description, device_info)
        sensors.append(sensor)
    return sensors


class DuplicatiBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Duplicati binary sensor."""

    _attr_has_entity_name = True
    entity_description: BinarySensorEntityDescription
    device_info: DeviceInfo

    def __init__(
        self,
        coordinator,
        description: BinarySensorEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self.device_info = device_info
        self._attr_unique_id = f"{device_info.get('serial_number')}-{description.key}"

    @property
    def translation_key(self) -> str | None:
        """Return the translation key to translate the entity's name and states."""
        return self.entity_description.translation_key

    @property
    def unique_id(self) -> str | None:
        """Return the unique ID."""
        return self._attr_unique_id

    @property
    def is_on(self) -> bool | None:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.entity_description.key)
        if value is None:
            return None
        return value
