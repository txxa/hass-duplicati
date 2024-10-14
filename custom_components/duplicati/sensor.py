"""Definition for Duplicati backup software sensors."""

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    METRIC_DURATION,
    METRIC_ERROR_MESSAGE,
    METRIC_EXECUTION,
    METRIC_SOURCE_FILES,
    METRIC_SOURCE_SIZE,
    METRIC_TARGET_FILES,
    METRIC_TARGET_SIZE,
    MODEL,
)

SENSORS = {
    METRIC_EXECUTION: SensorEntityDescription(
        key=METRIC_EXECUTION,
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        state_class=None,
        native_unit_of_measurement=None,
        translation_key=METRIC_EXECUTION,
    ),
    METRIC_DURATION: SensorEntityDescription(
        key=METRIC_DURATION,
        icon="mdi:timer-outline",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=1,
        translation_key=METRIC_DURATION,
    ),
    METRIC_SOURCE_FILES: SensorEntityDescription(
        key=METRIC_SOURCE_FILES,
        icon="mdi:file-multiple",
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=None,
        translation_key=METRIC_SOURCE_FILES,
    ),
    METRIC_SOURCE_SIZE: SensorEntityDescription(
        key=METRIC_SOURCE_SIZE,
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=2,
        translation_key=METRIC_SOURCE_SIZE,
    ),
    METRIC_TARGET_SIZE: SensorEntityDescription(
        key=METRIC_TARGET_SIZE,
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=2,
        translation_key=METRIC_TARGET_SIZE,
    ),
    METRIC_TARGET_FILES: SensorEntityDescription(
        key=METRIC_TARGET_FILES,
        icon="mdi:file-multiple",
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=None,
        translation_key=METRIC_TARGET_FILES,
    ),
    METRIC_ERROR_MESSAGE: SensorEntityDescription(
        key=METRIC_ERROR_MESSAGE,
        icon="mdi:alert-circle-outline",
        device_class=None,
        state_class=None,
        native_unit_of_measurement=None,
        translation_key=METRIC_ERROR_MESSAGE,
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
        name=backup["name"],
        model=MODEL,
        manufacturer=MANUFACTURER,
        configuration_url=url,
        sw_version=version_info.get("server_version"),
        serial_number=unique_id,
        identifiers={(DOMAIN, unique_id)},
        entry_type=DeviceEntryType.SERVICE,
    )

    for description in SENSORS.values():
        sensor = DuplicatiSensor(coordinator, description, device_info)
        sensors.append(sensor)
    return sensors


class DuplicatiSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Duplicati sensor."""

    _attr_has_entity_name = True
    entity_description: SensorEntityDescription
    device_info: DeviceInfo

    def __init__(
        self,
        coordinator,
        description: SensorEntityDescription,
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
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.entity_description.key)
        if value is None:
            return None
        return value
