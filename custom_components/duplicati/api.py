"""REST API for Duplicati backup software."""

import base64
import hashlib
import json
import logging
import re
import urllib.parse
from datetime import datetime
from http import HTTPMethod, HTTPStatus

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from yarl import URL

from .model import BackupDefinition, BackupProgress

_LOGGER = logging.getLogger(__name__)


class ApiResponseError(HomeAssistantError):
    """Error to indicate a processing error during an API request."""


class CannotConnect(HomeAssistantError):
    """Error to indicate a connection error during an API request."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication during an API request."""


class DuplicatiBackendAPI:
    """API wrapper for interacting with Duplicati backend."""

    def __init__(self, base_url: str, verify_ssl: bool, password=None) -> None:
        """Initialize the Duplicati backend API."""
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        self.password = None
        if password is not None:
            self.password = str(password)
        self.parsed_base_url = urllib.parse.urlparse(self.base_url)
        self.xsrf_token: str | None = None
        self.xsrf_token_expiration: str | None = None
        self.session_nonce: str | None = None
        self.session_nonce_expiration: str | None = None
        self.session_auth: str | None = None
        self.session_auth_expiration: str | None = None

    def __extract_cookie(
        self, response: aiohttp.ClientResponse, cookie_name: str
    ) -> str | None:
        """Extract a specific cookie from the response."""
        cookies = response.headers.getall("Set-Cookie", [])
        try:
            for cookie in cookies:
                if cookie.startswith(f"{cookie_name}="):
                    value = cookie.split(";")[0].split("=")[1]
                    return urllib.parse.unquote(value)
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "Extraction of value for cookie '%s' failed: %s", cookie_name, e
            )
        return None

    def __extract_cookie_expiration(
        self, response: aiohttp.ClientResponse, cookie_name: str
    ) -> str | None:
        """Extract the expiration of a specific cookie from the response."""
        cookies = response.headers.getall("Set-Cookie", [])
        try:
            for cookie in cookies:
                if cookie.startswith(f"{cookie_name}="):
                    expiration = re.search(r"expires=([^;]+)", cookie)
                    if expiration:
                        expires = expiration.group(1)
                        expires_date = datetime.strptime(
                            expires, "%a, %d %b %Y %H:%M:%S %Z"
                        )
                        return expires_date.strftime("%s")
        except Exception as e:  # noqa: BLE001
            _LOGGER.error(
                "Extraction of expiration date for cookie '%s' failed: %s",
                cookie_name,
                e,
            )
        return None

    def __extract_xsrf_token(self, response: aiohttp.ClientResponse) -> bool:
        """Extract the XSRF-Token from the response headers."""
        xsrf_token = self.__extract_cookie(response, "xsrf-token")
        if xsrf_token and xsrf_token != self.xsrf_token:
            self.xsrf_token = xsrf_token
            self.xsrf_token_expiration = self.__extract_cookie_expiration(
                response, "xsrf-token"
            )
            return True
        return False

    def __extract_session_nonce(self, response: aiohttp.ClientResponse) -> bool:
        """Extract the session-nonce from the response headers."""
        session_nonce = self.__extract_cookie(response, "session-nonce")
        if session_nonce and session_nonce != self.session_nonce:
            self.session_nonce = session_nonce
            self.session_nonce_expiration = self.__extract_cookie_expiration(
                response, "session-nonce"
            )
            return True
        return False

    def __extract_session_auth(self, response: aiohttp.ClientResponse) -> bool:
        """Extract the session-auth from the response headers."""
        session_auth = self.__extract_cookie(response, "session-auth")
        if session_auth and session_auth != self.session_auth:
            self.session_auth = session_auth
            self.session_auth_expiration = self.__extract_cookie_expiration(
                response, "session-auth"
            )
            return True
        return False

    async def __parse_json_response(self, response: aiohttp.ClientResponse) -> dict:
        """Parse JSON response."""
        try:
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                response_text = await response.text()
                # Strip UTF-8 BOM if present
                response_text = response_text.lstrip("\ufeff")
                # Create JSON object (deserialize the JSON text)
                return json.loads(response_text)
            raise ValueError("Response content type is not JSON")
        except json.JSONDecodeError as e:
            raise ValueError("Error decoding response JSON") from e

    async def __make_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict | None = None,
        data: dict | None = None,
    ) -> aiohttp.ClientResponse:
        """Make an HTTP request."""
        return await session.request(
            method, url, headers=headers, data=data, verify_ssl=self.verify_ssl
        )

    async def __get_xsrf_token(
        self, session: aiohttp.ClientSession
    ) -> aiohttp.ClientResponse:
        """Retrieve XSRF token from server cookies."""
        # Get XSRF token
        _LOGGER.debug("XSRF token - Initiating token retrieval")
        method = HTTPMethod.GET
        _LOGGER.debug(
            "XSRF token - Sending token request: %s %s", method, self.base_url
        )
        response = await self.__make_request(session, method, self.base_url)
        _LOGGER.debug(
            "XSRF token - Response of token request: %s %s",
            response.status,
            response.reason,
        )
        if response.status == HTTPStatus.OK.value:
            if self.__extract_xsrf_token(response):
                _LOGGER.debug(
                    "XSRF token - Token successfully extracted: %s", self.xsrf_token
                )
                return response

        # Handle missing XSRF token
        _LOGGER.debug("XSRF token - Response headers: %s", response.headers)
        _LOGGER.error("XSRF token - Failed to retrieve token")
        url = URL(self.base_url)
        reqeust_info = aiohttp.RequestInfo(
            method=method, url=url, headers=response.headers, real_url=url
        )
        raise aiohttp.ClientResponseError(
            request_info=reqeust_info,
            history=response.history,
            status=response.status,
            message="Failed to retrieve XSRF token",
            headers=response.headers,
        )

    async def __do_login(
        self, session: aiohttp.ClientSession, password: str
    ) -> aiohttp.ClientResponse:
        """Login to Duplicati using the cryptographic method."""
        try:
            _LOGGER.debug("Login - Initiating authentication")
            # Step 1: Get the nonce and salt
            method = HTTPMethod.POST
            url = f"{self.base_url}/login.cgi"
            data = {"get-nonce": 1}
            _LOGGER.debug("Login - Sending nonce request to get nonce and salt")
            response = await self.__make_request(session, method, url, data=data)
            _LOGGER.debug(
                "Login - Response of nonce request: %s %s",
                response.status,
                response.reason,
            )
            # Handle nonce request errors
            if response.status != HTTPStatus.OK.value:
                _LOGGER.error("Login - Failed to retrieve nonce and salt")
                raise ApiResponseError("Failed to retrieve nonce and salt")
            # Extract xsrf-token cookie
            if self.__extract_xsrf_token(response):
                _LOGGER.debug(
                    "Login - XSRF token successfully extracted: %s", self.xsrf_token
                )
            # Extract session-nonce cookie
            if self.__extract_session_nonce(response):
                _LOGGER.debug(
                    "Login - Session nonce successfully extracted: %s",
                    self.session_nonce,
                )
            # Parse the JSON response
            nonce_json = await self.__parse_json_response(response)
            _LOGGER.debug("Login - Nonce response successfully parsed")

            # Step 2: Calculate the salted and nonced password
            _LOGGER.debug("Login - Calculating salted and nonced password")
            salt = base64.b64decode(nonce_json["Salt"])
            nonce = base64.b64decode(nonce_json["Nonce"])
            salted_pwd = hashlib.sha256(password.encode("utf-8") + salt).hexdigest()
            nonced_pwd = base64.b64encode(
                hashlib.sha256(nonce + bytes.fromhex(salted_pwd)).digest()
            ).decode("utf-8")

            # Step 3: Send the login request with the nonced password
            _LOGGER.debug("Login - Sending login request with nonced password")
            method = HTTPMethod.POST
            url = f"{self.base_url}/login.cgi"
            data = {"password": nonced_pwd}
            # Add the xsrf-token and session-nonce cookie to the headers
            headers = {}
            if self.xsrf_token:
                headers["X-XSRF-Token"] = self.xsrf_token
            if self.session_nonce:
                headers["Cookie"] = f"session-nonce={self.session_nonce}"
            # Send the login request with the nonced password
            login_response = await self.__make_request(
                session, method, url, headers=headers, data=data
            )
            # Handle login response errors
            if login_response.status == HTTPStatus.UNAUTHORIZED.value:
                _LOGGER.error(
                    "Login - Authentication on server '%s' failed: Incorrect password provided",
                    self.get_api_host(),
                )
                raise InvalidAuth("Incorrect password provided")
            if login_response.status != HTTPStatus.OK.value:
                _LOGGER.error("Login - Unknown error occurred during login")
                raise ApiResponseError("Unknown error occured")
            _LOGGER.debug(
                "Login - Response of login request: %s %s",
                login_response.status,
                login_response.reason,
            )
            # Extract xsrf-token cookie
            if self.__extract_xsrf_token(login_response):
                _LOGGER.debug(
                    "Login - New XSRF token successfully extracted: %s", self.xsrf_token
                )
            # Extract session-auth cookie
            if self.__extract_session_auth(login_response):
                _LOGGER.debug(
                    "Login - Session auth successfully extracted: %s", self.session_auth
                )
        # Handle other errors
        except ApiResponseError as e:
            _LOGGER.error(
                "Login - Authentication failed: Unknown error occured (code=%s, reason=%s, method=%s, url=%s)",
                response.status,
                response.reason,
                method,
                url,
            )
            url = URL(url)
            request_info = aiohttp.RequestInfo(
                method=method,
                url=url,
                headers=response.headers,
                real_url=url,
            )
            raise aiohttp.ClientResponseError(
                request_info=request_info,
                history=response.history,
                status=response.status,
                message=str(e),
                headers=response.headers,
            ) from e
        except aiohttp.ClientError as e:
            _LOGGER.error(
                "Login - Authentication on server '%s' failed: %s",
                self.get_api_host(),
                str(e),
            )
            raise CannotConnect(
                f"Authentication on server '{self.get_api_host()}' failed: {e!s}"
            ) from e
        else:
            _LOGGER.debug("Login - Authentication successful")
            return login_response

    async def __make_api_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        endpoint: str,
        headers: dict | None = None,
        data: dict | None = None,
        retry_on_missing_token: bool = True,
    ) -> dict:
        """Make an API request to the Duplicati backend."""
        headers = headers or {}
        url = self.base_url + endpoint
        now = dt_util.utcnow().strftime("%s")
        cookies = []

        _LOGGER.debug("API call - Starting request for endpoint %s", endpoint)

        # Ensure authentication if enabled
        if self.password:
            _LOGGER.debug("API call - Authentication is enabled")
            if (
                not self.session_auth
                or (
                    self.session_auth_expiration and self.session_auth_expiration <= now
                )
                or not self.session_nonce
                or (
                    self.session_nonce_expiration
                    and self.session_nonce_expiration <= now
                )
            ):
                _LOGGER.debug("API call - Session expired or missing, trying to login")
                response = await self.__do_login(session, self.password)
            if self.session_auth:
                cookies.append(f"session-auth={self.session_auth}")
            if self.session_nonce:
                cookies.append(f"session-nonce={self.session_nonce}")
            if len(cookies) > 0:
                headers["Cookie"] = "; ".join(cookies)
        else:
            _LOGGER.debug("API call - Authentication is disabled")

        # Ensure that XSRF token is used if available
        if not self.xsrf_token or (
            self.xsrf_token_expiration and self.xsrf_token_expiration <= now
        ):
            _LOGGER.debug(
                "API call - XSRF token missing or expired, trying to retrieve new token"
            )
            response = await self.__get_xsrf_token(session)
            if "login" in response.url.name:
                _LOGGER.error(
                    "API call - Redirected to login page, no password provided"
                )
                raise InvalidAuth("No password provided")
        if self.xsrf_token:
            headers["X-XSRF-Token"] = self.xsrf_token

        # Make the API request
        _LOGGER.debug("API call - Sending API request: %s %s", method, url)
        _LOGGER.debug("API call - API request headers: %s", headers)
        _LOGGER.debug("API call - API request data: %s", data)
        response = await self.__make_request(
            session, method, url, headers=headers, data=data
        )
        _LOGGER.debug(
            "API call - Response of API request: %s %s",
            response.status,
            response.reason,
        )

        if response.status == HTTPStatus.UNAUTHORIZED:
            _LOGGER.error("API call - Unauthorized: Incorrect password provided")
            raise InvalidAuth("Incorrect password provided")
        if "login" in response.url.name:
            _LOGGER.error("API call - Redirected to login page, no password provided")
            raise InvalidAuth("No password provided")

        # Handle missing XSRF token
        if (
            response.status == HTTPStatus.BAD_REQUEST.value
            and response.reason
            and "Missing XSRF Token" in response.reason
            and retry_on_missing_token
        ):
            _LOGGER.debug(
                "API call - Missing XSRF Token, trying to retrieve new token and retrying request"
            )
            await self.__get_xsrf_token(session)
            if self.xsrf_token:
                headers["X-XSRF-Token"] = self.xsrf_token
            return await self.__make_api_request(
                session,
                method,
                endpoint,
                headers,
                data,
                retry_on_missing_token=False,
            )
        # Parse and return JSON response
        _LOGGER.debug("API call - Parsing JSON body and returning data dict")
        return await self.__parse_json_response(response)

    def get_api_host(self):
        """Return the host (including port) from the base URL."""
        return self.parsed_base_url.netloc

    def validate_backup_id(self, backup_id: str) -> bool:
        """Validate backup ID format."""
        return bool(re.match(r"\d+", backup_id))

    async def get_backup(self, backup_id: str) -> BackupDefinition:
        """Get the information of a backup by ID."""
        try:
            # Validate input
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")
            # Make the API request
            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}"
                response = await self.__make_api_request(
                    session, HTTPMethod.GET, endpoint
                )
                if "Error" in response:
                    raise ApiResponseError(response["Error"])
                return BackupDefinition.from_dict(response["data"])
        except (ValueError, ApiResponseError) as e:
            _LOGGER.debug(
                "Getting the information of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise

    async def create_backup(self, backup_id: str) -> dict:
        """Create a new backup by ID."""
        try:
            # Validate input
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")
            # Check if backup process is already running
            progress_state = await self.get_progress_state()
            if progress_state.phase not in {
                "No active backup",
                "Backup_Complete",
                "Error",
            }:
                raise RuntimeError("The backup process is currently already running")
            # Make the API request
            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}/run"
                response = await self.__make_api_request(
                    session, HTTPMethod.POST, endpoint
                )
                if "Error" in response:
                    raise ApiResponseError(response["Error"])
                _LOGGER.debug(
                    "Request to start backup process for backup with ID '%s' sent to Duplicati backend",
                    backup_id,
                )
                return response
        except (ValueError, RuntimeError, ApiResponseError) as e:
            _LOGGER.debug(
                "Starting the backup process for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise

    async def update_backup(self, backup_id: str, data: dict) -> dict:
        """Update the configuration of a backup by ID."""
        try:
            # Validate input
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")
            if len(data) == 0:
                raise ValueError("No data provided for the update")

            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}"
                response = await self.__make_api_request(
                    session, "PUT", endpoint, data=data
                )
                if "Error" in response:
                    raise ApiResponseError(response["Error"])
                return response
        except (ValueError, ApiResponseError) as e:
            _LOGGER.debug(
                "Updating the configuration for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise

    async def delete_backup(self, backup_id: str) -> dict:
        """Delete the configuration of a backup by ID."""
        try:
            # Validate input
            if not self.validate_backup_id(backup_id):
                raise ValueError("Invalid backup ID format")

            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}"
                response = await self.__make_api_request(
                    session, HTTPMethod.DELETE, endpoint
                )
                if "Error" in response:
                    raise ApiResponseError(response["Error"])
                return response
        except (ValueError, ApiResponseError) as e:
            _LOGGER.debug(
                "Deleting the configuration of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            raise

    async def list_backups(self) -> list[BackupDefinition]:
        """Get a list of all backups."""
        try:
            async with aiohttp.ClientSession() as session:
                endpoint = "/api/v1/Backups"
                response = await self.__make_api_request(
                    session, HTTPMethod.GET, endpoint
                )
                if "Error" in response:
                    raise ApiResponseError(response["Error"])
                return [
                    BackupDefinition.from_dict(backup_definition)
                    for backup_definition in response
                ]
        except ApiResponseError as e:
            _LOGGER.debug("Listing the configured backups failed: %s", str(e))
            raise

    async def get_progress_state(self) -> BackupProgress:
        """Get the current progress state of the backup process."""
        try:
            async with aiohttp.ClientSession() as session:
                endpoint = "/api/v1/ProgressState"
                response = await self.__make_api_request(
                    session, HTTPMethod.GET, endpoint
                )
                if "Error" in response:
                    raise ApiResponseError(response["Error"])
                return BackupProgress.from_dict(response)
        except ApiResponseError as e:
            _LOGGER.debug("Getting the current progress state failed: %s", str(e))
            raise

    async def get_system_info(self) -> dict:
        """Get system information."""
        try:
            async with aiohttp.ClientSession() as session:
                endpoint = "/api/v1/SystemInfo"
                response = await self.__make_api_request(
                    session, HTTPMethod.GET, endpoint
                )
                if "Error" in response:
                    raise ApiResponseError(response["Error"])
                return response
        except ApiResponseError as e:
            _LOGGER.debug(
                "Getting the system information of the Duplicati backend server failed: %s",
                str(e),
            )
            raise
