"""Anonymous visitors, without accounts or a session table.

Starting a demo deal hands the visitor a random token (`secrets.token_urlsafe`). Their id in the deal is a
hash of it, so the server never stores a token: any instance can check one, a restart logs nobody out, and
a copy of the database can't be used to act as a visitor. Every call about a deal sends the token as
`Authorization: Bearer <token>`.
"""

from __future__ import annotations

import hashlib
import secrets

from holdfast.errors import HoldfastError

VISITOR_PREFIX = "visitor-"
TOKEN_BYTES = 32
MIN_TOKEN_LENGTH, MAX_TOKEN_LENGTH = 20, 200


class Unauthorized(HoldfastError):
    """No usable visitor token came with the request."""


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def visitor_id(token: str) -> str:
    """The user id a token stands for in its deals."""
    return VISITOR_PREFIX + hashlib.sha256(token.encode()).hexdigest()[:32]


def bearer_token(authorization: str | None) -> str:
    """The token out of an `Authorization: Bearer ...` header."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH:
        raise Unauthorized("Send the deal's token as Authorization: Bearer <token>")
    return token
