"""Base class for REST API implementations."""

from abc import ABC, abstractmethod
from http import HTTPMethod

from .http_client import HttpClient, HttpResponse


class RestApiInterface(ABC):
    """Base class providing REST API operations."""

    def __init__(
        self,
        base_url: str,
        verify_ssl: bool,
        timeout: int = 30,
        http_client: HttpClient | None = None,
    ) -> None:
        """Initialize the REST API interface."""
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        if http_client:
            self.http_client = http_client
        else:
            self.http_client = HttpClient(verify_ssl, timeout)

    @abstractmethod
    async def _ensure_authentication(self) -> None:
        """Prepare request (authentication, headers etc.)."""

    @abstractmethod
    def _prepare_url(self, endpoint: str) -> str:
        """Prepare full URL from endpoint."""

    @abstractmethod
    def get_api_host(self) -> str:
        """Get the API host."""

    async def get(self, endpoint: str, headers: dict = {}) -> HttpResponse:
        """Perform GET request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(HTTPMethod.GET, url, headers=headers)

    async def post(
        self, endpoint: str, headers: dict = {}, data: dict | None = None
    ) -> HttpResponse:
        """Perform POST request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.POST, url, headers=headers, data=data
        )

    async def put(
        self, endpoint: str, headers: dict = {}, data: dict | None = None
    ) -> HttpResponse:
        """Perform PUT request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.PUT, url, headers=headers, data=data
        )

    async def patch(
        self, endpoint: str, headers: dict = {}, data: dict | None = None
    ) -> HttpResponse:
        """Perform PATCH request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.PATCH, url, headers=headers, data=data
        )

    async def delete(self, endpoint: str, headers: dict = {}) -> HttpResponse:
        """Perform DELETE request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.DELETE, url, headers=headers
        )

    async def head(self, endpoint: str, headers: dict = {}) -> HttpResponse:
        """Perform HEAD request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.HEAD, url, headers=headers
        )

    async def options(self, endpoint: str, headers: dict = {}) -> HttpResponse:
        """Perform OPTIONS request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.OPTIONS, url, headers=headers
        )

    async def trace(self, endpoint: str, headers: dict = {}) -> HttpResponse:
        """Perform TRACE request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.TRACE, url, headers=headers
        )

    async def connect(self, endpoint: str, headers: dict = {}) -> HttpResponse:
        """Perform CONNECT request."""
        await self._ensure_authentication()
        url = self._prepare_url(endpoint)
        return await self.http_client.make_request(
            HTTPMethod.CONNECT, url, headers=headers
        )
