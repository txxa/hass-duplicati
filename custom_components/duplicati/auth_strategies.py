"""Authentication strategies for Duplicati backend."""

import base64
import binascii
import hashlib
import json
import logging
import urllib.parse
from http import HTTPMethod, HTTPStatus
from typing import Any

import aiohttp
import jwt
from homeassistant.util import dt as dt_util

from .api import ApiResponseError, InvalidAuth
from .auth_interface import DuplicatiAuthStrategy
from .http_client import HttpClient, HttpResponse

_LOGGER = logging.getLogger(__name__)


class CookieAuthStrategy(DuplicatiAuthStrategy):
    """Cookie-based authentication strategy for Duplicati."""

    def __init__(
        self,
        base_url: str,
        verify_ssl: bool = False,
        timeout: int = 30,
        http_client: HttpClient | None = None,
    ):
        """Initialize the CookieAuthStrategy."""
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        if http_client:
            self.http_client = http_client
        else:
            self.http_client = HttpClient(verify_ssl, timeout)

    async def authenticate(
        self,
        password: str,
    ) -> HttpResponse:
        """Login to Duplicati using cookie-based authentication."""
        _LOGGER.debug("Login - Initiating cookie authentication")

        # Step 1: Ensure XSRF token is available and valid
        await self.__ensure_xsrf_token()

        # Step 2: Get the nonce and salt
        url = f"{self.base_url}/login.cgi"
        data = {"get-nonce": 1}
        server = urllib.parse.urlparse(url).netloc
        _LOGGER.debug("Login - Sending init request to get nonce and salt")
        nonce_response = await self.http_client.make_request(
            HTTPMethod.POST,
            url,
            data=data,
            content_type=HttpClient.CONTENT_TYPE_FORM,
        )
        _LOGGER.debug("Login - Nonce and salt successfully retrieved")
        if nonce_response.status != HTTPStatus.OK.value:
            _LOGGER.error("Login - Failed to retrieve nonce and salt")
            raise ApiResponseError("Failed to retrieve nonce and salt")

        # Step 3: Calculate the salted and nonced password
        _LOGGER.debug("Login - Calculating salted and nonced password")
        salt = base64.b64decode(nonce_response.body["Salt"])
        nonce = base64.b64decode(nonce_response.body["Nonce"])
        salted_pwd = hashlib.sha256(password.encode("utf-8") + salt).hexdigest()
        nonced_pwd = base64.b64encode(
            hashlib.sha256(nonce + bytes.fromhex(salted_pwd)).digest()
        ).decode("utf-8")

        # Step 4: Send login request with nonced password
        _LOGGER.debug("Login - Sending login request with nonced password")
        login_response = await self.http_client.make_request(
            HTTPMethod.POST,
            url,
            data={"password": nonced_pwd},
            content_type=HttpClient.CONTENT_TYPE_FORM,
        )

        # Handle login errors
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
                HTTPMethod.POST,
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

        _LOGGER.debug(
            "Login - Cookie authentication with nonced and salted password successful"
        )
        return login_response

    def get_auth_headers(self) -> dict:
        """Get headers needed for authenticated requests."""
        return {}

    def is_auth_valid(self) -> bool:
        """Check if current cookie auth is still valid."""

        return self.__is_session_auth_valid()

    async def __ensure_xsrf_token(self) -> None:
        """Ensure that XSRF token is used if available."""

        if not self.__is_xsrf_token_valid():
            _LOGGER.debug("XSRF token missing or expired, trying to retrieve new token")
            # Get XSRF token
            _LOGGER.debug("XSRF token - Initiating token retrieval")
            method = HTTPMethod.GET
            response = await self.http_client.make_request(method, self.base_url)
            # Handle missing XSRF token
            if not self.__is_xsrf_token_valid():
                _LOGGER.error("XSRF token - Failed to retrieve token")
                request_info = aiohttp.RequestInfo(
                    method=response.request_info["method"],
                    url=response.request_info["url"],
                    headers=HttpResponse.convert_headers(
                        response.request_info["headers"]
                    ),
                    real_url=response.request_info["real_url"],
                )
                raise aiohttp.ClientResponseError(
                    request_info=request_info,
                    history=response.history,
                    status=response.status,
                    message="Failed to retrieve XSRF token",
                    headers=response.headers,
                )

        xsrf_token = self.http_client.cookie_manager.stored_cookies.get("xsrf-token")
        if xsrf_token:
            _LOGGER.debug(
                "XSRF token - Token successfully retrieved: %s",
                xsrf_token.value,
            )

    def __is_xsrf_token_valid(self) -> bool:
        """Check if XSRF token cookie exists and is valid."""
        now = dt_util.utcnow().timestamp()
        xsrf_cookie = self.http_client.cookie_manager.stored_cookies.get("xsrf-token")
        return bool(
            xsrf_cookie
            and xsrf_cookie.value
            and xsrf_cookie.expires
            and xsrf_cookie.expires > now
        )

    def __is_session_auth_valid(self) -> bool:
        """Check if session auth is valid."""
        now = dt_util.utcnow().timestamp()
        session_auth = self.http_client.cookie_manager.stored_cookies.get(
            "session-auth"
        )
        return bool(
            session_auth
            and session_auth.value
            and session_auth.expires
            and session_auth.expires > now
        )


class JWTAuthStrategy(DuplicatiAuthStrategy):
    """Authentication strategy using JSON Web Tokens."""

    def __init__(
        self,
        base_url: str,
        verify_ssl: bool = False,
        timeout: int = 30,
        http_client: HttpClient | None = None,
    ):
        """Initialize the JWTAuthStrategy."""
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        if http_client:
            self.http_client = http_client
        else:
            self.http_client = HttpClient(verify_ssl, timeout)
        self.access_token = None

    async def authenticate(self, password: str) -> None:
        """Login to Duplicati using JWT authentication."""
        _LOGGER.debug("Login - Initiating JWT authentication")

        url = f"{self.base_url}/api/v1/auth/login"
        data = {"Password": password}
        server = urllib.parse.urlparse(url).netloc

        # Get access token
        _LOGGER.debug("Login - Sending login request to get access token")
        login_response = await self.http_client.make_request(
            HTTPMethod.POST,
            url,
            data=data,
            content_type=HttpClient.CONTENT_TYPE_JSON,
        )

        # Handle login errors
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
                HTTPMethod.POST,
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

        access_token = login_response.body.get("AccessToken")
        if not access_token:
            _LOGGER.error("Login - Failed to extract the access token")
            raise ValueError("Failed to extract the access token")
        self.access_token = access_token
        _LOGGER.debug("Login - Access token successfully extracted: %s", access_token)
        _LOGGER.debug("Login - JWT authentication successful")

    def get_auth_headers(self) -> dict:
        """Get headers needed for authenticated requests."""
        return (
            {"Authorization": f"Bearer {self.access_token}"}
            if self.access_token
            else {}
        )

    def is_auth_valid(self) -> bool:
        """Check if current JWT auth is still valid."""
        if not self.access_token:
            _LOGGER.debug("JWT validation - No access token available")
            return False

        try:
            _LOGGER.debug("JWT validation - Parsing JWT token")
            decoded = self.__parse_jwt(self.access_token)
            payload = json.loads(decoded["payload"])

            if not isinstance(payload, dict):
                _LOGGER.debug("JWT validation - Invalid payload format")
                raise jwt.DecodeError("Invalid payload string: must be a json object")
            if "exp" not in payload:
                _LOGGER.debug("JWT validation - No expiration claim found in token")
                raise jwt.MissingRequiredClaimError("exp")

            now = dt_util.utcnow().timestamp()
            exp = int(payload["exp"])
            is_valid = exp > now
            _LOGGER.debug(
                "JWT validation - Token expiration check: %s",
                "valid" if is_valid else "expired",
            )

        except (ValueError, jwt.InvalidTokenError) as e:
            _LOGGER.debug("JWT validation - Token validation failed: %s", str(e))
            return False
        else:
            return is_valid

    def __parse_jwt(self, token: str | bytes) -> dict[str, Any]:
        """Parse and validate JWT token structure."""
        if isinstance(token, str):
            token = token.encode("utf-8")

        if not isinstance(token, bytes):
            raise jwt.DecodeError(f"Invalid token type. Token must be a {bytes}")

        try:
            signing_input, crypto_segment = token.rsplit(b".", 1)
            header_segment, payload_segment = signing_input.split(b".", 1)
        except ValueError as err:
            raise jwt.DecodeError("Not enough segments") from err

        try:
            decoded_token = jwt.api_jws.decode_complete(
                token, options={"verify_signature": False}
            )
            header = decoded_token["header"]
            payload = decoded_token["payload"]
            signature = decoded_token["signature"]
        except (TypeError, binascii.Error) as err:
            raise jwt.DecodeError("Invalid token format") from err

        if not isinstance(header, dict):
            raise jwt.DecodeError("Invalid header string: must be a json object")

        return {
            "payload": payload,
            "header": header,
            "signature": signature,
        }
