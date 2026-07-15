"""Helpers for parsing and matching email thread headers."""

import re

_MESSAGE_TOKEN_RE = re.compile(r"<([^>]+)>|([^\s,<>]+)")


def normalize_message_token(value: str | None) -> str:
    """Normalizes Gmail IDs and RFC Message-ID values for comparison."""
    if not value:
        return ""
    return value.strip().strip("<>").lower()


def extract_reference_tokens(*headers: str | None) -> list[str]:
    """Extracts unique message reference tokens from In-Reply-To / References headers."""
    seen: set[str] = set()
    ordered: list[str] = []
    for header in headers:
        if not header:
            continue
        for match in _MESSAGE_TOKEN_RE.finditer(header):
            token = normalize_message_token(match.group(1) or match.group(2))
            if token and token not in seen:
                seen.add(token)
                ordered.append(token)
    return ordered
