"""Module for handling Duplicati backup data and URL components."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from homeassistant.util import dt as dt_util


@dataclass
class BackupDefinition:
    """Represents a complete backup definition including backup info and schedule."""

    @dataclass
    class Backup:
        """Represents a Duplicati backup."""

        @dataclass
        class Metadata:
            """Represents metadata for a Duplicati backup."""

            last_backup_date: datetime | None
            backup_list_count: str | None
            total_quota_space: str | None
            free_quota_space: str | None
            assigned_quota_space: str | None
            target_files_size: str | None
            target_files_count: str | None
            target_size_string: str | None
            source_files_size: str | None
            source_files_count: str | None
            source_size_string: str | None
            last_backup_started: datetime | None
            last_backup_finished: datetime | None
            last_backup_duration: timedelta | None
            last_compact_duration: timedelta | None
            last_compact_started: datetime | None
            last_compact_finished: datetime | None
            last_error_date: datetime | None
            last_error_message: str | None

            FIELD_MAPPING = {
                "last_backup_date": "LastBackupDate",
                "backup_list_count": "BackupListCount",
                "total_quota_space": "TotalQuotaSpace",
                "free_quota_space": "FreeQuotaSpace",
                "assigned_quota_space": "AssignedQuotaSpace",
                "target_files_size": "TargetFilesSize",
                "target_files_count": "TargetFilesCount",
                "target_size_string": "TargetSizeString",
                "source_files_size": "SourceFilesSize",
                "source_files_count": "SourceFilesCount",
                "source_size_string": "SourceSizeString",
                "last_backup_started": "LastBackupStarted",
                "last_backup_finished": "LastBackupFinished",
                "last_backup_duration": "LastBackupDuration",
                "last_compact_duration": "LastCompactDuration",
                "last_compact_started": "LastCompactStarted",
                "last_compact_finished": "LastCompactFinished",
                "last_error_date": "LastErrorDate",
                "last_error_message": "LastErrorMessage",
            }

            @classmethod
            def from_dict(cls, data: dict):
                """Create Metadata instance from API response."""
                converted_data = {}
                for cls_field, api_field in cls.FIELD_MAPPING.items():
                    value = data.get(api_field)
                    if cls_field.endswith(("_date", "_started", "_finished")):
                        value = (
                            cls.__parse_datetime(value) if value is not None else None
                        )
                        if value and not isinstance(value, (datetime, type(None))):
                            raise TypeError(
                                f"Field {cls_field} must be datetime or None"
                            )
                    elif cls_field.endswith("_duration"):
                        value = (
                            cls.__parse_duration(value) if value is not None else None
                        )
                        if value and not isinstance(value, (timedelta, type(None))):
                            raise TypeError(
                                f"Field {cls_field} must be timedelta or None"
                            )
                    elif cls_field == "last_error_message":
                        value = (
                            cls.__truncate_error_message(value)
                            if value is not None
                            else None
                        )
                    converted_data[cls_field] = value
                return cls(**converted_data)

            def to_dict(self) -> dict:
                """Convert Metadata instance to API response format."""
                result = {}
                for cls_field, api_field in self.FIELD_MAPPING.items():
                    value = getattr(self, cls_field)
                    if cls_field.endswith(("_date", "_started", "_finished")):
                        value = (
                            self.__datetime_to_string(value)
                            if value is not None
                            else None
                        )
                    elif cls_field.endswith("_duration"):
                        value = (
                            self.__duration_to_string(value)
                            if value is not None
                            else None
                        )
                    result[api_field] = value
                return result

            @staticmethod
            def __parse_datetime(date_string: str) -> datetime | None:
                """Parse a datetime string and return a datetime object."""
                if date_string is None:
                    return None
                parsed_date = datetime.strptime(date_string, "%Y%m%dT%H%M%SZ")
                return parsed_date.replace(tzinfo=dt_util.UTC)

            @staticmethod
            def __datetime_to_string(date: datetime | None) -> str:
                """Convert datetime object to API format string."""
                if date is None:
                    return ""
                return date.strftime("%Y%m%dT%H%M%SZ")

            @staticmethod
            def __parse_duration(duration_string: str | None) -> timedelta | None:
                """Parse a duration string and return a timedelta object."""
                if duration_string is None:
                    return None
                parts = duration_string.split(":")
                hours = int(parts[0])
                minutes = int(parts[1])
                if "." in parts[2]:
                    seconds, microseconds = map(float, parts[2].split("."))
                    microseconds = int(
                        f"{microseconds:.6f}".replace(".", "").ljust(6, "0")[:6]
                    )
                else:
                    seconds = float(parts[2])
                    microseconds = 0
                return timedelta(
                    hours=hours,
                    minutes=minutes,
                    seconds=seconds,
                    microseconds=microseconds,
                )

            @staticmethod
            def __duration_to_string(duration: timedelta | None) -> str:
                """Convert timedelta object to API format string."""
                if duration is None:
                    return ""
                total_seconds = duration.total_seconds()
                hours = total_seconds / 3600
                minutes = (total_seconds % 3600) / 60
                seconds = total_seconds % 60
                return f"{hours:02.0f}:{minutes:02.0f}:{seconds:02.0f}"

            @staticmethod
            def __truncate_error_message(message: str, max_length: int = 255) -> str:
                """Truncate error message to fit within the character limit."""
                truncation_indicator = "... [truncated]"
                available_length = max_length - len(truncation_indicator)

                if len(message) <= available_length:
                    return message

                words = message.split()
                truncated = ""
                for word in words:
                    if len(truncated + word) <= available_length:
                        truncated += word + " "
                    else:
                        break

                return truncated.strip() + truncation_indicator

        @dataclass
        class TargetURL:
            """Represents the components of a target URL for a Duplicati backup."""

            scheme: str
            host: str
            port: int | None
            path: str
            username: str | None
            password: str | None
            ssh_fingerprint: str | None
            query_params: dict = field(default_factory=dict)

            _scheme_ports = {
                "http": 80,
                "https": 443,
                "ftp": 21,
                "sftp": 22,
                "ssh": 22,
                "scp": 22,
                "rsync": 873,
                "webdav": 80,
                "webdavs": 443,
                "s3": 443,
                "smb": 445,
                "cifs": 445,
                "afp": 548,
            }

            FIELD_MAPPING = {
                "scheme": "scheme",
                "host": "hostname",
                "port": "port",
                "path": "path",
                "username": "auth-username",
                "password": "auth-password",
                "ssh_fingerprint": "ssh-fingerprint",
                "query_params": "query_params",
            }

            @classmethod
            def from_url(cls, url: str):
                """Parse the target URL and create TargetURLComponents instance."""

                # Handle local file paths
                if "://" not in url:
                    # Local file path case
                    return cls(
                        scheme="file",
                        host="localhost",
                        port=None,
                        path=url,
                        username=None,
                        password=None,
                        ssh_fingerprint=None,
                    )

                parsed_url = urlparse(url)
                query_params = parse_qs(parsed_url.query)

                converted_data = {}
                for cls_field, url_field in cls.FIELD_MAPPING.items():
                    if cls_field == "host":
                        value = parsed_url.hostname or "localhost"
                    elif cls_field == "port":
                        # Handle file:// URLs and missing ports
                        if parsed_url.scheme == "file":
                            value = None
                        else:
                            value = parsed_url.port or cls._scheme_ports.get(
                                parsed_url.scheme, None
                            )
                    elif cls_field == "path":
                        value = unquote(parsed_url.path)
                    elif cls_field in ["username", "password", "ssh_fingerprint"]:
                        value = query_params.get(url_field, [None])[0]
                    elif cls_field == "query_params":
                        value = {
                            k: v[0]
                            for k, v in query_params.items()
                            if k
                            not in ["auth-username", "auth-password", "ssh-fingerprint"]
                        }
                    else:
                        value = getattr(parsed_url, url_field)
                    converted_data[cls_field] = value

                return cls(**converted_data)

            def reconstruct_url(self) -> str:
                """Reconstruct the target URL from its components."""

                # Handle local file paths
                if self.scheme == "file":
                    return self.path

                # Handle network URLs
                url = f"{self.scheme}://{self.host}"

                if self.port is not None and self.port != self._scheme_ports.get(
                    self.scheme, None
                ):
                    url += f":{self.port}"

                url += quote(self.path)

                query_parts = []
                if self.username:
                    query_parts.append(f"auth-username={quote(self.username)}")
                if self.password:
                    query_parts.append(f"auth-password={quote(self.password)}")
                if self.ssh_fingerprint:
                    query_parts.append(f"ssh-fingerprint={quote(self.ssh_fingerprint)}")

                for key, value in self.query_params.items():
                    query_parts.append(f"{quote(key)}={quote(value)}")

                if query_parts:
                    url += "?" + "&".join(query_parts)

                return url

        id: str
        name: str
        metadata: Metadata
        description: str | None
        target_url: TargetURL

        FIELD_MAPPING = {
            "id": "ID",
            "name": "Name",
            "metadata": "Metadata",
            "description": "Description",
            "target_url": "TargetURL",
        }

        @classmethod
        def from_dict(cls, data: dict):
            """Create a DuplicatiBackup instance from a dictionary."""
            converted_data = {}
            for cls_field, api_field in cls.FIELD_MAPPING.items():
                value = data.get(api_field)
                if cls_field == "metadata":
                    value = cls.Metadata.from_dict(value) if value is not None else None
                    if not isinstance(value, cls.Metadata):
                        raise TypeError("Metadata must be a Metadata instance")
                elif cls_field == "target_url":
                    value = cls.TargetURL.from_url(value) if value is not None else None
                    if not isinstance(value, cls.TargetURL):
                        raise TypeError(
                            "Target URL must be a TargetURLComponents instance"
                        )
                converted_data[cls_field] = value
            return cls(**converted_data)

        def to_dict(self) -> dict:
            """Convert the DuplicatiBackup object to its dictionary representation."""
            result = {}
            for cls_field, api_field in self.FIELD_MAPPING.items():
                value = getattr(self, cls_field)
                if cls_field == "metadata":
                    value = value.to_dict()
                elif cls_field == "target_url":
                    value = value.reconstruct_url()
                result[api_field] = value
            return result

    @dataclass
    class Schedule:
        """Represents a schedule for a Duplicati backup."""

        schedule_id: int
        tags: list[str]
        time: datetime | None
        repeat: str
        last_run: datetime | None
        rule: str
        allowed_days: str | None

        FIELD_MAPPING = {
            "schedule_id": "ID",
            "tags": "Tags",
            "time": "Time",
            "repeat": "Repeat",
            "last_run": "LastRun",
            "rule": "Rule",
            "allowed_days": "AllowedDays",
        }

        @classmethod
        def from_dict(cls, data: dict):
            """Create Schedule instance from API response."""
            if data is None:
                raise ValueError("Cannot create Schedule instance from None data")
            converted_data = {}
            for cls_field, api_field in cls.FIELD_MAPPING.items():
                value = data.get(api_field, [] if cls_field == "tags" else "")
                if cls_field == "schedule_id":
                    value = int(value)
                    if not isinstance(value, int):
                        raise TypeError("Schedule ID must be an integer")
                elif cls_field in ["time", "last_run"]:
                    value = cls.__parse_datetime(value) if value is not None else None
                    if value and not isinstance(value, (datetime, type(None))):
                        raise TypeError(f"Field {cls_field} must be datetime or None")
                converted_data[cls_field] = value
            return cls(**converted_data)

        def to_dict(self) -> dict:
            """Convert Schedule instance to API response format."""
            result = {}
            for cls_field, api_field in self.FIELD_MAPPING.items():
                value = getattr(self, cls_field)
                if cls_field in ["time", "last_run"]:
                    value = self.__datetime_to_string(value) if value else ""
                result[api_field] = value
            return result

        @staticmethod
        def __parse_datetime(date_string: str) -> datetime | None:
            """Parse a datetime string and return a datetime object."""
            if not date_string:
                return None
            parsed_date = datetime.strptime(date_string, "%Y-%m-%dT%H:%M:%SZ")
            return parsed_date.replace(tzinfo=dt_util.UTC)

        @staticmethod
        def __datetime_to_string(date: datetime) -> str:
            """Convert datetime object to API format string."""
            if date is None:
                return ""
            return date.strftime("%Y-%m-%dT%H:%M:%SZ")

    backup: Backup
    schedule: Schedule | None

    FIELD_MAPPING = {"backup": "Backup", "schedule": "Schedule"}

    @classmethod
    def from_dict(cls, data: dict):
        """Create BackupDefinition instance from API response."""
        converted_data = {}
        if "data" in data:
            data = data["data"]
        for cls_field, api_field in cls.FIELD_MAPPING.items():
            value = data.get(api_field)
            if cls_field == "backup":
                value = cls.Backup.from_dict(value) if value is not None else None
                if not isinstance(value, cls.Backup):
                    raise TypeError("Backup must be a Backup instance")
            elif cls_field == "schedule":
                value = cls.Schedule.from_dict(value) if value is not None else None
            converted_data[cls_field] = value
        return cls(**converted_data)

    def to_dict(self) -> dict:
        """Convert BackupDefinition instance to API response format."""
        result = {}
        for cls_field, api_field in self.FIELD_MAPPING.items():
            value = getattr(self, cls_field)
            if value:
                result[api_field] = value.to_dict()
        return result


@dataclass
class BackupProgress:
    """Represents the progress state of a Duplicati backup operation."""

    backup_id: str
    task_id: int
    backend_action: str
    backend_path: str | None
    backend_file_size: int
    backend_file_progress: int
    backend_speed: int
    backend_is_blocking: bool
    current_filename: str | None
    current_filesize: int
    current_fileoffset: int
    current_filecomplete: bool
    phase: str
    overall_progress: float
    processed_file_count: int
    processed_file_size: int
    total_file_count: int
    total_file_size: int
    still_counting: bool

    FIELD_MAPPING = {
        "backup_id": "BackupID",
        "task_id": "TaskID",
        "backend_action": "BackendAction",
        "backend_path": "BackendPath",
        "backend_file_size": "BackendFileSize",
        "backend_file_progress": "BackendFileProgress",
        "backend_speed": "BackendSpeed",
        "backend_is_blocking": "BackendIsBlocking",
        "current_filename": "CurrentFilename",
        "current_filesize": "CurrentFilesize",
        "current_fileoffset": "CurrentFileoffset",
        "current_filecomplete": "CurrentFilecomplete",
        "phase": "Phase",
        "overall_progress": "OverallProgress",
        "processed_file_count": "ProcessedFileCount",
        "processed_file_size": "ProcessedFileSize",
        "total_file_count": "TotalFileCount",
        "total_file_size": "TotalFileSize",
        "still_counting": "StillCounting",
    }

    @classmethod
    def from_dict(cls, data: dict):
        """Create ProgressState instance from API response."""
        converted_data = {}
        if "data" in data:
            data = data["data"]
        for cls_field, api_field in cls.FIELD_MAPPING.items():
            if cls_field in [
                "task_id",
                "backend_file_size",
                "backend_file_progress",
                "backend_speed",
                "current_filesize",
                "current_fileoffset",
                "processed_file_count",
                "processed_file_size",
                "total_file_count",
                "total_file_size",
            ]:
                value = data.get(api_field, 0)
                value = int(value)
            elif cls_field in [
                "backend_is_blocking",
                "current_filecomplete",
                "still_counting",
            ]:
                value = data.get(api_field, False)
                value = bool(value)
            elif cls_field == "overall_progress":
                value = data.get(api_field, 0.0)
                value = float(value)
            elif cls_field in ["current_filename", "backend_path"]:
                value = data.get(api_field)
            else:
                value = data.get(api_field, "")
            converted_data[cls_field] = value
        return cls(**converted_data)

    def to_dict(self) -> dict:
        """Convert ProgressState instance to API response format."""
        return {
            api_field: getattr(self, field)
            for field, api_field in self.FIELD_MAPPING.items()
        }


@dataclass
class ApiError:
    """Represents an error response from the Duplicati API."""

    msg: str
    code: int

    FIELD_MAPPING = {
        "msg": "Error",
        "code": "Code",
    }

    @classmethod
    def from_dict(cls, data: dict):
        """Create ApiResponseError instance from API response."""
        converted_data = {}
        for cls_field, api_field in cls.FIELD_MAPPING.items():
            if cls_field == "code":
                value = data.get(api_field, 0)
                value = int(value)
            else:
                value = data.get(api_field, "")
            converted_data[cls_field] = value
        return cls(**converted_data)

    def to_dict(self) -> dict:
        """Convert ApiResponseError instance to API response format."""
        return {
            api_field: getattr(self, field)
            for field, api_field in self.FIELD_MAPPING.items()
        }


@dataclass
class ApiResponse:
    """Represents a response from the Duplicati API."""

    success: bool
    data: Any | ApiError
