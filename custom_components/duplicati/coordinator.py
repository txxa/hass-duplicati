"""Coordinator for Duplicati backup software."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DuplicatiBackendAPI
from .const import (
    DOMAIN,
    METRIC_CURRENT_STATUS,
    METRIC_LAST_DURATION,
    METRIC_LAST_ERROR_MESSAGE,
    METRIC_LAST_EXECUTION,
    METRIC_LAST_SOURCE_FILES,
    METRIC_LAST_SOURCE_SIZE,
    METRIC_LAST_STATUS,
    METRIC_LAST_TARGET_FILES,
    METRIC_LAST_TARGET_SIZE,
    MONITORING_DELAYED_STARTUP_CHECK_RETRIES,
    MONITORING_DELAYED_STARTUP_CHECK_SECONDS,
    MONITORING_SCAN_INTERVAL_SECONDS,
    MONITORING_UPDATE_DATA_WAIT_SECONDS,
)
from .model import BackupDefinition, BackupProgress

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
        """Initialize the data update coordinator.

        Args:
            hass: Home Assistant instance
            api: Duplicati API client
            backup_id: ID of the backup to monitor
            update_interval: Regular update interval in seconds

        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )
        self.api = api
        self.backup_id = backup_id
        self.last_exception_message = None
        self.next_backup_execution: datetime | None = None
        self._monitoring_active = False
        self._monitoring_scheduled = False
        self._remove_point_in_time_listener: Callable[[], None] | None = None
        self._remove_interval_listener: Callable[[], None] | None = None
        self._remove_delayed_check: Callable[[], None] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and process data from Duplicati API."""
        try:
            _LOGGER.debug(
                "Start fetching %s data for backup with ID '%s' of server '%s'",
                self.name,
                self.backup_id,
                self.api.get_api_host(),
            )

            # Get full backup info
            sensor_data = await self.__get_backup_info(self.backup_id)

            # Check if a backup is currently running (important for initial load)
            is_running = await self.__is_backup_running(self.backup_id)

            # Set current status as a boolean (True if running, False if not)
            sensor_data[METRIC_CURRENT_STATUS] = is_running

            # Start monitoring if backup is running and not already monitoring
            if is_running and not self._monitoring_active:
                _LOGGER.debug("Detected running backup during data update")
                await self._start_backup_monitoring()

            # Schedule monitoring for next backup if not already monitoring or scheduled
            if (
                self.next_backup_execution
                and not self._monitoring_active
                and not self._monitoring_scheduled
                and self.next_backup_execution > datetime.now(UTC)
            ):
                await self._schedule_backup_monitoring()

        except Exception as e:  # noqa: BLE001
            self.last_exception_message = str(e)
            raise UpdateFailed(str(e)) from e
        else:
            # Return the sensor data
            return sensor_data

    async def _schedule_backup_monitoring(self) -> None:
        """Schedule monitoring to start at the next backup execution time."""
        # Clean up any existing scheduled monitoring
        self._cleanup_scheduled_monitoring()

        # Guard against None value
        if self.next_backup_execution is None:
            _LOGGER.warning(
                "Cannot schedule backup monitoring: next_backup_execution is None"
            )
            return

        # Mark as scheduled
        self._monitoring_scheduled = True

        _LOGGER.debug(
            "Scheduling backup monitoring to start at %s", self.next_backup_execution
        )

        # Schedule the check for when the backup should start
        self._remove_point_in_time_listener = async_track_point_in_time(
            self.hass, self._handle_scheduled_backup_time, self.next_backup_execution
        )

    @callback
    def _handle_scheduled_backup_time(self, _now: datetime) -> None:
        """Handle the scheduled backup time being reached.

        Args:
            _now: Current time (provided by async_track_point_in_time)

        """
        self._monitoring_scheduled = False
        self._remove_point_in_time_listener = None

        # Start monitoring asynchronously
        self.hass.async_create_task(self._start_backup_monitoring())

    async def _start_backup_monitoring(
        self,
        retry_count: int = 1,
        max_retries: int = MONITORING_DELAYED_STARTUP_CHECK_RETRIES,
    ) -> None:
        """Start monitoring a backup that should be running.

        Args:
            retry_count: Current retry attempt count
            max_retries: Maximum number of retry attempts

        """
        # First check if backup is actually running
        is_running = await self.__is_backup_running(self.backup_id)

        if not is_running:
            # Log the current attempt (starting from 1 for better readability)
            _LOGGER.debug(
                "Expected backup to start but it's not running (attempt %s of %s)",
                retry_count,
                max_retries,
            )

            # Check if we've reached the maximum number of retries
            if retry_count >= max_retries:
                _LOGGER.warning(
                    "Backup didn't start after %s retries, stopping monitoring attempts for this schedule backup",
                    max_retries,
                )
                # Refresh backup info to get the latest next scheduled backup time
                try:
                    _LOGGER.debug("Refreshing backup info to get latest schedule")
                    updated_data = await self.__get_backup_info(self.backup_id)
                    updated_data[METRIC_CURRENT_STATUS] = False
                    self.async_set_updated_data(updated_data)
                except Exception as e:  # noqa: BLE001
                    _LOGGER.error(
                        "Failed to refresh backup info after failed monitoring: %s", e
                    )
                return

            # Check again after delay in case it's delayed
            self._cleanup_delayed_check()

            @callback
            def delayed_check(_now: datetime) -> None:
                """Handle delayed check callback."""
                self._remove_delayed_check = None
                # Pass the incremented retry count
                self.hass.async_create_task(
                    self._start_backup_monitoring(retry_count + 1, max_retries)
                )

            self._remove_delayed_check = async_track_point_in_time(
                self.hass,
                delayed_check,
                datetime.now(UTC)
                + timedelta(seconds=MONITORING_DELAYED_STARTUP_CHECK_SECONDS),
            )
            return

        _LOGGER.debug("Backup is running, setting up monitoring")

        # Mark as actively monitoring
        self._monitoring_active = True

        # Set up frequent monitoring while backup is running
        self._cleanup_interval_listener()
        self._remove_interval_listener = async_track_time_interval(
            self.hass,
            self._check_backup_status,
            timedelta(seconds=MONITORING_SCAN_INTERVAL_SECONDS),
        )

        # Initial status update
        self.async_set_updated_data({**(self.data or {}), METRIC_CURRENT_STATUS: True})

    @callback
    def _check_backup_status(self, _now: datetime) -> None:
        """Check if backup is still running.

        Args:
            _now: Current time (provided by async_track_time_interval)

        """
        self.hass.async_create_task(self._async_check_backup_status())

    async def _async_check_backup_status(self) -> None:
        """Asynchronously check backup status and update data."""
        try:
            is_still_running = await self.__is_backup_running(self.backup_id)

            if is_still_running:
                # Update status but keep monitoring
                self.async_set_updated_data(
                    {**(self.data or {}), METRIC_CURRENT_STATUS: True}
                )
            else:
                # Backup finished, wait a moment for Duplicati to update its schedule
                _LOGGER.debug(
                    "Backup completed, waiting %s seconds for schedule update",
                    MONITORING_UPDATE_DATA_WAIT_SECONDS,
                )
                await asyncio.sleep(MONITORING_UPDATE_DATA_WAIT_SECONDS)
                # Now get final status with updated schedule
                _LOGGER.debug("Getting final status after waiting")
                final_data = await self.__get_backup_info(self.backup_id)
                final_data[METRIC_CURRENT_STATUS] = False
                self.async_set_updated_data(final_data)

                # Stop monitoring
                self._stop_monitoring()
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Error checking backup status: %s", e)
            # Stop monitoring on error to prevent continuous errors
            self._stop_monitoring()

    def _stop_monitoring(self) -> None:
        """Stop all active monitoring."""
        self._cleanup_interval_listener()
        self._monitoring_active = False

    def _cleanup_scheduled_monitoring(self) -> None:
        """Clean up scheduled monitoring resources."""
        if self._remove_point_in_time_listener is not None:
            self._remove_point_in_time_listener()
            self._remove_point_in_time_listener = None
        self._monitoring_scheduled = False

    def _cleanup_interval_listener(self) -> None:
        """Clean up interval listener resources."""
        if self._remove_interval_listener is not None:
            self._remove_interval_listener()
            self._remove_interval_listener = None

    def _cleanup_delayed_check(self) -> None:
        """Clean up delayed check resources."""
        if self._remove_delayed_check is not None:
            self._remove_delayed_check()
            self._remove_delayed_check = None

    async def async_unload(self) -> None:
        """Clean up resources when coordinator is unloaded."""
        self._cleanup_scheduled_monitoring()
        self._cleanup_interval_listener()
        self._cleanup_delayed_check()

    async def __is_backup_running(self, backup_id: str) -> bool:
        """Get the current backup state.

        Args:
            backup_id: ID of the backup to check

        Returns:
            True if backup is running, False otherwise

        Raises:
            UpdateFailed: If API response is invalid

        """
        # Get the current backup state
        response = await self.api.get_progress_state()
        if not isinstance(response.data, BackupProgress):
            raise UpdateFailed("Invalid response from API")
        # Check if the backup process is running
        if response.data.backup_id == backup_id and response.data.phase not in (
            "Error",
            "Backup_Complete",
        ):
            return True
        # Else, the backup is not running
        return False

    async def __get_backup_info(self, backup_id: str) -> dict[str, Any]:
        """Get the last backup information.

        Args:
            backup_id: ID of the backup to get information for

        Returns:
            Dictionary containing sensor data

        Raises:
            UpdateFailed: If API response is invalid

        """
        sensor_data = {}
        # Get the current backup state
        response = await self.api.get_backup(backup_id)
        if not isinstance(response.data, BackupDefinition):
            raise UpdateFailed(f"Invalid response from API: {response}")
        # Check backup state
        error = False
        if (
            response.data.backup.metadata.last_error_date
            and not response.data.backup.metadata.last_backup_date
        ):
            error = True
        elif (
            response.data.backup.metadata.last_error_date
            and response.data.backup.metadata.last_backup_date
        ):
            if (
                response.data.backup.metadata.last_error_date
                > response.data.backup.metadata.last_backup_date
            ):
                error = True
            else:
                error = False
        elif (
            not response.data.backup.metadata.last_error_date
            and response.data.backup.metadata.last_backup_date
        ):
            error = False
        # Prepare the sensor data vlaues
        if error:
            last_backup_execution = response.data.backup.metadata.last_error_date
            last_backup_status = True
            last_backup_error_message = response.data.backup.metadata.last_error_message
            last_backup_duration = None
            last_backup_source_size = None
            last_backup_source_files_count = None
            last_backup_target_size = None
            last_backup_target_files_count = None
        else:
            last_backup_execution = response.data.backup.metadata.last_backup_date
            last_backup_status = False
            last_backup_error_message = "-"
            last_backup_duration = response.data.backup.metadata.last_backup_duration
            if last_backup_duration:
                last_backup_duration = last_backup_duration.total_seconds()
            last_backup_source_size = response.data.backup.metadata.source_files_size
            last_backup_source_files_count = (
                response.data.backup.metadata.source_files_count
            )
            last_backup_target_size = response.data.backup.metadata.target_files_size
            last_backup_target_files_count = (
                response.data.backup.metadata.target_files_count
            )
        if response.data.schedule:
            self.next_backup_execution = response.data.schedule.time
        # Set the sensor data values
        sensor_data[METRIC_LAST_STATUS] = last_backup_status
        sensor_data[METRIC_LAST_EXECUTION] = last_backup_execution
        sensor_data[METRIC_LAST_DURATION] = last_backup_duration
        sensor_data[METRIC_LAST_TARGET_SIZE] = last_backup_target_size
        sensor_data[METRIC_LAST_TARGET_FILES] = last_backup_target_files_count
        sensor_data[METRIC_LAST_SOURCE_SIZE] = last_backup_source_size
        sensor_data[METRIC_LAST_SOURCE_FILES] = last_backup_source_files_count
        sensor_data[METRIC_LAST_ERROR_MESSAGE] = last_backup_error_message
        # Return the sensor data
        return sensor_data
