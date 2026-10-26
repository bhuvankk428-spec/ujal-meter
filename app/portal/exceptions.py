class PortalError(Exception):
    """Base class for all failures that originate behind the adapter."""


class PortalAuthError(PortalError):
    """The operator session could not be established or restored."""


class PortalNotFoundError(PortalError):
    """The portal answered 404 for a requested resource."""


class PortalTimeoutError(PortalError):
    """Connect/read/total timeout while talking to the portal."""


class PortalUnavailableError(PortalError):
    """Portal is down, returned 5xx, or rejected the request (429)."""


class CircuitOpenError(PortalUnavailableError):
    """Fail fast: too many consecutive portal failures."""


class ParseError(PortalError):
    """The response shape changed; refuse to return silent empty data."""
