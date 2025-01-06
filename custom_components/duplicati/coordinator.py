"""Coordinator for Duplicati backup software."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DuplicatiBackendAPI
from .binary_sensor import BINARY_SENSORS
from .const import (
    DOMAIN,
    METRIC_LAST_DURATION,
    METRIC_LAST_ERROR_MESSAGE,
    METRIC_LAST_EXECUTION,
    METRIC_LAST_SOURCE_FILES,
    METRIC_LAST_SOURCE_SIZE,
    METRIC_LAST_STATUS,
    METRIC_LAST_TARGET_FILES,
    METRIC_LAST_TARGET_SIZE,
)
from .model import BackupDefinition
from .sensor import SENSORS

_LOGGER = logging.getLogger(__name__)


class DuplicatiDataUpdateCoordinator(DataUpdateCoordinator):
    """Define an object to manage Duplicati data update coordination."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: DuplicatiBackendAPI,
        backup_id: str,
        update_interval: int,
    ) -> None:
        """Initialize the data update coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.api = api
        self.backup_id = backup_id
        self.last_exception_message = None
        self.next_backup_execution = None

    async def _async_update_data(self):
        """Fetch and process data from Duplicati API."""
        try:
            _LOGGER.debug(
                "Start fetching %s data for backup with ID '%s' of server '%s'",
                self.name,
                self.backup_id,
                self.api.get_api_host(),
            )
            # Get backup definition
            backup_definition = await self.api.get_backup(self.backup_id)

            # Process metrics for sensors and return sensor data
            return self._process_data(backup_definition)
        except Exception as e:  # noqa: BLE001
            self.last_exception_message = str(e)
            raise UpdateFailed(str(e)) from e

    def _process_data(self, data: BackupDefinition):
        """Process raw data into sensor values."""

        # backup = BackupConfig.from_dict(data)
        backup_definition = data

        # Check backup state
        error = False
        if (
            backup_definition.backup.metadata.last_error_date
            and not backup_definition.backup.metadata.last_backup_date
        ):
            error = True
        elif (
            backup_definition.backup.metadata.last_error_date
            and backup_definition.backup.metadata.last_backup_date
        ):
            if (
                backup_definition.backup.metadata.last_error_date
                > backup_definition.backup.metadata.last_backup_date
            ):
                error = True
            else:
                error = False
        elif (
            not backup_definition.backup.metadata.last_error_date
            and backup_definition.backup.metadata.last_backup_date
        ):
            error = False

        if error:
            last_backup_execution = backup_definition.backup.metadata.last_error_date
            last_backup_status = True
            last_backup_error_message = (
                backup_definition.backup.metadata.last_error_message
            )
            last_backup_duration = None
            last_backup_source_size = None
            last_backup_source_files_count = None
            last_backup_target_size = None
            last_backup_target_files_count = None
        else:
            last_backup_execution = backup_definition.backup.metadata.last_backup_date
            last_backup_status = False
            last_backup_error_message = "-"
            last_backup_duration = (
                backup_definition.backup.metadata.last_backup_duration
            )
            if last_backup_duration:
                last_backup_duration = last_backup_duration.total_seconds()
            last_backup_source_size = (
                backup_definition.backup.metadata.source_files_size
            )
            last_backup_source_files_count = (
                backup_definition.backup.metadata.source_files_count
            )
            last_backup_target_size = (
                backup_definition.backup.metadata.target_files_size
            )
            last_backup_target_files_count = (
                backup_definition.backup.metadata.target_files_count
            )

        if backup_definition.schedule:
            self.next_backup_execution = backup_definition.schedule.time

        processed_data = {}

        for sensor_type in BINARY_SENSORS:
            # Process data according to sensor type
            if sensor_type == METRIC_LAST_STATUS:
                processed_data[sensor_type] = last_backup_status

        for sensor_type in SENSORS:
            # Process data according to sensor type
            if sensor_type == METRIC_LAST_EXECUTION:
                processed_data[sensor_type] = last_backup_execution
            elif sensor_type == METRIC_LAST_DURATION:
                processed_data[sensor_type] = last_backup_duration
            elif sensor_type == METRIC_LAST_TARGET_SIZE:
                processed_data[sensor_type] = last_backup_target_size
            elif sensor_type == METRIC_LAST_TARGET_FILES:
                processed_data[sensor_type] = last_backup_target_files_count
            elif sensor_type == METRIC_LAST_SOURCE_SIZE:
                processed_data[sensor_type] = last_backup_source_size
            elif sensor_type == METRIC_LAST_SOURCE_FILES:
                processed_data[sensor_type] = last_backup_source_files_count
            elif sensor_type == METRIC_LAST_ERROR_MESSAGE:
                processed_data[sensor_type] = last_backup_error_message

        return processed_data
