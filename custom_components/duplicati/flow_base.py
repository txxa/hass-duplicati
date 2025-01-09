"""Base flow handler for Duplicati integration."""

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectOptionDict,
)

from .api import ApiProcessingError, DuplicatiBackendAPI
from .model import ApiError, ApiResponse, BackupDefinition


class DuplicatiFlowHandlerBase:
    """Base class for Duplicati flow handlers."""

    api: DuplicatiBackendAPI

    def _validate_backup_definitions(
        self, response: ApiResponse
    ) -> list[BackupDefinition]:
        """Validate backups."""
        if isinstance(response.data, ApiError):
            raise ApiProcessingError(response.data.msg)
        if not isinstance(response.data, list) or not isinstance(
            response.data[0], BackupDefinition
        ):
            raise ApiProcessingError(f"Unexpected response from API: {response.data}")
        if len(response.data) == 0:
            raise BackupsError(
                f"No backups found for server '{self.api.get_api_host()}'"
            )
        return response.data

    def _get_backup_select_options_list(
        self, backups: dict[str, str]
    ) -> list[SelectOptionDict]:
        """Return a dictionary of available backup names."""
        return [
            SelectOptionDict(
                label=value,
                value=key,
            )
            for key, value in backups.items()
        ]


class BackupsError(HomeAssistantError):
    """Error to indicate there is an error with backups."""
