class AgniError(Exception):
    """Base exception for AGNI client errors."""


class AuthenticationError(AgniError):
    """Raised when authentication fails."""


class APIRequestError(AgniError):
    """Raised when an API request is unsuccessful."""
