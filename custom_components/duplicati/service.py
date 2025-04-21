"""Definition for Duplicati backup software services."""

import asyncio
import logging

from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant, ServiceCall, callback

from .api import ApiProcessingError, DuplicatiBackendAPI
from .const import (
    DOMAIN,
    METRIC_CURRENT_STATUS,
    METRIC_LAST_ERROR_MESSAGE,
    METRIC_LAST_STATUS,
)
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

    async def __wait_for_backup_completion(self, backup_id):
        """Wait for backup completion using the coordinator's monitoring capabilities."""
        # Get the coordinator for this backup
        coordinator = self.coordinators[backup_id]

        # Set up a one-time listener for backup completion
        completion_event = asyncio.Event()

        @callback
        def backup_state_listener():
            """Handle backup state changes."""
            # Check if backup is still running
            if coordinator.data and METRIC_CURRENT_STATUS in coordinator.data:
                if not coordinator.data[
                    METRIC_CURRENT_STATUS
                ]:  # Backup completed or failed
                    completion_event.set()

        # Register listener for coordinator updates
        remove_listener = coordinator.async_add_listener(backup_state_listener)

        try:
            # Wait for backup completion with timeout
            await asyncio.wait_for(
                completion_event.wait(), timeout=3600
            )  # 1 hour timeout

            # Check final status
            if coordinator.data and METRIC_LAST_STATUS in coordinator.data:
                if coordinator.data[METRIC_LAST_STATUS]:  # Error status is True
                    error_message = coordinator.data.get(
                        METRIC_LAST_ERROR_MESSAGE, "Unknown error"
                    )
                    raise DuplicatiServiceException(error_message)
        except TimeoutError as e:
            raise DuplicatiServiceException(
                "Backup operation timed out after 1 hour"
            ) from e
        finally:
            # Remove the listener to prevent memory leaks
            remove_listener()

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
            _LOGGER.info(
                "Backup creation for backup with ID '%s' of server '%s' initiated",
                backup_id,
                self.api.get_api_host(),
            )

            # Check if the backup ID is valid
            backup_id = str(backup_id)
            if backup_id not in self.coordinators:
                raise DuplicatiServiceException("Unknown backup ID provided")

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

            # Refresh the sensor data for the backup
            await self.async_refresh_sensor_data(backup_id)

            # Wait for the backup to complete
            await self.__wait_for_backup_completion(backup_id)

            # Handle successful backup creation
            _LOGGER.info(
                "Backup creation for backup with ID '%s' of server '%s' successfully finished",
                backup_id,
                self.api.get_api_host(),
            )

            # Fire an event to notify that the backup process has finished
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
            # Fire an event to notify that the backup process has failed
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
        finally:
            # Refresh the sensor data for the backup
            await self.async_refresh_sensor_data(backup_id)

    async def async_refresh_sensor_data(self, backup_id):
        """Service to manually update data."""
        try:
            # Check if the backup ID is valid
            backup_id = str(backup_id)
            if backup_id not in self.coordinators:
                raise DuplicatiServiceException("Unknown backup ID provided")

            # Get the coordinator of the backup ID
            coordinator = self.coordinators[backup_id]
            _LOGGER.debug(
                "Initiate sensor data refresh for backup with ID '%s' of server '%s'",
                backup_id,
                self.api.get_api_host(),
            )
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
