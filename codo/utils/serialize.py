"""Serialization helpers for runtime events and UI metadata."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum


def _serialize(value: object, *, include_object_attrs: bool) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _serialize(asdict(value), include_object_attrs=include_object_attrs)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_serialize(item, include_object_attrs=include_object_attrs) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item, include_object_attrs=include_object_attrs) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _serialize(item, include_object_attrs=include_object_attrs)
            for key, item in value.items()
        }
    if include_object_attrs and hasattr(value, "__dict__") and not isinstance(value, type):
        return {
            str(key): _serialize(item, include_object_attrs=include_object_attrs)
            for key, item in vars(value).items()
        }
    return value


def serialize_to_json(value: object) -> object:
    return _serialize(value, include_object_attrs=False)


def serialize_ui_metadata(value: object) -> object:
    return _serialize(value, include_object_attrs=True)
