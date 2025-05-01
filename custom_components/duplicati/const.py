"""Constants for the Duplicati integration."""

DOMAIN = "duplicati"

CONF_BACKUPS = "backups"

MODEL = "Backup"
MANUFACTURER = "Duplicati"

DEFAULT_SCAN_INTERVAL_SECONDS = 300
MONITORING_SCAN_INTERVAL_SECONDS = 5
MONITORING_DETECTION_CHECK_RETRIES = 6
MONITORING_UPDATE_DATA_WAIT_SECONDS = 2
MONITORING_SERVICE_WAIT_TIMEOUT_SECONDS = 86400

METRIC_NEXT_EXECUTION = "next_backup_execution"
METRIC_CURRENT_STATUS = "current_backup_status"
METRIC_LAST_STATUS = "last_backup_status"
METRIC_LAST_EXECUTION = "last_backup_execution"
METRIC_LAST_DURATION = "last_backup_duration"
METRIC_LAST_TARGET_SIZE = "last_backup_target_size"
METRIC_LAST_TARGET_FILES = "last_backup_target_files_count"
METRIC_LAST_SOURCE_SIZE = "last_backup_source_size"
METRIC_LAST_SOURCE_FILES = "last_backup_source_files_count"
METRIC_LAST_ERROR_MESSAGE = "last_backup_error_message"
