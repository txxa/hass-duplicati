"""Definition for Duplicati backup software services."""

import asyncio
import logging
import urllib.parse

import aiohttp
from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant, ServiceCall

from .api import ApiResponseError, DuplicatiBackendAPI
from .const import DOMAIN
from .coordinator import DuplicatiDataUpdateCoordinator
from .event import BACKUP_COMPLETED, BACKUP_FAILED, BACKUP_STARTED, SENSORS_REFRESHED

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

    async def _wait_for_backup_completion(self, backup_id):
        """Wait for the backup process to complete and fire an event."""
        while True:
            # Check the backup progress state
            response = await self.api.get_progress_state()
            resp_backup_id = response["BackupID"]
            resp_phase = response["Phase"]
            resp_progress = response["OverallProgress"]
            # Check if the backup process has failed
            if resp_backup_id == backup_id and resp_phase == "Error":
                error_message = "Error while creating backup"
                response = await self.api.get_backup(backup_id)
                if "LastErrorMessage" in response["data"]["Backup"]["Metadata"]:
                    error_message = response["data"]["Backup"]["Metadata"][
                        "LastErrorMessage"
                    ]
                if error_message == "No route to host":
                    if "TargetURL" in response["data"]["Backup"]:
                        target = urllib.parse.urlparse(
                            response["data"]["Backup"]["TargetURL"]
                        )
                    error_message += f" '{target.netloc}'"
                raise DuplicatiServiceException(error_message)
            # Check if the backup process has finished
            if resp_backup_id == backup_id and resp_phase == "Backup_Complete":
                break
            _LOGGER.debug(
                "Backup process for backup with ID '%s' in progress: %s%%",
                backup_id,
                resp_progress,
            )
            # Wait for 1 second before checking the backup progress state again
            await asyncio.sleep(1)

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
            _LOGGER.info("Backup process for backup with ID '%s' initiated", backup_id)
            # Check if the backup ID is valid
            backup_id = str(backup_id)
            if backup_id not in self.coordinators:
                raise DuplicatiServiceException("Unknown backup ID provided")
            # Start the backup process
            _LOGGER.info(
                "Calling the Duplicati backend API to start the backup process for backup with ID '%s'",
                backup_id,
            )
            resp = await self.api.create_backup(backup_id)
            if resp is None:
                raise ApiResponseError("No API response received")

            _LOGGER.debug("Backup creation response: %s", resp)

            # Check if the backup process has been started
            if "Error" in resp:
                raise ApiResponseError(resp["Error"])
            if "Status" not in resp:
                raise ApiResponseError("No status received in API response")
            if resp["Status"] != "OK":
                raise ApiResponseError("Unable to start the backup process")
            # Fire an event to notify that the backup process has started
            self.hass.bus.async_fire(
                BACKUP_STARTED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
            # Wait for the backup process to complete
            await self._wait_for_backup_completion(backup_id)
            # Fire an event to notify that the backup process has finished
            self.hass.bus.async_fire(
                BACKUP_COMPLETED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
            _LOGGER.info(
                "Backup creation for backup with ID '%s' sucessfully finished",
                backup_id,
            )
            # Refresh the sensor data for the backup
            await self.async_refresh_sensor_data(backup_id)
        except DuplicatiServiceException as e:
            _LOGGER.error(
                "Backup creation for backup with ID '%s' failed: %s", backup_id, str(e)
            )
            # Fire an event to notify that the backup process has failed
            self.hass.bus.async_fire(
                BACKUP_FAILED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
            async_create(
                self.hass,
                f"Backup creation for backup with ID '{backup_id!s}' failed: {e!s}",
                title="Backup creation error",
            )
        except aiohttp.ClientConnectionError as e:
            _LOGGER.error(
                "Backup creation for backup with ID '%s' failed: %s", backup_id, str(e)
            )
            # Fire an event to notify that the backup process has failed
            self.hass.bus.async_fire(
                BACKUP_FAILED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
            async_create(
                self.hass,
                f"Backup creation for backup with ID '{backup_id!s}' failed: {e!s}",
                title="Backup creation error",
            )

        except ApiResponseError as e:
            _LOGGER.error(
                "Backup creation for backup with ID '%s' failed: %s", backup_id, str(e)
            )
            # Fire an event to notify that the backup process has failed
            self.hass.bus.async_fire(
                BACKUP_FAILED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
            async_create(
                self.hass,
                f"Backup creation for backup with ID '{backup_id!s}' failed: {e!s}",
                title="Backup creation error",
            )

    async def async_refresh_sensor_data(self, backup_id):
        """Service to manually update data."""
        try:
            # Check if the backup ID is valid
            backup_id = str(backup_id)
            if backup_id not in self.coordinators:
                raise DuplicatiServiceException("Unknown backup ID provided")
            # Get the coordinator of the backup ID
            coordinator = self.coordinators[backup_id]
            # Refresh the data
            await coordinator.async_refresh()
            _LOGGER.info("Sensor data successfully refreshed")
            # Fire an event to notify that the sensors have been refreshed
            self.hass.bus.async_fire(
                SENSORS_REFRESHED,
                {
                    "host": self.api.get_api_host(),
                    "backup_id": backup_id,
                },
            )
        except DuplicatiServiceException as e:
            _LOGGER.error(
                "Sensors of backup with ID '%s' could not be refreshed: %s",
                backup_id,
                str(e),
            )
            async_create(
                self.hass,
                f"Sensors of backup with ID '{backup_id!s}' could not be refreshed: {e!s}",
                title="Sensor refresh error",
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "Sensors of backup with ID '%s' could not be refreshed: %s",
                backup_id,
                str(e),
            )
            async_create(
                self.hass,
                f"Sensors of backup with ID '{backup_id!s}' could not be refreshed: {e!s}",
                title="Sensor refresh error",
            )
