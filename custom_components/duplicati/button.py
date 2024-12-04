"""Definition for Duplicati backup software buttons."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL
from .service import DuplicatiService

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DuplicatiButtonDescriptionMixin:
    """Mixin to describe a Duplicati button entity."""

    press_action: Callable[
        [DuplicatiService, str], Callable[[], Coroutine[Any, Any, None]]
    ]


@dataclass(frozen=True)
class DuplicatiButtonDescription(
    ButtonEntityDescription, DuplicatiButtonDescriptionMixin
):
    """Class to describe a Duplicati button entity."""


BUTTONS: Final = [
    DuplicatiButtonDescription(
        key="create_backup",
        translation_key="create_backup",
        entity_category=EntityCategory.CONFIG,
        press_action=lambda service, backup_id: lambda: service.async_create_backup(
            backup_id
        ),
    ),
    DuplicatiButtonDescription(
        key="refresh_sensor_data",
        translation_key="refresh_sensor_data",
        entity_category=EntityCategory.CONFIG,
        press_action=lambda service,
        backup_id: lambda: service.async_refresh_sensor_data(backup_id),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set buttons for Duplicati integration."""
    backups: dict[str, str] = hass.data[DOMAIN][entry.entry_id]["backups"]
    for backup_id, backup_name in backups.items():
        backup = {"id": backup_id, "name": backup_name}
        buttons = create_buttons(hass, entry, backup)
        # Add buttons to hass
        async_add_entities(buttons)


def create_buttons(hass: HomeAssistant, entry: ConfigEntry, backup) -> list[Any]:
    """Create sensor entities for the given resource."""
    buttons = []
    host = hass.data[DOMAIN][entry.entry_id]["host"]
    service = hass.data[DOMAIN][host]["service"]
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

    for description in BUTTONS:
        sensor = DuplicatiButton(service, description, device_info, backup["id"])
        buttons.append(sensor)
    return buttons


class DuplicatiButton(ButtonEntity):
    """Defines a Duplicati button."""

    _attr_has_entity_name = True
    entity_description: DuplicatiButtonDescription
    device_info: DeviceInfo

    def __init__(
        self,
        service: DuplicatiService,
        description: DuplicatiButtonDescription,
        device_info: DeviceInfo,
        backup_id: str,
    ) -> None:
        """Initialize the Duplicati button."""
        self.entity_description = description
        self.device_info = device_info
        self.service = service
        self.backup_id = backup_id
        self._attr_unique_id = f"{device_info.get('serial_number')}-{description.key}"
        self._is_enabled = True  # Initial state is enabled

    @property
    def translation_key(self) -> str | None:
        """Return the translation key to translate the entity's name and states."""
        return self.entity_description.translation_key

    @property
    def unique_id(self) -> str | None:
        """Return the unique ID."""
        return self._attr_unique_id

    @property
    def is_enabled(self):
        """Return whether the button is enabled."""
        return self._is_enabled

    async def async_press(self) -> None:
        """Triggers the Duplicati button press service."""
        # Execute the associated function with the backup_id parameter
        LOGGER.debug("Trigger %s for Duplicati", self.entity_description.key)
        await self._call_press_action()

    async def _call_press_action(self):
        """Call the press action function with the backup_id."""
        press_action = self.entity_description.press_action(
            self.service, self.backup_id
        )
        await press_action()
