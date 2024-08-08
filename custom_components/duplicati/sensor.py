"""Definition for Duplicati backup software sensors."""

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    METRIC_DURATION,
    METRIC_ERROR_MESSAGE,
    METRIC_EXECUTION,
    METRIC_SOURCE_FILES,
    METRIC_SOURCE_SIZE,
    METRIC_STATUS,
    METRIC_TARGET_FILES,
    METRIC_TARGET_SIZE,
    STATUS_ERROR,
    STATUS_OK,
)

SENSORS = {
    METRIC_STATUS: SensorEntityDescription(
        key=METRIC_STATUS,
        icon="mdi:shield-check",
        device_class=SensorDeviceClass.ENUM,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=None,
        options=[STATUS_OK, STATUS_ERROR],
        translation_key=METRIC_STATUS,
    ),
    METRIC_EXECUTION: SensorEntityDescription(
        key=METRIC_EXECUTION,
        icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        state_class=SensorStateClass.MEASUREMENT,
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
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_info = hass.data[DOMAIN][entry.entry_id]["device_info"]
    # Create sensors
    sensors = [
        DuplicatiSensor(coordinator, description, device_info)
        for _, description in SENSORS.items()
    ]
    # Add sensors to hass
    async_add_entities(sensors)


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
