"""The Duplicati integration."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ID,
    CONF_PASSWORD,
    CONF_URL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .api import DuplicatiBackendAPI
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MANUFACTURER, MODEL
from .coordinator import DuplicatiDataUpdateCoordinator
from .service import DuplicatiService, async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Duplicati from a config entry."""
    try:
        hass.data.setdefault(DOMAIN, {})
        # Extract entry data
        base_url = entry.data[CONF_URL]
        backup_id = entry.data[CONF_ID]
        verify_ssl = entry.data[CONF_VERIFY_SSL]
        password = entry.data.get(CONF_PASSWORD)  # Duplicati UI PW is not yet supported
        # Create an instance of DuplicatiBackendAPI
        api = DuplicatiBackendAPI(base_url, verify_ssl, password)
        # Validate the API connection (and authentication)
        backup_resp = await api.get_backup(backup_id)
        if "data" in backup_resp:
            backup_name = backup_resp["data"]["Backup"]["Name"]
            device_name = f"{backup_name} Backup"
        else:
            reason = backup_resp.get("Error", "Unknown")
            _LOGGER.error(
                "Getting the information of backup with ID '%s' failed: %s",
                backup_id,
                reason,
            )
            return False
        sysinfo_resp = await api.get_system_info()
        if "ServerVersion" in sysinfo_resp:
            version = sysinfo_resp.get("ServerVersion", "Unknown")
        if "APIVersion" in sysinfo_resp:
            api_version = sysinfo_resp.get("APIVersion")
            if api_version is not None:
                version = f"{version} (API v{api_version})"
        # Create domain data if it does not exist
        if DOMAIN not in hass.data:
            hass.data[DOMAIN] = {}
        # Create a data coordinator for managing data updates
        coordinator = DuplicatiDataUpdateCoordinator(
            hass,
            api=api,
            backup_id=backup_id,
            update_interval=int(DEFAULT_SCAN_INTERVAL),
        )
        # Define unique ID
        host = api.get_api_host()
        unique_id = f"{host}/{backup_id}"
        # Create a service for managing Duplicati operations
        if host not in hass.data[DOMAIN]:
            hass.data[DOMAIN][host] = {}
            service = DuplicatiService(hass, api)
            service.register_coordinator(coordinator)
            hass.data[DOMAIN][host] = {"service": service}
        # Create device information
        device_info = DeviceInfo(
            name=device_name,
            model=MODEL,
            manufacturer=MANUFACTURER,
            configuration_url=base_url,
            sw_version=version,
            serial_number=unique_id,
            identifiers={(DOMAIN, unique_id)},
            entry_type=DeviceEntryType.SERVICE,
        )
        # Store required entry data in hass domain entry object
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "device_info": device_info,
            "host": host,
            "backup_id": backup_id,
        }
        # Forward the setup to your platforms, passing the coordinator to them
        for platform in PLATFORMS:
            hass.async_create_task(
                hass.config_entries.async_forward_entry_setup(entry, platform)
            )
        # Set up custom services
        await async_setup_services(hass)
    except aiohttp.ClientConnectionError as e:
        # Handle authentication or connection errors here
        _LOGGER.error("Failed to connect: %s", str(e))
        return False
    else:
        # Initial sensor data refresh
        await coordinator.async_refresh()
        return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Get the service
        host = hass.data[DOMAIN][entry.entry_id]["host"]
        service = hass.data[DOMAIN][host]["service"]
        # Remove the coordinator
        service.unregister_coordinator(hass.data[DOMAIN][entry.entry_id]["coordinator"])
        # Remove the service
        if service.get_number_of_coordinators() == 0:
            await async_unload_services(hass)
            hass.data[DOMAIN].pop(host)
        # Remove the entry data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
