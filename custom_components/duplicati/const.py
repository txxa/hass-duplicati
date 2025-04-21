"""Constants for the Duplicati integration."""

DOMAIN = "duplicati"

CONF_BACKUPS = "backups"

MODEL = "Backup"
MANUFACTURER = "Duplicati"

DEFAULT_SCAN_INTERVAL_SECONDS = 300

MONITORING_SCAN_INTERVAL_SECONDS = 5
MONITORING_UPDATE_DATA_WAIT_SECONDS = 5
MONITORING_DELAYED_STARTUP_CHECK_SECONDS = 10
MONITORING_DELAYED_STARTUP_CHECK_RETRIES = 3

METRIC_CURRENT_STATUS = "current_backup_status"
METRIC_LAST_STATUS = "last_backup_status"
METRIC_LAST_EXECUTION = "last_backup_execution"
METRIC_LAST_DURATION = "last_backup_duration"
METRIC_LAST_TARGET_SIZE = "last_backup_target_size"
METRIC_LAST_TARGET_FILES = "last_backup_target_files_count"
METRIC_LAST_SOURCE_SIZE = "last_backup_source_size"
METRIC_LAST_SOURCE_FILES = "last_backup_source_files_count"
METRIC_LAST_ERROR_MESSAGE = "last_backup_error_message"

PROPERTY_NEXT_EXECUTION = "next_backup_execution"
