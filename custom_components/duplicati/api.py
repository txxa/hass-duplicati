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
        _LOGGER.debug("Extracting cookie: %s", cookie_name)
        for cookie in cookies:
            if cookie.startswith(f"{cookie_name}="):
                value = cookie.split(";")[0].split("=")[1]
                value = urllib.parse.unquote(value)
                _LOGGER.debug("Found cookie %s with value: %s", cookie_name, value)
                return value
        _LOGGER.debug("Cookie %s not found", cookie_name)
        return None

    def __extract_cookie_expiration(
        self, response: aiohttp.ClientResponse, cookie_name: str
    ) -> str | None:
        """Extract the expiration of a specific cookie from the response."""
        cookies = response.headers.getall("Set-Cookie", [])
        _LOGGER.debug("Extracting expiration for cookie: %s", cookie_name)
        for cookie in cookies:
            if cookie.startswith(f"{cookie_name}="):
                expiration = re.search(r"expires=([^;]+)", cookie)
                if expiration:
                    expires = expiration.group(1)
                    try:
                        expires_date = datetime.strptime(
                            expires, "%a, %d %b %Y %H:%M:%S %Z"
                        )
                        value = expires_date.strftime("%s")
                    except ValueError as e:
                        _LOGGER.error("Failed to parse cookie expiration date: %s", e)
                        return ""
                    else:
                        _LOGGER.debug(
                            "Found expiration for cookie %s: %s", cookie_name, value
                        )
                        return value
        _LOGGER.debug("Expiration for cookie %s not found", cookie_name)
        return None

    def __extract_xsrf_token(self, response: aiohttp.ClientResponse) -> None:
        """Extract the XSRF-Token from the response headers."""
        xsrf_token = self.__extract_cookie(response, "xsrf-token")
        if xsrf_token:
            self.xsrf_token = xsrf_token
            self.xsrf_token_expiration = self.__extract_cookie_expiration(
                response, "xsrf-token"
            )

    def __extract_session_nonce(self, response: aiohttp.ClientResponse) -> None:
        """Extract the session-nonce from the response headers."""
        session_nonce = self.__extract_cookie(response, "session-nonce")
        if session_nonce:
            self.session_nonce = session_nonce
            self.session_nonce_expiration = self.__extract_cookie_expiration(
                response, "session-nonce"
            )

    def __extract_session_auth(self, response: aiohttp.ClientResponse) -> None:
        """Extract the session-auth from the response headers."""
        session_auth = self.__extract_cookie(response, "session-auth")
        if session_auth:
            self.session_auth = session_auth
            self.session_auth_expiration = self.__extract_cookie_expiration(
                response, "session-auth"
            )

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
        method = HTTPMethod.GET
        response = await self.__make_request(session, method, self.base_url)
        if response.status == HTTPStatus.OK.value:
            self.__extract_xsrf_token(response)
            return response
        # Handle missing XSRF token
        _LOGGER.warning("Failed to retrieve XSRF token: %s", response.status)
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

        # Ensure authentication if enabled
        if self.password:
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
                response = await self.login(session, self.password)
                # if response.status == HTTPStatus.
            if self.session_auth:
                cookies.append(f"session-auth={self.session_auth}")
            if self.session_nonce:
                cookies.append(f"session-nonce={self.session_nonce}")
            if len(cookies) > 0:
                headers["Cookie"] = "; ".join(cookies)

        # Ensure that XSRF token is used if available
        if not self.xsrf_token or (
            self.xsrf_token_expiration and self.xsrf_token_expiration <= now
        ):
            response = await self.__get_xsrf_token(session)
            if "login" in response.url.name:
                raise InvalidAuth("No password provided")
        if self.xsrf_token:
            headers["X-XSRF-Token"] = self.xsrf_token

        # Make the API request
        response = await self.__make_request(
            session, method, url, headers=headers, data=data
        )
        if response.status == HTTPStatus.UNAUTHORIZED:
            raise InvalidAuth("Incorrect password provided")
        if "login" in response.url.name:
            raise InvalidAuth("No password provided")

        # Handle missing XSRF token
        if (
            response.status == HTTPStatus.BAD_REQUEST.value
            and response.reason
            and "Missing XSRF Token" in response.reason
            and retry_on_missing_token
        ):
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
        return await self.__parse_json_response(response)

    def get_api_host(self):
        """Return the host (including port) from the base URL."""
        return self.parsed_base_url.netloc

    async def login(
        self, session: aiohttp.ClientSession, password: str
    ) -> aiohttp.ClientResponse:
        """Login to Duplicati using the cryptographic method."""
        try:
            # Step 1: Get the nonce and salt
            method = HTTPMethod.POST
            url = f"{self.base_url}/login.cgi"
            data = {"get-nonce": 1}
            response = await self.__make_request(session, method, url, data=data)
            # Handle nonce response errors
            if response.status != HTTPStatus.OK.value:
                raise ApiResponseError("Failed to retrieve nonce and salt")
            # Extract xsrf-token cookie
            self.__extract_xsrf_token(response)
            # Extract session-nonce cookie
            self.__extract_session_nonce(response)
            # Parse the JSON response
            nonce_json = await self.__parse_json_response(response)

            # Step 2: Calculate the salted and nonced password
            salt = base64.b64decode(nonce_json["Salt"])
            nonce = base64.b64decode(nonce_json["Nonce"])
            salted_pwd = hashlib.sha256(password.encode("utf-8") + salt).hexdigest()
            nonced_pwd = base64.b64encode(
                hashlib.sha256(nonce + bytes.fromhex(salted_pwd)).digest()
            ).decode("utf-8")

            # Step 3: Send the login request with the nonced password
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
                _LOGGER.debug("Authentication failed: Incorrect password provided")
                raise InvalidAuth("Incorrect password provided")
            if login_response.status != HTTPStatus.OK.value:
                raise ApiResponseError("Unknown error occured")
            # Extract xsrf-token cookie
            self.__extract_xsrf_token(login_response)
            # Extract session-auth cookie
            self.__extract_session_auth(login_response)
        # Handle other errors
        except ApiResponseError as e:
            _LOGGER.debug(
                "Authentication failed: Unknown error occured (code=%s, reason=%s, method=%s, url=%s)",
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
            raise CannotConnect(f"Authentication failed: {e!s}") from e
        else:
            _LOGGER.debug("Login successful")
            return login_response

    async def get_backup(self, backup_id: str) -> dict:
        """Get the information of a backup by ID."""
        try:
            # Validate backup ID
            if not re.match(r"\d+", backup_id):
                raise ValueError("Invalid backup ID format")
            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}"
                return await self.__make_api_request(session, HTTPMethod.GET, endpoint)
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Getting the information of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}
        except ValueError as e:
            _LOGGER.debug(
                "Getting the information of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}

    async def create_backup(self, backup_id: str) -> dict:
        """Create a new backup by ID."""
        # Validate backup ID
        if not re.match(r"\d+", backup_id):
            raise ValueError("The provided backup ID has an invalid format")
        try:
            resp = await self.get_progress_state()
            progress_state = ""
            if "Error" in resp:
                progress_state = resp["Error"]
            if "Phase" in resp:
                progress_state = resp["Phase"]
            if progress_state in {"No active backup", "Backup_Complete", "Error"}:
                async with aiohttp.ClientSession() as session:
                    endpoint = f"/api/v1/Backup/{backup_id}/run"
                    response = await self.__make_api_request(
                        session, HTTPMethod.POST, endpoint
                    )
                    _LOGGER.debug(
                        "Request to start backup process for backup with ID '%s' sent to Duplicati backend",
                        backup_id,
                    )
                    return response
            else:
                raise RuntimeError("The backup process is currently already running")
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Starting the backup process for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}
        except RuntimeError as e:
            _LOGGER.debug(
                "Starting the backup process for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}
        except ValueError as e:
            _LOGGER.debug(
                "Starting the backup process for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}

    async def update_backup(self, backup_id: str, data: dict) -> dict:
        """Update the configuration of a backup by ID."""
        try:
            # Validate backup ID
            if not re.match(r"\d+", backup_id):
                raise ValueError("Invalid backup ID format")
            # Validate data
            if len(data) == 0:
                raise ValueError("No data provided for the update")
            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}"
                return await self.__make_api_request(
                    session, "PUT", endpoint, data=data
                )
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Updating the configuration for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}
        except ValueError as e:
            _LOGGER.debug(
                "Updating the configuration for backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}

    async def delete_backup(self, backup_id: str) -> dict:
        """Delete the configuration of a backup by ID."""
        try:
            # Validate backup ID
            if not re.match(r"\d+", backup_id):
                raise ValueError("Invalid backup ID format")
            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}"
                return await self.__make_api_request(
                    session, HTTPMethod.DELETE, endpoint
                )
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Deleting the configuration of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}
        except ValueError as e:
            _LOGGER.debug(
                "Deleting the configuration of backup with ID '%s' failed: %s",
                backup_id,
                str(e),
            )
            return {"Error": str(e)}

    async def list_backups(self) -> dict:
        """Get a list of all backups."""
        try:
            async with aiohttp.ClientSession() as session:
                endpoint = "/api/v1/Backups"
                return await self.__make_api_request(session, HTTPMethod.GET, endpoint)
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Listing the configured backups failed: %s",
                str(e),
            )
            return {"Error": str(e)}

    async def get_progress_state(self) -> dict:
        """Get the current progress state of the backup process."""
        try:
            async with aiohttp.ClientSession() as session:
                endpoint = "/api/v1/ProgressState"
                return await self.__make_api_request(session, HTTPMethod.GET, endpoint)
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Getting the current progress state failed: %s",
                str(e),
            )
            return {"Error": str(e)}

    async def get_system_info(self) -> dict:
        """Get system information."""
        try:
            async with aiohttp.ClientSession() as session:
                endpoint = "/api/v1/SystemInfo"
                return await self.__make_api_request(session, HTTPMethod.GET, endpoint)
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Getting the system information of the Duplicati backend server failed: %s",
                str(e),
            )
            return {"Error": str(e)}
