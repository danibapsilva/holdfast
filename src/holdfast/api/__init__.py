"""The HTTP API: the web playground's backend. `create_app` builds it over any `EscrowService`."""

from holdfast.api.app import create_app

__all__ = ["create_app"]
