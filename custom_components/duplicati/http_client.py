"""Generic HTTP client."""

import json
import logging
import urllib.parse
import weakref
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

_LOGGER = logging.getLogger(__name__)


class CannotConnect(HomeAssistantError):
    """Error to indicate a connection error during an HTTP request."""


@dataclass
class StoredCookie:
    """Cookie storage with all relevant attributes."""

    value: str
    expires: float | None = None
    path: str = "/"
    domain: str | None = None
    secure: bool = False
    http_only: bool = False

    @property
    def expires_str(self) -> str | None:
        """Return the expiration date as a string in GMT."""
        if self.expires:
            return datetime.strftime(
                datetime.fromtimestamp(self.expires),
                "%a, %d %b %Y %H:%M:%S GMT",
            )
        return None


@dataclass
class HttpResponse:
    """Custom response class containing response data."""

    status: int
    headers: dict
    body: Any
    cookies: dict
    url: str
    content_type: str
    content_length: int | None
    reason: str | None
    charset: str | None
    request_info: dict
    elapsed: float  # Time taken for request/response cycle
    history: tuple
    real_url: str  # Final URL after redirects
    redirects: int

    @staticmethod
    def convert_headers(headers: dict) -> CIMultiDictProxy[str]:
        """Convert headers dict to CIMultiDictProxy."""
        ci_headers = CIMultiDict()
        for key, value in headers.items():
            ci_headers.add(key, value)
        return CIMultiDictProxy(ci_headers)


class CookieManager:
    """Cookie manager to store and update cookies."""

    def __init__(self):
        """Initialize the cookie manager."""
        self.stored_cookies = {}

    def extract_and_update_cookies(self, response: aiohttp.ClientResponse) -> None:
        """Update cookies and special header values from response."""
        current_time = dt_util.utcnow().timestamp()

        for cookie in response.cookies.values():
            # Extract cookie expiration
            expires_str = cookie.get("expires")
            if expires_str:
                try:
                    expires_dt = datetime.strptime(
                        expires_str, "%a, %d %b %Y %H:%M:%S %Z"
                    )
                    expires_ts = expires_dt.timestamp()
                except ValueError:
                    _LOGGER.debug(
                        "Cookie manager - Could not parse expires date %s of cookie %s",
                        expires_str,
                        cookie.key,
                    )
            else:
                expires_ts = None
                _LOGGER.debug(
                    "Cookie manager - Cookie %s does not have an expires date",
                    cookie.key,
                )

            # Create a new StoredCookie object
            cookie_data = StoredCookie(
                value=urllib.parse.unquote(cookie.value),
                expires=expires_ts,  # None for session cookies
                path=cookie.get("path", "/"),
                domain=cookie.get("domain"),
                secure=bool(cookie.get("secure")),
                http_only=bool(cookie.get("httponly")),
            )

            # Check if the cookie is being set with an expiration date in the past
            if cookie_data.expires is not None and cookie_data.expires < current_time:
                _LOGGER.debug(
                    "Cookie manager - Removing expired cookie: %s", cookie.key
                )
                self.stored_cookies.pop(cookie.key, None)
                continue

            # Check if the cookie is new or has changed
            stored_cookie = self.stored_cookies.get(cookie.key)
            if (
                not stored_cookie
                or stored_cookie.value != cookie_data.value
                or stored_cookie.expires != cookie_data.expires
            ):
                self.stored_cookies[cookie.key] = cookie_data
                _LOGGER.debug(
                    "Cookie manager - Stored cookie '%s': value=%s, expires=%s, path=%s, domain=%s, secure=%s http_only=%s",
                    cookie.key,
                    cookie_data.value,
                    cookie_data.expires_str,
                    cookie_data.path,
                    cookie_data.domain,
                    cookie_data.secure,
                    cookie_data.http_only,
                )

    def remove_expired_cookies(self) -> None:
        """Remove expired cookies from the store."""
        current_time = dt_util.utcnow().timestamp()
        expired_cookies = [
            key
            for key, cookie in self.stored_cookies.items()
            if cookie.expires is not None and cookie.expires < current_time
        ]
        for key in expired_cookies:
            _LOGGER.debug(
                "Cookie manager - Removing expired cookie before sending: %s", key
            )
            self.stored_cookies.pop(key)

    def get_valid_cookies(self, url: str) -> dict:
        """Get valid cookies for the outgoing request."""
        return {
            key: cookie
            for key, cookie in self.stored_cookies.items()
            if self.cookie_matches_request(cookie, URL(url))
        }

    def cookie_matches_request(self, cookie: StoredCookie, url: URL) -> bool:
        """Check if a cookie matches the request's domain, path, and security requirements."""
        # Check domain
        if cookie.domain and url.host and not url.host.endswith(cookie.domain):
            return False
        # Check path
        if not url.path.startswith(cookie.path):
            return False
        # Check secure
        if cookie.secure and url.scheme != "https":
            return False
        return True


class HttpClient:
    """Handle HTTP operations with cookie and header support."""

    CONTENT_TYPE_JSON = "application/json"
    CONTENT_TYPE_FORM = "application/x-www-form-urlencoded"
    CONTENT_TYPE_TEXT = "text/plain"
    CONTENT_TYPE_HTML = "text/html"

    COOKIE_TO_HEADER_MAP = {"xsrf-token": "X-XSRF-Token"}

    def __init__(
        self,
        verify_ssl: bool,
        timeout: int = 30,
    ) -> None:
        """Initialize the HTTP client."""
        self.headers = {}
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.cookie_manager = CookieManager()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        # Register cleanup callback
        weakref.finalize(self, self.__cleanup_session)

    def __cleanup_session(self) -> None:
        """Clean up resources synchronously."""
        if self._session and not self._session.closed and self._session.connector:
            self._session.connector.close()

    def __prepare_request_headers(self, url: str, headers: dict | None = None) -> dict:
        """Prepare request headers (cookies and special headers)."""
        final_headers = headers or {}
        final_headers.update(self.headers)
        # Handle cookies
        cookie_string = ""
        for key, cookie in self.cookie_manager.get_valid_cookies(url).items():
            # Add cookie
            if cookie_string:
                cookie_string += "; "
            cookie_string += f"{key}={cookie.value}"
            # Handle cookie based special headers
            if key in self.COOKIE_TO_HEADER_MAP:
                header_name = self.COOKIE_TO_HEADER_MAP[key]
                final_headers[header_name] = cookie.value
        # Add cookies header
        if cookie_string:
            final_headers["Cookie"] = cookie_string
        return final_headers

    def __prepare_request_data(
        self, data: Any, headers: dict, content_type: str = CONTENT_TYPE_JSON
    ) -> str | None:
        """Prepare request data and set content type header."""
        if data is not None:
            # Add content type header
            headers["Content-Type"] = content_type
            # Add data
            if content_type == self.CONTENT_TYPE_JSON:
                return json.dumps(data)
            if content_type == self.CONTENT_TYPE_FORM:
                return urllib.parse.urlencode(data)
        return None

    def __create_http_response(
        self,
        response: aiohttp.ClientResponse,
        parsed_body: Any,
        start_time: float,
        redirect_count: int = 0,
    ) -> HttpResponse:
        """Create an HTTP response object."""
        return HttpResponse(
            status=response.status,
            headers=dict(response.headers),
            body=parsed_body,
            cookies=dict(response.cookies),
            url=str(response.url),
            content_type=response.headers.get("Content-Type", ""),
            content_length=response.content_length,
            reason=response.reason,
            charset=response.charset,
            request_info={
                "method": response.request_info.method,
                "url": response.request_info.url,
                "headers": response.request_info.headers,
                "real_url": response.request_info.real_url,
            },
            elapsed=dt_util.utcnow().timestamp() - start_time,
            history=response.history,
            real_url=str(response.real_url),
            redirects=redirect_count,
        )

    def __truncate_http_data(self, data: str, max_length: int = 1000) -> str:
        r"""Truncate data if it is too large and replace new lines with \n."""
        data = data.replace("\n", "\\n").replace("\r", "\\r")
        if data and len(data) > max_length:
            return data[:max_length] + "... [truncated]"
        return data

    def __log_request(
        self,
        method: str,
        url: str,
        headers: dict | None = None,
        data: Any = None,
    ) -> None:
        """Log the request details."""
        _LOGGER.debug(
            "Request - Line: %s %s HTTP/%s.%s",
            method,
            url,
            self._session.version[0],
            self._session.version[1],
        )
        _LOGGER.debug("Request - Headers: %s", headers)
        if not data:
            data = ""
        _LOGGER.debug("Request - Data: %s", self.__truncate_http_data(str(data)))

    def __log_response(self, response: HttpResponse) -> None:
        """Log the response details."""
        _LOGGER.debug(
            "Response - Line: HTTP/%s.%s %s %s",
            self._session.version[0],
            self._session.version[1],
            response.status,
            response.reason,
        )
        _LOGGER.debug("Response - Headers: %s", response.headers)
        body = response.body
        if not body:
            body = ""
        _LOGGER.debug("Response - Data: %s", self.__truncate_http_data(str(body)))

    def add_headers(self, headers: dict) -> None:
        """Add headers to the session."""
        self.headers.update(headers)

    async def parse_response_body(self, response: aiohttp.ClientResponse) -> Any:
        """Parse response based on content type."""
        content_type = response.headers.get("Content-Type", "")
        response_text = await response.text()

        if not response_text:
            _LOGGER.debug(
                "Response - Empty response body for content type: %s", content_type
            )
            return None

        try:
            if self.CONTENT_TYPE_JSON in content_type:
                response_text = response_text.lstrip("\ufeff")  # Strip UTF-8 BOM
                return json.loads(response_text)
            if self.CONTENT_TYPE_TEXT in content_type:
                return response_text
            if self.CONTENT_TYPE_HTML in content_type:
                return response_text
            if self.CONTENT_TYPE_FORM in content_type:
                return dict(urllib.parse.parse_qsl(response_text))
        except (json.JSONDecodeError, ValueError) as e:
            _LOGGER.error(
                "Response - Failed to parse response for content type %s: %s",
                content_type,
                e,
            )
            return None
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "Response - Unexpected error for content type %s: %s", content_type, e
            )
            return None
        else:
            _LOGGER.debug(
                "Response - Returning raw response for content type: %s", content_type
            )
            return response_text

    async def make_request(
        self,
        method: str,
        url: str,
        headers: dict | None = None,
        data: Any = None,
        content_type: str = CONTENT_TYPE_JSON,
        redirect_count: int = 0,
    ) -> HttpResponse:
        """Make HTTP request."""

        try:
            start_time = dt_util.utcnow().timestamp()
            headers = self.__prepare_request_headers(url, headers)
            headers.update(self.headers)
            data = self.__prepare_request_data(data, headers, content_type)
            self.__log_request(method, url, headers, data)

            # Allow redirects but process each response
            async with self._session.request(
                method=method,
                url=url,
                headers=headers,
                data=data,
                verify_ssl=self.verify_ssl,
                allow_redirects=False,
            ) as response:
                # Handle response
                parsed_body = await self.parse_response_body(response)
                http_response = self.__create_http_response(
                    response, parsed_body, start_time, redirect_count
                )
                self.__log_response(http_response)
                self.cookie_manager.extract_and_update_cookies(response)
                self.cookie_manager.remove_expired_cookies()

                # Handle redirects
                if response.status in (301, 302, 303, 307, 308):
                    # Get redirect URL and resolve it if relative
                    url = str(URL(response.url).join(URL(response.headers["Location"])))
                    response = await self.make_request(
                        method=method,
                        url=url,
                        headers=headers,
                        data=data,
                        redirect_count=redirect_count + 1,
                    )
        except aiohttp.ClientError as e:
            _LOGGER.error("Request - HTTP request failed: %s", e)
            raise CannotConnect(f"Request failed: {e}") from e
        else:
            return http_response
