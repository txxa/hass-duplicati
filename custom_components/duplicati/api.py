"""REST API for Duplicati backup software."""

import logging
import re
import urllib.parse

from homeassistant.exceptions import HomeAssistantError

from custom_components.duplicati.http_client import HttpClient

from .auth_interface import DuplicatiAuthStrategy
from .model import BackupDefinition, BackupProgress
from .rest_interface import RestApiInterface

_LOGGER = logging.getLogger(__name__)


class ApiResponseError(HomeAssistantError):
    """Error to indicate a processing error during an API request."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication during an API request."""


class DuplicatiBackendAPI(RestApiInterface):
    """API wrapper for interacting with Duplicati backend."""

    def __init__(
        self,
        base_url: str,
        verify_ssl: bool,
        password: str,
        auth_strategy: DuplicatiAuthStrategy,
        http_client: HttpClient | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialize the Duplicati backend API."""
        self.http_client = http_client
        super().__init__(base_url, verify_ssl, timeout, http_client)
        self.password = password
        self.parsed_base_url = urllib.parse.urlparse(self.base_url)
        self.auth_strategy = auth_strategy

    def set_auth_strategy(self, auth_strategy: DuplicatiAuthStrategy) -> None:
        """Set the authentication strategy."""
        self.auth_strategy = auth_strategy

    def get_api_host(self) -> str:
        """Return the host (including port) from the base URL."""
        return self.parsed_base_url.netloc

    def validate_backup_id(self, backup_id: str) -> bool:
        """Validate backup ID format."""
        return bool(re.match(r"\d+", backup_id))

    def _prepare_url(self, endpoint: str) -> str:
        """Prepare full URL from endpoint."""
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    async def _ensure_authentication(self) -> None:
        """Prepare request with authentication."""
        if not self.auth_strategy:
            raise RuntimeError("Authentication strategy not set")
        if not self.auth_strategy.is_auth_valid():
            _LOGGER.debug("Authentication required, performing authentication")
            await self.auth_strategy.authenticate(self.password)
        # Add authentication headers to client's headers
        auth_headers = self.auth_strategy.get_auth_headers()
        if self.http_client and auth_headers:
            self.http_client.add_headers(auth_headers)

    async def __handle_api_error(self, error: Exception, url: str) -> None:
        """Handle API specific errors."""
        if "login" in url:
            raise InvalidAuth("Authentication failed") from error

    async def get_backup(self, backup_id: str) -> BackupDefinition:
        """Get the information of a backup by ID."""
        if not self.validate_backup_id(backup_id):
            raise ValueError("Invalid backup ID format")

        try:
            response = await self.get(f"api/v1/backup/{backup_id}")
            return BackupDefinition.from_dict(response.body)
        except Exception as error:  # noqa: BLE001
            await self.__handle_api_error(error, f"api/v1/backup/{backup_id}")
            raise
        else:
            return response.body

    async def create_backup(self, backup_id: str) -> dict:
        """Create a new backup by ID."""
        if not self.validate_backup_id(backup_id):
            raise ValueError("Invalid backup ID format")

        progress_state = await self.get_progress_state()
        if progress_state.phase not in {"No active backup", "Backup_Complete", "Error"}:
            raise RuntimeError("The backup process is currently already running")

        try:
            response = await self.post(f"api/v1/backup/{backup_id}/run")
        except Exception as error:  # noqa: BLE001
            await self.__handle_api_error(error, f"api/v1/backup/{backup_id}/run")
            raise
        else:
            return response.body

    async def update_backup(self, backup_id: str, data: dict) -> dict:
        """Update the configuration of a backup by ID."""
        if not self.validate_backup_id(backup_id):
            raise ValueError("Invalid backup ID format")
        if not data:
            raise ValueError("No data provided for the update")

        try:
            response = await self.put(f"api/v1/backup/{backup_id}", data)
        except Exception as error:  # noqa: BLE001
            await self.__handle_api_error(error, f"api/v1/backup/{backup_id}")
            raise
        else:
            return response.body

    async def delete_backup(self, backup_id: str) -> dict:
        """Delete the configuration of a backup by ID."""
        if not self.validate_backup_id(backup_id):
            raise ValueError("Invalid backup ID format")

        try:
            response = await self.delete(f"api/v1/backup/{backup_id}")
        except Exception as error:  # noqa: BLE001
            await self.__handle_api_error(error, f"api/v1/backup/{backup_id}")
            raise
        else:
            return response.body

    async def get_backups(self) -> list[BackupDefinition]:
        """Get a list of all backups."""
        try:
            response = await self.get("api/v1/backups")
            return [BackupDefinition.from_dict(backup) for backup in response.body]
        except Exception as error:  # noqa: BLE001
            await self.__handle_api_error(error, "api/v1/backups")
            raise

    async def get_progress_state(self) -> BackupProgress:
        """Get the current progress state of the backup process."""
        try:
            response = await self.get("api/v1/progressstate")
            return BackupProgress.from_dict(response.body)
        except Exception as error:  # noqa: BLE001
            await self.__handle_api_error(error, "api/v1/progressstate")
            raise

    async def get_system_info(self) -> dict:
        """Get system information."""
        try:
            response = await self.get("api/v1/systeminfo")
        except Exception as error:  # noqa: BLE001
            await self.__handle_api_error(error, "api/v1/systeminfo")
            raise
        else:
            return response.body
