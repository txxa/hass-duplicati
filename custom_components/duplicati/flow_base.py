"""Base flow handler for Duplicati integration."""

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    SelectOptionDict,
)

from .api import DuplicatiBackendAPI
from .model import BackupDefinition


class DuplicatiFlowHandlerBase:
    """Base class for Duplicati flow handlers."""

    api: DuplicatiBackendAPI

    def _validate_backup_definitions(
        self, backup_definitions: list[BackupDefinition]
    ) -> None:
        """Validate backups."""
        if len(backup_definitions) == 0:
            raise BackupsError(
                f"No backups found for server '{self.api.get_api_host()}'"
            )

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
