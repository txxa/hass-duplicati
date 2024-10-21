"""The Duplicati integration."""

import logging
import re
import urllib.parse

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ID,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import async_get_platforms

from .api import DuplicatiBackendAPI
from .const import CONF_BACKUPS, DEFAULT_SCAN_INTERVAL, DOMAIN, METRIC_LAST_STATUS
from .coordinator import DuplicatiDataUpdateCoordinator
from .service import DuplicatiService, async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Duplicati from a config entry."""
    try:
        hass.data.setdefault(DOMAIN, {})
        # Extract entry data
        base_url = entry.data[CONF_URL]
        password = entry.data.get(CONF_PASSWORD)
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
        # Create a service for managing Duplicati operations
        host = api.get_api_host()
        if host not in hass.data[DOMAIN]:
            hass.data[DOMAIN][host] = {}
        if "service" not in hass.data[DOMAIN][host]:
            service = DuplicatiService(hass, api)
            # Register coordinators
            for coordinator in coordinators.values():
                service.register_coordinator(coordinator)
            hass.data[DOMAIN][host] = {"service": service}
        # Store required entry data in hass domain entry object
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinators": coordinators,
            "version_info": version_info,
            "host": host,
            "backups": backups,
        }
        # Forward setup to used platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.info(
        "Migrating configuration from version %s.%s",
        entry.version,
        entry.minor_version,
    )

    # Skip migration if not needed
    if entry.version > 1:
        # This means the user has downgraded from a future version
        return False

    # Create a copy of the entry data
    data = {**entry.data}

    # Version 1 migration
    if entry.version == 1:
        version = 2
        minor_version = 1
        configured_backups = {}
        config_entries_to_remove = []
        backup_name_pattern = r"(.+?)\sBackup.*"

        ########## Merge config entries with same server URL ##########

        # Get config entries
        domain_config_entries = hass.config_entries.async_entries(DOMAIN)
        if len(domain_config_entries) == 0:
            _LOGGER.error("Failed to get config entries")
            _LOGGER.error(
                "Migration to configuration version %s.%s failed",
                entry.version,
                entry.minor_version,
            )
            return False

        # Get device registry
        device_registry = hass.data[dr.DATA_REGISTRY]

        # Define new title
        url = entry.data[CONF_URL]
        title = urllib.parse.urlparse(url).netloc
        # Get backup ID
        backup_id = entry.data[CONF_ID]

        # Iterate over config entries
        for config_entry in domain_config_entries:
            if config_entry.data[CONF_URL] == url:
                # Get backup ID
                b_id = config_entry.data[CONF_ID]
                # Get device entries
                device_entries = []
                for device_entry in device_registry.devices.data.values():
                    for device_config_entry in device_entry.config_entries:
                        if (
                            device_config_entry == config_entry.entry_id
                            and title is not None
                        ):
                            device_entries.append(device_entry)
                            break
                # Get device (only one device available => index=0)
                device = device_entries[0] if len(device_entries) > 0 else None
                if not device:
                    _LOGGER.error("Failed to get device entry")
                    _LOGGER.error(
                        "Migration to configuration version %s.%s failed",
                        entry.version,
                        entry.minor_version,
                    )
                    return False
                # Get backup name
                device_name = device.name if device and device.name else ""
                match = re.match(backup_name_pattern, device_name)
                if match:
                    backup_name = match.group(1).strip()
                else:
                    backup_name = f"Backup (id={backup_id})"
                # Create backups dictionary
                configured_backups[b_id] = backup_name
                # Update device
                if b_id == backup_id:
                    # Rename device
                    device_registry.async_update_device(
                        device_id=device.id, name=backup_name
                    )
                elif config_entry.entry_id != entry.entry_id:
                    # Rename device and move device to migrated entry
                    device_registry.async_update_device(
                        device_id=device.id,
                        name=backup_name,
                        add_config_entry=entry,
                        remove_config_entry_id=config_entry.entry_id,
                    )
                    # Collect old entries (with same URL but different backup ID) for removal
                    config_entries_to_remove.append(config_entry)

        # Update entry data
        data[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
        data["backups"] = configured_backups
        if CONF_ID in data:
            data.pop(CONF_ID)

        # Update entry
        hass.config_entries.async_update_entry(
            entry,
            title=title,
            data=data,
            version=version,
            minor_version=minor_version,
        )

        # Remove old entries with same URL but different backup ID
        for config_entry_to_remove in config_entries_to_remove:
            hass.async_create_task(
                hass.config_entries.async_remove(config_entry_to_remove.entry_id)
            )

        ########## Remove status sensor (replaced as binary sensor) ##########

        # Get platforms
        entities_to_remove = {}
        platforms = async_get_platforms(hass, DOMAIN)
        if len(platforms) > 0:
            for platform in platforms:
                if platform.domain == Platform.SENSOR:
                    for entity_name in platform.domain_entities:
                        entity = platform.domain_entities[entity_name]
                        # Filter entities (sensors) to remove
                        if entity.entity_description.key == METRIC_LAST_STATUS:
                            if entity.entity_id not in entities_to_remove:
                                # Collect entity (sensor) to remove entity
                                entities_to_remove[entity.entity_id] = entity
            # Remove entities (sensors)
            for entity in entities_to_remove.values():
                await platform.async_remove_entity(entity.entity_id)

    _LOGGER.info(
        "Migration to configuration version %s.%s successful",
        entry.version,
        entry.minor_version,
    )

    return True
