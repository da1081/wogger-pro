"""Feature flag persistence and access helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .paths import features_path

LOGGER = logging.getLogger("wogger.features")


@dataclass(slots=True)
class FeatureState:
    """Discrete feature toggles persisted to disk."""

    disable_update_check: bool = True


DEFAULT_STATE = FeatureState()


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


class FeatureService:
    """Load and persist feature toggles in the app data directory."""

    def __init__(self, path: Path | None = None) -> None:
        self._explicit_path = Path(path) if path is not None else None
        self._path = self._resolve_path()
        self._state = FeatureState()
        self._load()

    def _resolve_path(self) -> Path:
        return self._explicit_path or features_path()

    def _load(self) -> None:
        self._path = self._resolve_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_state(self._state)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            LOGGER.warning("Unable to read feature flags; restoring defaults", exc_info=True)
            self._write_state(FeatureState())
            return
        if not isinstance(raw, dict):
            LOGGER.warning("Feature flags file malformed; restoring defaults")
            self._write_state(FeatureState())
            return
        self._state = FeatureState(
            disable_update_check=_coerce_bool(
                raw.get("disable_update_check"), DEFAULT_STATE.disable_update_check
            ),
        )

    def _write_state(self, state: FeatureState) -> None:
        payload = asdict(state)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            LOGGER.exception("Failed to persist feature flags", extra={"event": "features_write_error"})
            return
        self._state = state

    @property
    def state(self) -> FeatureState:
        return self._state

    def is_update_check_disabled(self) -> bool:
        return self._state.disable_update_check

    def set_disable_update_check(self, value: bool) -> None:
        desired = bool(value)
        if self._state.disable_update_check == desired:
            return
        new_state = replace(self._state, disable_update_check=desired)
        self._write_state(new_state)

    def reload(self) -> None:
        self._load()
