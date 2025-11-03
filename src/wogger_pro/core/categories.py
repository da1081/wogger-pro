"""Category storage management for Wogger Pro."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, List

import portalocker

from .exceptions import PersistenceError
from .paths import categories_path


LOGGER = logging.getLogger("wogger.categories")


class CategoryManager:
    """Handles CRUD operations for category metadata."""

    def __init__(
        self,
        path: Path | None = None,
        lock_timeout: float = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else categories_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("[]", encoding="utf-8")
        self._lock_timeout = lock_timeout
        self._logger = logger or LOGGER

    # ------------------------------------------------------------------
    def list_categories(self) -> list[str]:
        categories = self._load()
        categories.sort(key=lambda value: value.lower())
        return categories

    def add_category(self, name: str) -> None:
        normalized = _normalize(name)
        if not normalized:
            raise ValueError("Category name must be non-empty")

        categories = self._load()
        if _contains(categories, normalized):
            raise ValueError("Category already exists")
        categories.append(normalized)
        self._save(categories)

    def rename_category(self, old_name: str, new_name: str) -> None:
        old_normalized = _normalize(old_name)
        new_normalized = _normalize(new_name)
        if not old_normalized or not new_normalized:
            raise ValueError("Category name must be non-empty")
        categories = self._load()
        if not _contains(categories, old_normalized):
            raise ValueError("Category not found")
        if old_normalized.lower() == new_normalized.lower():
            return
        if _contains(categories, new_normalized):
            raise ValueError("A category with that name already exists")

        categories = [new_normalized if item.lower() == old_normalized.lower() else item for item in categories]
        self._save(categories)

    def delete_category(self, name: str) -> None:
        normalized = _normalize(name)
        if not normalized:
            return
        categories = self._load()
        filtered = [item for item in categories if item.lower() != normalized.lower()]
        if len(filtered) == len(categories):
            return
        self._save(filtered)

    # ------------------------------------------------------------------
    def _load(self) -> list[str]:
        try:
            with portalocker.Lock(
                self._path,
                mode="r",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.SHARED,
                encoding="utf-8",
            ) as locked_file:
                try:
                    data = json.load(locked_file)
                except json.JSONDecodeError:
                    self._logger.warning("Categories file malformed; resetting to empty list")
                    data = []
        except FileNotFoundError:
            self._path.write_text("[]", encoding="utf-8")
            return []
        except Exception as exc:
            self._logger.exception("Unable to read categories file")
            raise PersistenceError("Unable to read categories") from exc

        return [_normalize(item) for item in data if _normalize(item)]

    def _save(self, categories: Iterable[str]) -> None:
        ordered = sorted({_normalize(item) for item in categories if _normalize(item)}, key=lambda value: value.lower())
        try:
            with portalocker.Lock(
                self._path,
                mode="w",
                timeout=self._lock_timeout,
                flags=portalocker.LockFlags.EXCLUSIVE,
                encoding="utf-8",
            ) as locked_file:
                json.dump(list(ordered), locked_file, ensure_ascii=False, indent=2)
                locked_file.flush()
        except Exception as exc:
            self._logger.exception("Unable to save categories file")
            raise PersistenceError("Unable to save categories") from exc


def _normalize(value: str | None) -> str:
    return (value or "").strip()


def _contains(collection: List[str], value: str) -> bool:
    return any(item.lower() == value.lower() for item in collection)
