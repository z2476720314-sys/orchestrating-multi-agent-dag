"""Strictly limit text that can reach the observer UI."""

from __future__ import annotations

import os
import re

HIDDEN_TEXT = "[内容已隐藏]"

_UNSAFE_FIELD = re.compile(
    r"(?:"
    r"authorization\s*[:=]|\bbearer(?:\s|:)|cookie\s*[:=]|\boauth\b|"
    r"(?:access[_ -]?)?token(?:\s*[:=]|\s+)|"
    r"password(?:\s*[:=]|\s+)|passwd(?:\s*[:=]|\s+)|"
    r"secret(?:\s*[:=]|\s+)|api[_ -]?key(?:\s*[:=]|\s+)|"
    r"(?:^|[\\/])\.?credentials?(?:[.\s\\/]|$)|\.dpapi(?:[.\s\\/]|$)|"
    r"(?:system(?:[_ -]?prompt)?|developer(?:[_ -]?instructions?)?|"
    r"reasoning|hidden[_ -]?reasoning|prompt|"
    r"tool[_ -]?(?:args?|arguments?|results?)|stdout|stderr|environment|clipboard)"
    r"(?:\s|[:=])"
    r")",
    flags=re.IGNORECASE,
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:/-]{0,159}\Z")
_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def sanitize_text(value: str, limit: int = 240) -> str:
    """Return a compact public label, or hide content with unsafe markers."""

    if not isinstance(value, str) or limit <= 0:
        return HIDDEN_TEXT
    compact = " ".join(value.split())
    compact = re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", compact)
    if not compact or _UNSAFE_FIELD.search(compact):
        return HIDDEN_TEXT
    if len(compact) > limit:
        return compact[: max(0, limit - 1)] + "…"
    return compact


def _sanitize_token(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return HIDDEN_TEXT
    if ".." in value or "//" in value or "\\" in value:
        return HIDDEN_TEXT
    return value


def sanitize_identifier(value: object) -> str:
    """Allow only bounded ASCII identifiers suitable for UI relationships."""

    return _sanitize_token(value, _IDENTIFIER)


def sanitize_model_name(value: object) -> str:
    """Allow only the conventional provider/model token format."""

    return _sanitize_token(value, _MODEL_NAME)


def sanitize_tool_name(value: object) -> str:
    """Allow only a bounded tool token; never pass arguments or commands."""

    return _sanitize_token(value, _TOOL_NAME)


def canonical_path_key(value: str | os.PathLike[str]) -> str:
    """Return one comparison key for normal and Windows extended paths."""

    raw = os.fspath(value)
    if os.name == "nt":
        if raw.casefold().startswith("\\\\?\\unc\\"):
            raw = "\\\\" + raw[8:]
        elif raw.startswith("\\\\?\\"):
            raw = raw[4:]
    normalized = os.path.realpath(os.path.abspath(raw))
    return os.path.normcase(normalized).casefold().rstrip("\\/")
