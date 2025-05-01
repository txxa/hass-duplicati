"""Definition for Duplicati backup software services."""

import logging

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant, ServiceCall

from .api import ApiProcessingError, DuplicatiBackendAPI
from .const import DOMAIN
from .coordinator import DuplicatiDataUpdateCoordinator
from .event import BACKUP_COMPLETED, BACKUP_FAILED, BACKUP_STARTED, SENSORS_REFRESHED
from .model import ApiError

_LOGGER = logging.getLogger(__name__)

SERVICE_CREATE_BACKUP = "create_backup"
SERVICE_REFRESH_SENSOR_DATA = "refresh_sensor_data"
SERVICES = [SERVICE_CREATE_BACKUP, SERVICE_REFRESH_SENSOR_DATA]


async def async_setup_services(hass: HomeAssistant) -> None:
    """Service handler setup."""

    async def service_handler(call: ServiceCall) -> None:
        """Handle service call."""
        # Execute the service function
        try:
            # Verify that the host is existing
            host = call.data["host"]
            if host not in hass.data[DOMAIN]:
                raise DuplicatiServiceException(
                    f"No configuration found for Duplicati host '{host}'"
                )
            # Get the service from the host
            service: DuplicatiService = hass.data[DOMAIN][host].get("service")
            if not service:
                raise DuplicatiServiceException(
                    f"No Duplicati service found for host '{host}'"
                )
            await getattr(service, f"async_{call.service}")(call.data["backup_id"])
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Error calling service %s: %s", call.service, e)

    # Register Duplicati services
    for service in SERVICES:
        hass.services.async_register(
            DOMAIN,
            service,
            service_handler,
        )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Service handler removal."""
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)


class DuplicatiServiceException(Exception):
    """Custom exception for DuplicatiService errors."""


class DuplicatiService:
    """Service handler for Duplicati integration."""

    def __init__(self, hass: HomeAssistant, api: DuplicatiBackendAPI) -> None:
        """Initialize the Duplicati service."""
        self.hass = hass
        self.api = api
        self.coordinators = {}

    def register_coordinator(self, coordinator: DuplicatiDataUpdateCoordinator):
        """Register a coordinator."""
        backup_id = str(coordinator.backup_id)
        self.coordinators[backup_id] = coordinator

    def unregister_coordinator(self, coordinator: DuplicatiDataUpdateCoordinator):
        """Unregister a coordinator."""
        backup_id = str(coordinator.backup_id)
        if backup_id in self.coordinators:
            del self.coordinators[backup_id]

    def get_coordinators(self):
        """Return the coordinators."""
        return self.coordinators

    def get_number_of_coordinators(self) -> int:
        """Return the number of coordinators."""
        return len(self.coordinators)

    async def async_create_backup(self, backup_id):
        """Service to start a backup."""
        try:
            # Check if the backup ID is valid
            backup_id = str(backup_id)
            if backup_id not in self.coordinators:
                raise DuplicatiServiceException("Unknown backup ID provided")

            # Initiate backup creation
            _LOGGER.info(
                "Initiate backup creation for backup with ID '%s' of server '%s'",
                backup_id,
                self.api.get_api_host(),
            )
            # Start the backup process
            response = await self.api.create_backup(backup_id)

            # Check if response is valid
            if response is None:
                raise ApiProcessingError("No API response received")
            _LOGGER.debug("Backup creation response: %s", response)
            # Check if the backup process has been started
            if isinstance(response.data, ApiError):
                raise ApiProcessingError(response.data.msg)
            if "Status" not in response.data:
                raise ApiProcessingError("No status received in API response")
            if response.data["Status"] != "OK":
                raise ApiProcessingError("Unable to start the backup process")

            # Fire an event to notify that the backup process has started
            self.hass.bus.async_fire(
                BACKUP_STARTED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )

            # Get the coordinator of the backup ID
            coordinator: DuplicatiDataUpdateCoordinator = self.coordinators[backup_id]
            # Start monitoring and wait for completion
            await coordinator.start_monitoring_and_wait()

            # Handle successful backup creation
            _LOGGER.info(
                "Backup creation for backup with ID '%s' of server '%s' successfully finished",
                backup_id,
                self.api.get_api_host(),
            )
            # Fire an event to notify that the backup process finished
            self.hass.bus.async_fire(
                BACKUP_COMPLETED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )

        except Exception as e:  # noqa: BLE001
            # Handle failed backup creation
            _LOGGER.error(
                "Backup creation for backup with ID '%s' of server '%s' failed: %s",
                backup_id,
                self.api.get_api_host(),
                str(e),
            )
            # Fire an event to notify that the backup process failed
            self.hass.bus.async_fire(
                BACKUP_FAILED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
            # Create a notification in the UI
            async_create(
                self.hass,
                f"Backup creation for backup with ID '{backup_id!s}' of server '{self.api.get_api_host()}' failed: {e!s}",
                title="Backup creation error",
            )

    async def async_refresh_sensor_data(self, backup_id):
        """Service to manually update data."""
        try:
            # Check if the backup ID is valid
            backup_id = str(backup_id)
            if backup_id not in self.coordinators:
                raise DuplicatiServiceException("Unknown backup ID provided")

            # Initiate refresh
            _LOGGER.debug(
                "Initiate sensor data refresh for backup with ID '%s' of server '%s'",
                backup_id,
                self.api.get_api_host(),
            )
            # Get the coordinator of the backup ID
            coordinator: DuplicatiDataUpdateCoordinator = self.coordinators[backup_id]
            # Refresh the data
            await coordinator.async_refresh()

            # Check if the refresh was successful
            if not coordinator.last_update_success:
                raise DuplicatiServiceException(coordinator.last_exception_message)

            # Handle successful refresh
            _LOGGER.info(
                "Sensor data refresh for backup with ID '%s' of server '%s' successfully completed",
                backup_id,
                self.api.get_api_host(),
            )
            # Fire an event to notify that the sensors have been refreshed
            self.hass.bus.async_fire(
                SENSORS_REFRESHED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
        except Exception as e:  # noqa: BLE001$
            # Handle failed refresh
            _LOGGER.error(
                "Sensor data refresh for backup with ID '%s' of server '%s' failed",
                backup_id,
                self.api.get_api_host(),
            )
            # Create a notification in the UI
            async_create(
                self.hass,
                f"Sensor data refresh for backup with ID '{backup_id!s}' of server '{self.api.get_api_host()}' failed: {str(e)!s}",
                title="Sensor refresh error",
            )
