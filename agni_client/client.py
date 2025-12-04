from typing import Any, Dict, Optional

import requests

from .config import AgniConfig
from .exceptions import APIRequestError, AuthenticationError


class AgniClient:
    """Thin wrapper around requests.Session for AGNI."""

    def __init__(self, config: AgniConfig, *, session: Optional[requests.Session] = None, timeout: int = 30) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.timeout = timeout
        self._authenticated = False

    def authenticate(self) -> None:
        if self._authenticated:
            return

        login_params = {"keyID": self.config.key_id, "keyValue": self.config.key_value}
        login_headers = {"Accept": "application/json"}

        try:
            resp = self.session.get(self.config.auth_url, headers=login_headers, params=login_params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise AuthenticationError(f"Login failed: {exc}") from exc

        self._authenticated = True

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        self.authenticate()

        url = f"{self.config.api_base.rstrip('/')}/{path.lstrip('/')}"
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise APIRequestError(f"Request to {url} failed: {exc}") from exc

        try:
            body = resp.json()
        except ValueError as exc:
            raise APIRequestError(f"Invalid JSON response from {url}") from exc

        if isinstance(body, dict) and body.get("error"):
            raise APIRequestError(f"API returned an error: {body['error']}")

        return body

    def close(self) -> None:
        self.session.close()
