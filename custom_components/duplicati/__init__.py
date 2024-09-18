"""The Duplicati integration."""

from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant

from .api import DuplicatiBackendAPI
from .const import CONF_BACKUPS, DEFAULT_SCAN_INTERVAL, DOMAIN
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
        password = entry.data.get(CONF_PASSWORD)  # Duplicati UI PW is not yet supported
        verify_ssl = entry.data[CONF_VERIFY_SSL]
        # Create an instance of DuplicatiBackendAPI
        api = DuplicatiBackendAPI(base_url, verify_ssl, password)
        # Get backups and create a coordinator for each backup
        backups = entry.data.get(CONF_BACKUPS, {})
        coordinators = {}
        for backup_id in backups:
            coordinator = DuplicatiDataUpdateCoordinator(
                hass,
                api=api,
                backup_id=backup_id,
                update_interval=int(DEFAULT_SCAN_INTERVAL),
            )
            coordinators[backup_id] = coordinator
        if len(backups) == 0:
            _LOGGER.error("No backups found in the Duplicati server.")
            return False
        # Get version info
        sysinfo_resp = await api.get_system_info()
        server_version = sysinfo_resp.get("ServerVersion", "Unknown")
        api_version = sysinfo_resp.get("APIVersion", "Unknown")
        version_info = {
            "server": server_version,
            "api": api_version,
        }
        # Get the host name from the API
        host = api.get_api_host()
        # Create a service for managing Duplicati operations
        if host not in hass.data[DOMAIN]:
            hass.data[DOMAIN][host] = {}
            service = DuplicatiService(hass, api)
            service.register_coordinator(coordinator)
            hass.data[DOMAIN][host] = {"service": service}
        # Store required entry data in hass domain entry object
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinators": coordinators,
            "version_info": version_info,
            "host": host,
            "backups": backups,
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
    except Exception:
        _LOGGER.exception("Unexpected exception")
        return False
    else:
        # Initial sensor data refresh
        for coordinator in coordinators.values():
            await coordinator.async_refresh()
        return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Get the service
        host = hass.data[DOMAIN][entry.entry_id]["host"]
        service = hass.data[DOMAIN][host]["service"]
        # Remove the coordinator
        coordinators = hass.data[DOMAIN][entry.entry_id]["coordinators"]
        backups = entry.data.get(CONF_BACKUPS, {})
        for backup_id in backups:
            service.unregister_coordinator(coordinators[backup_id])
        # Remove the service
        if service.get_number_of_coordinators() == 0:
            await async_unload_services(hass)
            hass.data[DOMAIN].pop(host)
        # Remove the entry data
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
