"""Normalize acquisition failures without exposing URLs, cookies or exception bodies."""

from __future__ import annotations

import re

from ..schema import ProbeWarning


def subtitle_discovery_warning(exc: Exception) -> ProbeWarning:
    """Handle urllib and yt-dlp wrappers; retain only a recognized HTTP status."""
    pending = [exc]
    seen: set[int] = set()
    status = None
    timed_out = False
    while pending and len(seen) < 12:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        timed_out |= isinstance(error, TimeoutError)
        code = getattr(error, "status", None) or getattr(error, "code", None)
        if isinstance(code, int) and 400 <= code <= 599:
            status = code
        else:
            match = re.search(r"\bHTTP Error ([45]\d{2})\b", str(error))
            if match:
                status = int(match.group(1))
        for nested in (error.__cause__, error.__context__, getattr(error, "reason", None)):
            if isinstance(nested, BaseException):
                pending.append(nested)
        info = getattr(error, "exc_info", None)
        if isinstance(info, tuple) and len(info) > 1 and isinstance(info[1], BaseException):
            pending.append(info[1])

    if status in (403, 412, 429):
        return ProbeWarning(
            code="subtitle_access_blocked", http_status=status, retryable=True,
            message=f"Subtitle discovery received HTTP {status}; availability is unknown. "
                    "Pause requests and verify the configured browser login before a later retry; "
                    "do not interpret this as no subtitles or automatically start ASR.",
        )
    if status is not None:
        return ProbeWarning(
            code="subtitle_http_error", http_status=status, retryable=status >= 500,
            message=f"Subtitle discovery received HTTP {status}; availability is unknown. "
                    "Resolve provider access before choosing subtitle reuse or ASR.",
        )
    return ProbeWarning(
        code="subtitle_timeout" if timed_out else "subtitle_discovery_failed",
        retryable=timed_out,
        message="Subtitle discovery failed; availability is unknown. Check network access and "
                "the configured browser login before retrying. No absence of subtitles was established.",
    )
