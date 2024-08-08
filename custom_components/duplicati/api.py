"""REST API for Duplicati backup software."""

import json
import logging
import re
import urllib.parse
from datetime import datetime
from http import HTTPStatus

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from yarl import URL

# # Set log level for aiohttp
# aiohttp_logger = logging.getLogger("aiohttp")
# aiohttp_logger.setLevel(logging.DEBUG)

# # Add a console handler for aiohttp logging
# console_handler = logging.StreamHandler()
# console_handler.setLevel(logging.DEBUG)
# aiohttp_logger.addHandler(console_handler)

_LOGGER = logging.getLogger(__name__)


class ApiResponseError(HomeAssistantError):
    """Error to indicate a processing error during an API request."""


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class DuplicatiBackendAPI:
    """API wrapper for interacting with Duplicati backend."""

    def __init__(self, base_url: str, verify_ssl: bool, password=None) -> None:
        """Initialize the Duplicati backend API."""
        self.base_url = base_url
        self.verify_ssl = verify_ssl
        if password is not None:
            self.password = str(password)
        self.parsed_base_url = urllib.parse.urlparse(self.base_url)
        self.xsrf_token: str | None = None
        self.expires: str | None = None

    async def _get_xsrf_token(self, session: aiohttp.ClientSession) -> None:
        """Retrieve XSRF token from server cookies."""
        response = await self._make_request(session, "GET", self.base_url)
        if response.status == HTTPStatus.OK.value:
            self._extract_xsrf_token(response)
        else:
            _LOGGER.warning("Failed to retrieve XSRF token: %s", response.status)
            url = URL(self.base_url)
            reqeust_info = aiohttp.RequestInfo(
                method="GET", url=url, headers=response.headers, real_url=url
            )
            raise aiohttp.ClientResponseError(
                request_info=reqeust_info,
                history=response.history,
                status=response.status,
                message="Failed to retrieve XSRF token",
                headers=response.headers,
            )

    def _extract_xsrf_token(self, response: aiohttp.ClientResponse) -> None:
        """Extract XSRF token from Set-Cookie header."""
        set_cookie_header = response.headers.get("Set-Cookie")
        if set_cookie_header:
            cookies = {}
            for cookie_str in set_cookie_header.split(";"):
                cookie_parts = cookie_str.strip().split("=")
                if len(cookie_parts) == 2:
                    key, value = cookie_parts
                    cookies[key.strip()] = value.strip()
            # Extract XSRF token from cookies
            xsrf_token = urllib.parse.unquote(cookies.get("xsrf-token", ""))
            if xsrf_token:
                self.xsrf_token = xsrf_token
                _LOGGER.debug("XSRF token obtained successfully")
            else:
                _LOGGER.warning("XSRF token not found in cookies")
            # Extract expiration date from cookies
            expires = urllib.parse.unquote(cookies.get("expires", ""))
            if expires:
                try:
                    expires_date = datetime.strptime(
                        expires, "%a, %d %b %Y %H:%M:%S %Z"
                    )
                    self.expires = expires_date.strftime("%s")
                    _LOGGER.debug("Cookie expiration date obtained successfully")
                except ValueError as e:
                    raise ValueError("Failed to parse cookie expiration date") from e
            else:
                _LOGGER.warning("No cookie expiration date found")
        else:
            raise ValueError("Set-Cookie header not found in response")

    async def _make_request(
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

    async def _make_api_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        endpoint: str,
        headers: dict | None = None,
        data: dict | None = None,
        retry_on_missing_token: bool = True,
    ) -> dict:
        """Make an API request to the Duplicati backend."""
        url = self.base_url + endpoint
        now = dt_util.utcnow().strftime("%s")
        # Ensure that XSRF token is available
        if not self.xsrf_token or (self.expires and self.expires <= now):
            await self._get_xsrf_token(session)
        headers = headers or {}
        headers["X-XSRF-Token"] = self.xsrf_token
        response = await self._make_request(
            session, method, url, headers=headers, data=data
        )
        # Handle missing XSRF token
        if (
            response.status == HTTPStatus.BAD_REQUEST.value
            and response.reason == "Missing XSRF Token. Please reload the page"
            and retry_on_missing_token
        ):
            await self._get_xsrf_token(session)
            headers["X-XSRF-Token"] = self.xsrf_token
            return await self._make_api_request(
                session,
                method,
                endpoint,
                headers,
                data,
                retry_on_missing_token=False,
            )
        # Extract latest XSRF token from response
        self._extract_xsrf_token(response)
        # Parse and return JSON response
        return await self._parse_json_response(response)

    async def _parse_json_response(self, response: aiohttp.ClientResponse) -> dict:
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

    def get_api_host(self):
        """Return the host (including port) from the base URL."""
        return self.parsed_base_url.netloc

    async def get_backup(self, backup_id: str) -> dict:
        """Get the information of a backup by ID."""
        try:
            # Validate backup ID
            if not re.match(r"\d+", backup_id):
                raise ValueError("Invalid backup ID format")
            async with aiohttp.ClientSession() as session:
                endpoint = f"/api/v1/Backup/{backup_id}"
                return await self._make_api_request(session, "GET", endpoint)
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
                    response = await self._make_api_request(session, "POST", endpoint)
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
                return await self._make_api_request(session, "PUT", endpoint, data=data)
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
                return await self._make_api_request(session, "DELETE", endpoint)
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
                return await self._make_api_request(session, "GET", endpoint)
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
                return await self._make_api_request(session, "GET", endpoint)
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
                return await self._make_api_request(session, "GET", endpoint)
        except aiohttp.ClientConnectionError as e:
            raise e from e
        except aiohttp.ClientError as e:
            _LOGGER.debug(
                "Getting the system information of the Duplicati backend server failed: %s",
                str(e),
            )
            return {"Error": str(e)}
