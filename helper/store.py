"""File-backed approval requests with a 24 hour expiry. One JSON file per request."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

TTL = timedelta(hours=24)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass(frozen=True)
class Request:
    id: str
    payload: dict[str, Any]
    expired: bool
    chosen: tuple[str, ...] | None
    built_path: str | None

    @property
    def submitted(self) -> bool:
        return self.chosen is not None


class ApprovalStore:
    def __init__(self, root: Path, clock: Callable[[], datetime] = datetime.now, ttl: timedelta = TTL):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._clock, self._ttl = clock, ttl
        self._lock = threading.Lock()

    def _path(self, request_id: str) -> Path | None:
        return self._root / f"{request_id}.json" if _ID_PATTERN.match(request_id) else None

    def _read(self, request_id: str) -> dict[str, Any] | None:
        path = self._path(request_id)
        if path is None or not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, request_id: str, record: dict[str, Any]) -> None:
        path = self._path(request_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, path)

    def create(self, payload: dict[str, Any]) -> str:
        request_id = secrets.token_urlsafe(16)
        record = {"created_at": self._clock().isoformat(), "payload": payload, "chosen": None, "built_path": None}
        self._write(request_id, record)
        return request_id

    def get(self, request_id: str) -> Request | None:
        record = self._read(request_id)
        if record is None:
            return None
        age = self._clock() - datetime.fromisoformat(record["created_at"])
        chosen = record["chosen"]
        return Request(
            id=request_id,
            payload=record["payload"],
            expired=age > self._ttl,
            chosen=None if chosen is None else tuple(chosen),
            built_path=record["built_path"],
        )

    def submit(self, request_id: str, chosen: list[str]) -> bool:
        """Record the user's choice. False when unknown, expired or already submitted."""
        with self._lock:
            req = self.get(request_id)
            if req is None or req.expired or req.submitted:
                return False
            record = self._read(request_id)
            record["chosen"] = list(chosen)
            self._write(request_id, record)
            return True

    def mark_built(self, request_id: str, path: str) -> None:
        with self._lock:
            record = self._read(request_id)
            if record is not None:
                record["built_path"] = path
                self._write(request_id, record)
