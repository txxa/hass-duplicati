"""Authentication interface for Duplicati backend."""

from abc import ABC, abstractmethod

from .http_client import HttpResponse


class DuplicatiAuthStrategy(ABC):
    """Base class for authentication strategies."""

    def __init__(self):
        """Initialize authentication strategy."""

    @abstractmethod
    def is_auth_valid(self) -> bool:
        """Check if current auth is still valid."""

    @abstractmethod
    async def authenticate(self, password: str) -> HttpResponse:
        """Authenticate with backend."""

    @abstractmethod
    def get_auth_headers(self) -> dict:
        """Get headers needed for authenticated requests."""
