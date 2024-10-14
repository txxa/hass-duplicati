"""Coordinator for Duplicati backup software."""

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ApiResponseError, DuplicatiBackendAPI
from .binary_sensor import BINARY_SENSORS
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
)
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

    def __truncate_error_message(self, message: str, max_length: int = 255) -> str:
        """Truncate error message to fit within the character limit."""
        truncation_indicator = " ... (see log for full message)"
        available_length = max_length - len(truncation_indicator)
        # If the message is already within the character limit, return it as is
        if len(message) <= available_length:
            return message
        # Split the message into words and truncate the message
        words = message.split()
        truncated = ""
        for word in words:
            if len(truncated + word) <= available_length:
                truncated += word + " "
            else:
                break
        # Add the truncation indicator if the full message is exceeding the limit
        return truncated.strip() + truncation_indicator

    def __convert_duration_string_to_seconds(self, duration_string: str) -> float:
        """Convert duration string to seconds."""
        # Split the duration string into hours, minutes, seconds, and microseconds
        parts = duration_string.split(":")
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds, microseconds = map(float, parts[2].split("."))
        microseconds = int(f"{microseconds:.6f}".replace(".", "").ljust(6, "0")[:6])
        milliseconds = round(microseconds / 1000)
        # Calculate the total duration in seconds
        return (hours * 3600) + (minutes * 60) + seconds + (milliseconds / 1000)

    async def _async_update_data(self):
        """Fetch and process data from Duplicati API."""
        try:
            _LOGGER.debug(
                "Start fetching %s data for backup with ID '%s' of server '%s'",
                self.name,
                self.backup_id,
                self.api.get_api_host(),
            )
            # Get backup info
            backup_info = await self.api.get_backup(self.backup_id)
            if "Error" in backup_info:
                raise ApiResponseError(backup_info["Error"])
            # Process metrics for sensors and return sensor data
            return self._process_data(backup_info)
        except Exception as e:  # noqa: BLE001
            self.last_exception_message = str(e)
            raise UpdateFailed(str(e)) from e

    def _process_data(self, data):
        """Process raw data into sensor values."""

        if "LastBackupDate" in data["data"]["Backup"]["Metadata"]:
            last_backup_date = data["data"]["Backup"]["Metadata"]["LastBackupDate"]
            last_backup_date = datetime.strptime(last_backup_date, "%Y%m%dT%H%M%SZ")
            last_backup_date = last_backup_date.replace(tzinfo=dt_util.UTC)
        else:
            last_backup_date = None

        if "LastErrorDate" in data["data"]["Backup"]["Metadata"]:
            last_error_date = data["data"]["Backup"]["Metadata"]["LastErrorDate"]
            last_error_date = datetime.strptime(last_error_date, "%Y%m%dT%H%M%SZ")
            last_error_date = last_error_date.replace(tzinfo=dt_util.UTC)
        else:
            last_error_date = None

        # Check backup state
        if last_error_date and not last_backup_date:
            error = True
        elif last_error_date and last_backup_date:
            if last_error_date > last_backup_date:
                error = True
            else:
                error = False
        elif not last_error_date and last_backup_date:
            error = False

        if error:
            last_backup_execution = last_error_date
            last_backup_status = True
            if "LastErrorMessage" in data["data"]["Backup"]["Metadata"]:
                last_backup_error_message = self.__truncate_error_message(
                    data["data"]["Backup"]["Metadata"]["LastErrorMessage"]
                )
                last_backup_duration = None
                last_backup_source_size = None
                last_backup_source_files_count = None
                last_backup_target_size = None
                last_backup_target_files_count = None
        else:
            last_backup_execution = last_backup_date
            last_backup_status = False
            last_backup_error_message = "-"
            if "LastBackupDuration" in data["data"]["Backup"]["Metadata"]:
                last_backup_duration = self.__convert_duration_string_to_seconds(
                    data["data"]["Backup"]["Metadata"]["LastBackupDuration"]
                )

            if "SourceFilesSize" in data["data"]["Backup"]["Metadata"]:
                last_backup_source_size = data["data"]["Backup"]["Metadata"][
                    "SourceFilesSize"
                ]

            if "SourceFilesCount" in data["data"]["Backup"]["Metadata"]:
                last_backup_source_files_count = data["data"]["Backup"]["Metadata"][
                    "SourceFilesCount"
                ]

            if "TargetFilesSize" in data["data"]["Backup"]["Metadata"]:
                last_backup_target_size = data["data"]["Backup"]["Metadata"][
                    "TargetFilesSize"
                ]

            if "TargetFilesCount" in data["data"]["Backup"]["Metadata"]:
                last_backup_target_files_count = data["data"]["Backup"]["Metadata"][
                    "TargetFilesCount"
                ]

        processed_data = {}

        for sensor_type in BINARY_SENSORS:
            # Process data according to sensor type
            if sensor_type == METRIC_STATUS:
                processed_data[sensor_type] = last_backup_status

        for sensor_type in SENSORS:
            # Process data according to sensor type
            if sensor_type == METRIC_EXECUTION:
                processed_data[sensor_type] = last_backup_execution
            elif sensor_type == METRIC_DURATION:
                processed_data[sensor_type] = last_backup_duration
            elif sensor_type == METRIC_TARGET_SIZE:
                processed_data[sensor_type] = last_backup_target_size
            elif sensor_type == METRIC_TARGET_FILES:
                processed_data[sensor_type] = last_backup_target_files_count
            elif sensor_type == METRIC_SOURCE_SIZE:
                processed_data[sensor_type] = last_backup_source_size
            elif sensor_type == METRIC_SOURCE_FILES:
                processed_data[sensor_type] = last_backup_source_files_count
            elif sensor_type == METRIC_ERROR_MESSAGE:
                processed_data[sensor_type] = last_backup_error_message

        return processed_data
