"""REST API for Duplicati backup software."""

import logging
import re
import urllib.parse

from homeassistant.exceptions import HomeAssistantError

from .auth_interface import DuplicatiAuthStrategy
from .http_client import HttpClient, HttpResponse
from .model import ApiError, ApiResponse, BackupDefinition, BackupProgress
from .rest_interface import RestApiInterface

_LOGGER = logging.getLogger(__name__)


class ApiProcessingError(HomeAssistantError):
    """Error to indicate a processing error during an API request."""


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

    def __handle_api_response_error(self, response: HttpResponse) -> ApiResponse | None:
        """Handle API specific errors."""
        if not response.body:
            raise ApiProcessingError("No response body")
        if "Error" in response.body:
            return ApiResponse(success=False, data=ApiError.from_dict(response.body))

    async def is_backup_running(self) -> bool:
        """Check if a backup process is currently running."""
        response = await self.get_progress_state()

        if isinstance(response.data, ApiError):
            message = response.data.msg
        elif isinstance(response.data, BackupProgress):
            message = response.data.phase
        else:
            raise ApiProcessingError("Unknown progress state")

        return message not in {
            "No active backup",
            "Backup_Complete",
            "Error",
            "",
        }

    async def get_backup(self, backup_id: str) -> ApiResponse:
        """Get the information of a backup by ID."""
        try:
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")
            response = await self.get(f"api/v1/backup/{backup_id}")
            self.__handle_api_response_error(response)
            api_response = ApiResponse(
                success=True, data=BackupDefinition.from_dict(response.body)
            )
        except (ValueError, ApiProcessingError) as e:
            _LOGGER.debug(
                "Getting the information of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise
        else:
            return api_response

    async def create_backup(self, backup_id: str) -> ApiResponse:
        """Create a new backup by ID."""
        try:
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")
            if await self.is_backup_running():
                raise RuntimeError("The backup process is currently already running")
            response = await self.post(f"api/v1/backup/{backup_id}/run")
            self.__handle_api_response_error(response)
            api_response = ApiResponse(success=True, data=response.body)
        except (ValueError, RuntimeError, ApiProcessingError) as e:
            _LOGGER.debug(
                "Starting the backup process for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise
        else:
            _LOGGER.debug(
                "Request to start backup process for backup with ID '%s' sent to Duplicati backend",
                backup_id,
            )
            return api_response

    async def update_backup(self, backup_id: str, data: dict) -> ApiResponse:
        """Update the configuration of a backup by ID."""
        try:
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")
            if not data:
                raise ValueError("No data provided for the update")
            response = await self.put(f"api/v1/backup/{backup_id}", data)
            self.__handle_api_response_error(response)
            api_response = ApiResponse(success=True, data=response.body)
        except (ValueError, ApiProcessingError) as e:
            _LOGGER.debug(
                "Updating the configuration for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise
        else:
            return api_response

    async def delete_backup(self, backup_id: str) -> ApiResponse:
        """Delete the configuration of a backup by ID."""
        try:
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")
            response = await self.delete(f"api/v1/backup/{backup_id}")
            self.__handle_api_response_error(response)
            api_response = ApiResponse(success=True, data=response.body)
        except (ValueError, ApiProcessingError) as e:
            _LOGGER.debug(
                "Deleting the configuration of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise
        else:
            return api_response

    async def get_backups(self) -> ApiResponse:
        """Get a list of all backups."""
        try:
            response = await self.get("api/v1/backups")
            self.__handle_api_response_error(response)
            api_response = ApiResponse(
                success=True,
                data=[BackupDefinition.from_dict(backup) for backup in response.body],
            )
        except ApiProcessingError as e:
            _LOGGER.debug("Listing the configured backups failed: %s", str(e))
            raise
        else:
            return api_response

    async def get_progress_state(self) -> ApiResponse:
        """Get the current progress state of the backup process."""
        try:
            response = await self.get("api/v1/progressstate")
            self.__handle_api_response_error(response)
            api_response = ApiResponse(
                success=True, data=BackupProgress.from_dict(response.body)
            )
        except ApiProcessingError as e:
            _LOGGER.debug("Getting the current progress state failed: %s", str(e))
            raise
        else:
            return api_response

    async def get_system_info(self) -> ApiResponse:
        """Get system information."""
        try:
            response = await self.get("api/v1/systeminfo")
            self.__handle_api_response_error(response)
            api_response = ApiResponse(success=True, data=response.body)
        except ApiProcessingError as e:
            _LOGGER.debug(
                "Getting the system information of the Duplicati backend server failed: %s",
                str(e),
            )
            raise
        else:
            return api_response
