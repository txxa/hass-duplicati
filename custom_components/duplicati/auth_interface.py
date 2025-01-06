"""Authentication interface for Duplicati backend."""

import logging
from abc import ABC, abstractmethod
from http import HTTPStatus

import aiohttp
from homeassistant.exceptions import HomeAssistantError

from .http_client import HttpResponse

_LOGGER = logging.getLogger(__name__)


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication during login."""


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

    def handle_login_errors(self, login_response, server, url):
        """Handle login response errors."""
        if login_response.status == HTTPStatus.UNAUTHORIZED.value:
            _LOGGER.error(
                "Login - Authentication on server '%s' failed: Incorrect password provided",
                server,
            )
            raise InvalidAuth("Incorrect password provided")
        if login_response.status != HTTPStatus.OK.value:
            _LOGGER.error(
                "Login - Unknown error occurred during login (code=%s, reason=%s, method=%s, url=%s)",
                login_response.status,
                login_response.reason,
                login_response.request_info["method"],
                url,
            )
            request_info = aiohttp.RequestInfo(
                method=login_response.request_info["method"],
                url=login_response.request_info["url"],
                headers=HttpResponse.convert_headers(
                    login_response.request_info["headers"]
                ),
                real_url=login_response.request_info["real_url"],
            )
            raise aiohttp.ClientResponseError(
                request_info=request_info,
                history=login_response.history,
                status=login_response.status,
                message="Unknown error occurred during login",
                headers=login_response.headers,
            )
