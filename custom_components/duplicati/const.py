"""Constants for the Duplicati integration."""

DOMAIN = "duplicati"

CONF_BACKUPS = "backups"

MODEL = "Backup"
MANUFACTURER = "Duplicati"

DEFAULT_SCAN_INTERVAL = 300

METRIC_STATUS = "last_backup_status"
METRIC_EXECUTION = "last_backup_execution"
METRIC_DURATION = "last_backup_duration"
METRIC_TARGET_SIZE = "last_backup_target_size"
METRIC_TARGET_FILES = "last_backup_target_files_count"
METRIC_SOURCE_SIZE = "last_backup_source_size"
METRIC_SOURCE_FILES = "last_backup_source_files_count"
METRIC_ERROR_MESSAGE = "last_backup_error_message"

STATUS_OK = "OK"
STATUS_ERROR = "Error"
