"""Definition for Duplicati backup software buttons."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
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
    device_info = hass.data[DOMAIN][entry.entry_id]["device_info"]
    backup_id = hass.data[DOMAIN][entry.entry_id]["backup_id"]
    host = hass.data[DOMAIN][entry.entry_id]["host"]
    service = hass.data[DOMAIN][host]["service"]

    async_add_entities(
        DuplicatiButton(service, button, device_info, backup_id) for button in BUTTONS
    )


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
