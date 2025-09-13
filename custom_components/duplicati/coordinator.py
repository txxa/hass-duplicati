"""Coordinator for Duplicati backup software."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
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
    METRIC_NEXT_EXECUTION,
    MONITORING_DETECTION_CHECK_RETRIES,
    MONITORING_SCAN_INTERVAL_SECONDS,
    MONITORING_SERVICE_WAIT_TIMEOUT_SECONDS,
    MONITORING_UPDATE_DATA_WAIT_SECONDS,
)
from .model import BackupDefinition, BackupProgress

_LOGGER = logging.getLogger(__name__)


class DuplicatiCoordinatorException(Exception):
    """Custom exception for DuplicatiDataUpdateCoordinator errors."""


class MonitoringState(Enum):
    """Enum representing the monitoring state of a backup."""

    IDLE = auto()  # Not monitoring or scheduled
    SCHEDULED = auto()  # Scheduled for future monitoring
    ACTIVE = auto()  # Actively monitoring a running backup


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
        self._next_backup_execution: datetime | None = None
        self._monitoring_scheduled_for: datetime | None = None
        self._monitoring_state = MonitoringState.IDLE
        self._remove_scheduled_backup_listener: Callable[[], None] | None = None
        self._remove_active_backup_monitor_listener: Callable[[], None] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and process data from Duplicati API."""
        try:
            _LOGGER.info(
                "Fetching %s data for backup with ID '%s' of server '%s'",
                self.name,
                self.backup_id,
                self.api.get_api_host(),
            )

            # Get unified backup state
            backup_state = await self._check_backup_state()

            # Handle monitoring state management in separate methods
            await self.__manage_monitoring_state(backup_state[METRIC_CURRENT_STATUS])
            await self.__manage_future_monitoring()

        except Exception as e:  # noqa: BLE001
            await self._handle_error(e, "data update")
            raise UpdateFailed(str(e)) from e
        else:
            return backup_state

    async def __manage_monitoring_state(self, is_running: bool) -> None:
        """Manage monitoring state based on current backup status."""
        # Start active monitoring if backup is running
        if is_running and (
            self._monitoring_state in (MonitoringState.IDLE, MonitoringState.SCHEDULED)
        ):
            _LOGGER.info("Detected running backup during data update")
            await self._start_active_monitoring()
        # Stop active monitoring if backup is no longer running
        elif not is_running and self._monitoring_state == MonitoringState.ACTIVE:
            await self._stop_active_monitoring()

    async def __manage_future_monitoring(self) -> None:
        """Manage scheduling of future monitoring if needed."""
        # Skip if no next backup execution or if the next execution is in the past
        if (
            not self._next_backup_execution
            or self._next_backup_execution <= datetime.now(UTC)
        ):
            return
        # If in SCHEDULED state, check if the scheduled time has changed
        if (
            self._monitoring_state == MonitoringState.SCHEDULED
            and self._monitoring_scheduled_for != self._next_backup_execution
        ):
            _LOGGER.info(
                "Detected new backup schedule time while in SCHEDULED state. "
                "Updating from %s to %s",
                self._monitoring_scheduled_for,
                self._next_backup_execution,
            )
            await self._schedule_future_monitoring()
        # If in IDLE state, schedule normally
        elif self._monitoring_state == MonitoringState.IDLE:
            await self._schedule_future_monitoring()

    async def _check_backup_state(self) -> dict[str, Any]:
        """Get unified backup state."""
        # Get full backup info
        sensor_data = await self.__get_backup_info(self.backup_id)

        # Check if backup is currently running
        is_running = await self.__is_backup_running(self.backup_id)

        # Set current status
        sensor_data[METRIC_CURRENT_STATUS] = is_running

        return sensor_data

    async def _start_active_monitoring(self) -> None:
        """Start active monitoring of a running backup."""
        # Check if already monitoring
        if self._monitoring_state == MonitoringState.ACTIVE:
            _LOGGER.debug("Already actively monitoring, skipping duplicate start")
            return

        _LOGGER.info("Starting active backup monitoring")

        # Clean up any existing listeners first
        self._cleanup_all_listeners()

        # Set state to active
        self.__set_monitoring_state(MonitoringState.ACTIVE)

        # Set up interval monitoring
        self._remove_active_backup_monitor_listener = async_track_time_interval(
            self.hass,
            self._check_active_backup,
            timedelta(seconds=MONITORING_SCAN_INTERVAL_SECONDS),
        )

    @callback
    def _check_active_backup(self, _now: datetime) -> None:
        """Check if actively monitored backup is still running."""
        # Only proceed if still in active monitoring state
        if self._monitoring_state != MonitoringState.ACTIVE:
            _LOGGER.debug(
                "Skipping interval check - no longer in active monitoring state"
            )
            return

        self.hass.async_create_task(self._async_check_active_backup())

    async def _async_check_active_backup(self) -> None:
        """Asynchronously check active backup status."""
        try:
            # Only proceed if in active monitoring state
            if self._monitoring_state != MonitoringState.ACTIVE:
                _LOGGER.debug("Skipping check - no longer in active monitoring state")
                return

            # Only check if backup is running without getting full info
            is_running = await self.__is_backup_running(self.backup_id)

            # Backup is still running
            if is_running:
                _LOGGER.info("Backup still running")
            # Backup finished
            else:
                # Backup finished
                _LOGGER.info("Backup completed")

                # Stop monitoring
                await self._stop_active_monitoring()

                # Wait for Duplicati to update its state
                await asyncio.sleep(MONITORING_UPDATE_DATA_WAIT_SECONDS)

                # Get final state
                final_state = await self._check_backup_state()
                self._update_and_notify(final_state, "Updating with final backup state")

                # Schedule next backup if available
                if (
                    self._next_backup_execution
                    and self._next_backup_execution > datetime.now(UTC)
                ):
                    await self._schedule_future_monitoring()
        except Exception as e:  # noqa: BLE001
            await self._handle_error(e, "active monitoring")

    async def _stop_active_monitoring(self) -> None:
        """Stop active monitoring."""
        # Check if already stopped
        if self._monitoring_state != MonitoringState.ACTIVE:
            _LOGGER.debug("Not in active monitoring state, skipping stop")
            return

        _LOGGER.info("Stopping active backup monitoring")

        # Clean up interval listener
        self._cleanup_active_backup_monitoring()

        # Reset state
        self.__set_monitoring_state(MonitoringState.IDLE)

    async def _schedule_future_monitoring(self) -> None:
        """Schedule monitoring for future backup."""
        # Guard against None value
        if self._next_backup_execution is None:
            _LOGGER.warning("Cannot schedule monitoring: no next execution time")
            return

        # Check if already scheduled for this time
        if (
            self._monitoring_state == MonitoringState.SCHEDULED
            and self._monitoring_scheduled_for
            and self._monitoring_scheduled_for == self._next_backup_execution
        ):
            _LOGGER.debug(
                "Monitoring already scheduled for %s, skipping duplicate scheduling",
                self._next_backup_execution,
            )
            return

        # Clean up any existing scheduled monitoring
        self._cleanup_scheduled_monitoring()

        # Store the scheduled time
        self._monitoring_scheduled_for = self._next_backup_execution

        _LOGGER.info(
            "Scheduling backup monitoring to start at %s",
            self._next_backup_execution,
        )

        # Set state to scheduled
        self.__set_monitoring_state(MonitoringState.SCHEDULED)

        # Schedule the monitoring
        start_time = self._next_backup_execution + timedelta(milliseconds=500)
        self._remove_scheduled_backup_listener = async_track_point_in_time(
            self.hass, self._handle_scheduled_time, start_time
        )

    @callback
    def _handle_scheduled_time(self, _now: datetime) -> None:
        """Handle scheduled monitoring time being reached."""
        self.__set_monitoring_state(MonitoringState.IDLE)
        self._remove_scheduled_backup_listener = None

        # Start checking for backup
        self.hass.async_create_task(self._check_for_scheduled_backup())

    async def _check_for_scheduled_backup(
        self,
        retry: int = 0,
        max_retries: int = MONITORING_DETECTION_CHECK_RETRIES,
    ) -> None:
        """Check if scheduled backup has started."""
        try:
            # First check if backup is running without getting full info
            is_running = await self.__is_backup_running(self.backup_id)

            # Backup is running, start monitoring
            if is_running:
                await self.__handle_detected_running_backup(retry, max_retries)
            # Not running yet, schedule another check
            elif retry <= max_retries:
                await self.__handle_backup_not_detected(retry, max_retries)

        except Exception as e:  # noqa: BLE001
            await self._handle_error(e, "scheduled backup check")

    async def __handle_detected_running_backup(
        self, retry: int, max_retries: int
    ) -> None:
        """Handle case when running backup is detected."""
        # Update backup state
        backup_state = {
            **(self.data or {}),
            METRIC_CURRENT_STATUS: True,
            METRIC_NEXT_EXECUTION: None,
        }
        # Default message
        message = "Running backup detected"
        # Started with retry
        if retry > 0:
            message = f"Running backup detected (retry {retry} of {max_retries}), stopping further retries"
        # Update backup execution state
        self._update_and_notify(backup_state, message)
        # Start active monitoring
        await self._start_active_monitoring()

    async def __handle_backup_not_detected(self, retry: int, max_retries: int) -> None:
        """Handle case when backup is not detected but retries remain."""
        # Calculate delay with exponential backoff (1, 2, 4, 8, 16, 32...)
        delay = 2 ** (retry - 1)
        # Prepare retry information
        retries = "" if retry == 0 else f" (retry {retry} of {max_retries})"
        # Check for running backup again
        if retry < max_retries:
            # Log retry information
            _LOGGER.info(
                "No running backup detected%s, checking again in %.1f seconds",
                retries,
                delay,
            )
            # Wait for delay
            await asyncio.sleep(delay)
            # Check again
            await self._check_for_scheduled_backup(retry + 1, max_retries)
        else:
            # Log retry information
            _LOGGER.info(
                "No running backup detected%s, stopping further retries", retries
            )
            # Max retries reached, handle case
            await self.__handle_max_retries_reached()

    async def __handle_max_retries_reached(self) -> None:
        """Handle case when max retries are reached without detecting backup."""
        # Stop monitoring
        await self._stop_active_monitoring()
        # Get full backup info for latest state
        _LOGGER.info("Updating with latest state after failed monitoring")
        backup_state = await self.__get_backup_info(self.backup_id)
        backup_state[METRIC_CURRENT_STATUS] = False
        # Update with latest state
        self._update_and_notify(backup_state)
        # Try to reschedule if there's a next execution time
        if self._next_backup_execution and self._next_backup_execution > datetime.now(
            UTC
        ):
            await self._schedule_future_monitoring()

    def _cleanup_scheduled_monitoring(self) -> None:
        """Clean up scheduled monitoring resources."""
        if self._remove_scheduled_backup_listener is not None:
            self._remove_scheduled_backup_listener()
            self._remove_scheduled_backup_listener = None
            _LOGGER.debug("Removed scheduled backup listener")

        # Only reset state if in scheduled state
        if self._monitoring_state == MonitoringState.SCHEDULED:
            self.__set_monitoring_state(MonitoringState.IDLE)

    def _cleanup_active_backup_monitoring(self) -> None:
        """Clean up interval listener resources."""
        if self._remove_active_backup_monitor_listener is not None:
            self._remove_active_backup_monitor_listener()
            self._remove_active_backup_monitor_listener = None
            _LOGGER.debug("Removed active backup monitor listener")

        # Always reset to IDLE state
        self.__set_monitoring_state(MonitoringState.IDLE)

    def _cleanup_all_listeners(self) -> None:
        """Clean up all listeners."""
        self._cleanup_scheduled_monitoring()
        self._cleanup_active_backup_monitoring()
        self._monitoring_state = MonitoringState.IDLE

    def __set_monitoring_state(self, new_state: MonitoringState) -> None:
        """Change monitoring state."""
        previous_state = self._monitoring_state
        self._monitoring_state = new_state
        # Only log if state changed
        if previous_state != new_state:
            _LOGGER.debug(
                "Monitoring state changed from %s to %s",
                previous_state.name,
                self._monitoring_state.name,
            )

    def _update_and_notify(
        self, data: dict[str, Any], log_message: str | None = None
    ) -> None:
        """Update data and notify listeners with optional logging."""
        if log_message:
            _LOGGER.info(log_message)

        self.async_set_updated_data(data)
        _LOGGER.info("Manually updated duplicati data")

    async def _handle_error(self, error: Exception, context: str) -> None:
        """Handle errors with unified approach."""
        self.last_exception_message = str(error)
        _LOGGER.error("Error in %s: %s", context, error)

        # Try to recover state
        try:
            # Clean up listeners
            self._cleanup_all_listeners()

            # Try to get latest backup info
            backup_state = await self.__get_backup_info(self.backup_id)
            backup_state[METRIC_CURRENT_STATUS] = False
            self.async_set_updated_data(backup_state)

            # Reschedule if possible
            if (
                self._next_backup_execution
                and self._next_backup_execution > datetime.now(UTC)
            ):
                await self._schedule_future_monitoring()
        except Exception as recovery_error:  # noqa: BLE001
            _LOGGER.error("Failed to recover from error: %s", recovery_error)

    async def start_monitoring(self) -> None:
        """Start active monitoring of a running backup.

        Public method that can be called by external components.
        """
        await self._start_active_monitoring()

    async def start_monitoring_and_wait(self) -> None:
        """Start active monitoring of a running backup and wait for completion.

        This method is designed to be called from the service to start a backup
        and wait for its completion.
        """
        # Refresh data to get current state
        await self.async_refresh()
        # Set up a one-time listener for backup completion
        completion_event = asyncio.Event()

        @callback
        def backup_state_listener():
            """Handle backup state changes."""
            # Check if backup is still running
            if self.data and METRIC_CURRENT_STATUS in self.data:
                if not self.data[METRIC_CURRENT_STATUS]:  # Backup completed or failed
                    completion_event.set()

        # Register listener for coordinator updates
        remove_listener = self.async_add_listener(backup_state_listener)
        # Wait for backup completion with timeout
        try:
            await asyncio.wait_for(
                completion_event.wait(), timeout=MONITORING_SERVICE_WAIT_TIMEOUT_SECONDS
            )
            # Check final status
            if self.data and METRIC_LAST_STATUS in self.data:
                if self.data[METRIC_LAST_STATUS]:  # Error status is True
                    error_message = self.data.get(
                        METRIC_LAST_ERROR_MESSAGE, "Unknown error"
                    )
                    raise DuplicatiCoordinatorException(error_message)
        # Handle timeout error
        except TimeoutError as e:
            raise DuplicatiCoordinatorException(
                f"Backup operation timed out after {MONITORING_SERVICE_WAIT_TIMEOUT_SECONDS} seconds"
            ) from e
        # Remove the listener to prevent memory leaks
        finally:
            remove_listener()

    async def async_unload(self) -> None:
        """Clean up resources when coordinator is unloaded."""
        self._cleanup_all_listeners()

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
        # Prepare the sensor data vlaues
        last_backup_status = False
        last_backup_execution = (
            response.data.backup.metadata.last_backup_finished
        )  # Duplicati is not updating last_backup_date on each execution
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
        self._next_backup_execution = None
        if response.data.schedule:
            if (
                response.data.schedule.time
                and response.data.schedule.time > datetime.now(UTC)
            ):
                self._next_backup_execution = response.data.schedule.time
        # Error case
        if response.data.backup.metadata.last_error_date:
            if (
                not response.data.backup.metadata.last_backup_finished
                or response.data.backup.metadata.last_error_date
                > response.data.backup.metadata.last_backup_finished
            ):
                last_backup_status = True
                last_backup_execution = response.data.backup.metadata.last_error_date
                last_backup_error_message = (
                    response.data.backup.metadata.last_error_message
                )
                last_backup_duration = None
                last_backup_source_size = None
                last_backup_source_files_count = None
                last_backup_target_size = None
                last_backup_target_files_count = None
        # Set the sensor data values
        sensor_data[METRIC_NEXT_EXECUTION] = self._next_backup_execution
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
