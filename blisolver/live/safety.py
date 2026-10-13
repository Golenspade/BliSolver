"""Safety guardrails, anti-banning measures, and strict read-only enforcement.

Guarantees:
1. Strictly Consumer-Only (Read-Only): zero message-sending, voting, liking, or gifting APIs.
2. Jittered rate limiting for status polling to prevent WAF / bot-fingerprinting.
3. Clean browser header hygiene and credential masking.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Final

_FORBIDDEN_KEYWORDS: Final[tuple[str, ...]] = (
    "send",
    "post",
    "like",
    "click",
    "publish",
    "modify",
    "delete",
    "bag_send",
    "comment",
    "share",
)

DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


class SafetyViolationError(PermissionError):
    """Raised when an operation violates read-only anti-banning constraints."""


AccountSecurityViolation = SafetyViolationError


class SafetyGuard:
    """Manages rate-limiting jitter and verifies TOS compliance boundaries."""

    def __init__(
        self,
        sessdata: str | None = None,
        min_interval_s: float = 15.0,
        max_interval_s: float = 30.0,
    ):
        if min_interval_s <= 0 or max_interval_s < min_interval_s:
            raise ValueError("invalid interval bounds")
        self.sessdata = sessdata
        self.min_interval = min_interval_s
        self.max_interval = max_interval_s
        self._last_request_time: float = 0.0

    async def wait_jitter(self, min_s: float | None = None, max_s: float | None = None) -> float:
        """Introduce randomized delay between room polling cycles to avoid bot signatures."""
        low = min_s if min_s is not None else self.min_interval
        high = max_s if max_s is not None else self.max_interval
        target_interval = random.uniform(low, high)
        await asyncio.sleep(target_interval)
        self._last_request_time = time.monotonic()
        return target_interval

    @staticmethod
    def assert_read_only(action_name: str, url: str | None = None) -> None:
        """Reject any operation that might attempt writing or interaction to protect dedicated accounts."""
        target = f"{action_name} {url or ''}".strip().lower()
        if any(kw in target for kw in _FORBIDDEN_KEYWORDS):
            raise SafetyViolationError(
                f"TOS safety constraint violated: '{action_name}' is not permitted. "
                f"The live solver operates strictly in read-only / consumer mode."
            )

    def safe_headers(self, room_id: int | None = None) -> dict[str, str]:
        """Construct standard browser headers required by bilibili endpoints."""
        referer = f"https://live.bilibili.com/{room_id}" if room_id else "https://live.bilibili.com/"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.sessdata:
            headers["Cookie"] = f"SESSDATA={self.sessdata};"
        return headers

    @staticmethod
    def mask_credential(cred: str | None) -> str:
        """Mask cookies or tokens for safe diagnostic logging."""
        if not cred:
            return "<none>"
        if len(cred) <= 8:
            return "***"
        return f"{cred[:4]}...{cred[-4:]}"
