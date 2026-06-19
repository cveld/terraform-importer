from __future__ import annotations

import json
from pathlib import Path


class ResolveCache:
    """Persistent read-through cache for live (`az`) lookups, keyed by command.

    Only successful, non-empty results are stored, so a failed or empty lookup
    can still succeed on a later run. Persisted as JSON next to the plan; delete
    the file (or pass --no-cache) to force fresh lookups.
    """

    def __init__(self, path: str | None, enabled: bool = True):
        self.path = Path(path) if path else None
        self.enabled = enabled and self.path is not None
        self._data: dict[str, str] = {}
        self._dirty = False
        self.hits = 0
        if self.enabled and self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = {k: v for k, v in loaded.items() if isinstance(v, str)}
            except Exception:
                self._data = {}

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        val = self._data.get(key)
        if val is not None:
            self.hits += 1
        return val

    def set(self, key: str, value: str) -> None:
        if self.enabled and value:
            if self._data.get(key) != value:
                self._data[key] = value
                self._dirty = True

    def save(self) -> None:
        if self.enabled and self._dirty:
            try:
                self.path.write_text(
                    json.dumps(self._data, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                self._dirty = False
            except Exception:
                pass
