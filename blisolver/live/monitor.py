"""Live room status monitor with automatic live-start detection.

Periodically queries Bilibili room endpoints with jittered rate-limiting
until the broadcaster goes live, then yields room metadata for ingestion.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import aiohttp

from .safety import SafetyGuard

logger = logging.getLogger(__name__)

ROOM_INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info"


class RoomInfoError(RuntimeError):
    """Raised when the room info endpoint returns a non-zero code or invalid body."""


class LiveMonitor:
    """Watches a Bilibili live room and detects stream lifecycle transitions."""

    def __init__(
        self,
        room_id: int,
        session: aiohttp.ClientSession | None = None,
        safety: SafetyGuard | None = None,
    ):
        self.room_id = room_id
        self._session = session
        self._own_session = False
        self.safety = safety or SafetyGuard()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Release internally managed session resources if created."""
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    async def check_status(self) -> dict[str, Any]:
        """Fetch current room state.

        Returns:
            Dict containing:
                - live_status: 1 if live, 0 if offline, 2 if rebroadcast
                - is_live: bool (True only when live_status == 1)
                - real_room_id: int (canonical long room ID)
                - title: str
                - anchor_uid: int
                - live_time: str
                - raw: dict
        """
        session = await self._get_session()
        headers = self.safety.safe_headers(self.room_id)
        params = {"room_id": str(self.room_id)}

        try:
            async with session.get(ROOM_INFO_API, params=params, headers=headers) as resp:
                if resp.status != 200:
                    raise RoomInfoError(f"HTTP {resp.status} querying room {self.room_id}")
                data = await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            logger.warning("network error checking room %s: %s", self.room_id, exc)
            return {
                "live_status": None,
                "is_live": None,
                "real_room_id": self.room_id,
                "title": "",
                "anchor_uid": 0,
                "live_time": "",
                "raw": {},
            }

        code = data.get("code")
        if code != 0:
            msg = data.get("message", "unknown error")
            raise RoomInfoError(f"room info error (code {code}): {msg}")

        room_data = data.get("data") or {}
        live_status = int(room_data.get("live_status", 0))
        return {
            "live_status": live_status,
            "is_live": (live_status == 1),
            "real_room_id": int(room_data.get("room_id", self.room_id)),
            "title": str(room_data.get("title", "")),
            "anchor_uid": int(room_data.get("uid", 0)),
            "live_time": str(room_data.get("live_time", "")),
            "raw": room_data,
        }

    async def wait_until_live(
        self,
        on_poll: Callable[[dict[str, Any]], Coroutine[Any, Any, None] | None] | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        """Block until the broadcaster begins streaming, pacing calls with safe jitter."""
        attempts = 0
        while True:
            attempts += 1
            info = await self.check_status()
            if on_poll:
                res = on_poll(info)
                if asyncio.iscoroutine(res):
                    await res

            if info["is_live"]:
                logger.info("Room %s is now LIVE: '%s'", self.room_id, info["title"])
                return info

            if max_attempts and attempts >= max_attempts:
                logger.info("Max poll attempts (%d) reached for room %s", max_attempts, self.room_id)
                return info

            await self.safety.wait_jitter()
