"""The one family every deliberate Holdfast error belongs to.

Callers (the HTTP API, the Discord bot) catch `HoldfastError` and show its message: each one is
written to be safe to show the person whose action failed. Anything else that escapes is a bug.
"""

from __future__ import annotations


class HoldfastError(Exception):
    """Something Holdfast refused or couldn't do, said in plain words."""
