"""
Secret store — the ONLY place customer-portal / social credentials live.

Why this exists: the distributor may "help the agent log in" to myHerbalife so it
can pull customer details. Those credentials must NEVER be written to the
application database or to source control. This module is a small, swappable
abstraction:

  • Default (dev): an in-process, in-memory dict. Credentials vanish when the
    process restarts — intentionally. Nothing is persisted to disk.
  • Production: replace ``_backend`` with a real secrets manager
    (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault). The rest of the
    app only calls put/get/has/delete, so swapping the backend is a one-file
    change.

The DB only ever records a boolean "connected" flag + timestamp (see the
``distributors`` table) — never the secret itself.
"""

from __future__ import annotations

from typing import Dict, Optional


class _InMemoryBackend:
    """Process-local secret storage. Not persisted. Cleared on restart."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, str]] = {}

    def put(self, key: str, value: Dict[str, str]) -> None:
        self._data[key] = dict(value)

    def get(self, key: str) -> Optional[Dict[str, str]]:
        return self._data.get(key)

    def has(self, key: str) -> bool:
        return key in self._data

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


_backend = _InMemoryBackend()


def _key(distributor_id: int, provider: str) -> str:
    return f"dist:{distributor_id}:{provider}"


def store_credentials(distributor_id: int, provider: str, creds: Dict[str, str]) -> None:
    """Store provider credentials (e.g. {'username':..,'password':..}) securely."""
    _backend.put(_key(distributor_id, provider), creds)


def has_credentials(distributor_id: int, provider: str) -> bool:
    return _backend.has(_key(distributor_id, provider))


def get_credentials(distributor_id: int, provider: str) -> Optional[Dict[str, str]]:
    """Retrieved only at the moment a connector needs to authenticate."""
    return _backend.get(_key(distributor_id, provider))


def revoke_credentials(distributor_id: int, provider: str) -> None:
    _backend.delete(_key(distributor_id, provider))
