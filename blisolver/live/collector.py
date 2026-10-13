"""Real-time event collector based on blivedm.

Captures all danmaku, gifts (v1 & v2 protobuf), SuperChats, and Guard buys,
transforming raw frames into strongly-typed LiveModel records.
"""

from __future__ import annotations

import asyncio
import http.cookies
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp
import blivedm
import blivedm.models.web as web_models

from .models import (
    LiveDanmakuRecord,
    LiveEvent,
    LiveGiftRecord,
    LiveGuardRecord,
    LiveSuperChatRecord,
    LiveSystemEvent,
)
from .safety import SafetyGuard

logger = logging.getLogger(__name__)


class LiveHandler(blivedm.BaseHandler):
    """Bridges blivedm WebSocket callbacks into standard BliSolver LiveEvent records."""

    def __init__(
        self,
        event_queue: asyncio.Queue[LiveEvent],
        listener: Callable[[LiveEvent], Any] | None = None,
    ):
        super().__init__()
        self.queue = event_queue
        self.listener = listener
        self.overflow_count = 0
        self.failure: Exception | None = None
        self.stopping = False

    def on_client_stopped(
        self, client: blivedm.BLiveClient, exception: Exception | None
    ) -> None:
        """Surface fatal upstream termination; normal requested shutdown is expected."""
        if exception is not None or not self.stopping:
            self.failure = exception or RuntimeError("Live collector stopped unexpectedly")

    def _dispatch(self, event: LiveEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflow_count += 1
            logger.error("Live event queue overflow; session capture is incomplete")

        if self.listener:
            try:
                self.listener(event)
            except Exception:
                logger.exception("Error in user live event listener")

    def _on_heartbeat(self, client: blivedm.BLiveClient, message: web_models.HeartbeatMessage):
        event = LiveSystemEvent(
            timestamp=time.time(),
            room_id=client.room_id,
            action="heartbeat",
            detail={"popularity": getattr(message, "popularity", 0)},
        )
        self._dispatch(event)

    def _on_danmaku(self, client: blivedm.BLiveClient, message: web_models.DanmakuMessage):
        SafetyGuard.assert_read_only("danmaku_received")
        ts = message.timestamp / 1000.0 if message.timestamp > 1e11 else float(message.timestamp)
        if ts <= 0:
            ts = time.time()

        record = LiveDanmakuRecord(
            timestamp=ts,
            room_id=client.room_id,
            uid=getattr(message, "uid", 0) or 0,
            uname=getattr(message, "uname", "") or "",
            msg=getattr(message, "msg", "") or "",
            mode=getattr(message, "mode", 1),
            font_size=getattr(message, "font_size", 25),
            color=getattr(message, "color", 0xFFFFFF),
            medal_name=getattr(message, "medal_name", None),
            medal_level=getattr(message, "medal_level", 0),
            guard_level=getattr(message, "privilege_type", 0),
            is_admin=bool(getattr(message, "admin", False)),
            emoticon_url=getattr(message, "emoticon_url", None)
            or (message.emoticon_options.get("url") if isinstance(getattr(message, "emoticon_options", None), dict) else None),
        )
        self._dispatch(record)

    def _on_gift(self, client: blivedm.BLiveClient, message: web_models.GiftMessage):
        SafetyGuard.assert_read_only("gift_received")
        ts = float(getattr(message, "timestamp", 0) or time.time())
        coin_type = getattr(message, "coin_type", "gold")
        total_coin = int(getattr(message, "total_coin", 0))

        # 1000 gold seeds = 1 CNY battery
        cny_value = (total_coin / 1000.0) if coin_type == "gold" else 0.0

        record = LiveGiftRecord(
            timestamp=ts,
            room_id=client.room_id,
            uid=getattr(message, "uid", 0) or 0,
            uname=getattr(message, "uname", "") or "",
            gift_name=getattr(message, "gift_name", "") or "未知礼物",
            gift_id=int(getattr(message, "gift_id", 0)),
            num=int(getattr(message, "num", 1)),
            coin_type=coin_type if coin_type in ("gold", "silver") else "gold",
            total_coin=total_coin,
            cny_value=round(cny_value, 2),
            guard_level=int(getattr(message, "guard_level", 0)),
            action=str(getattr(message, "action", "投喂")),
        )
        self._dispatch(record)

    def _on_super_chat(self, client: blivedm.BLiveClient, message: web_models.SuperChatMessage):
        SafetyGuard.assert_read_only("super_chat_received")
        ts = float(getattr(message, "start_time", 0) or time.time())
        price = float(getattr(message, "price", 0.0))

        record = LiveSuperChatRecord(
            timestamp=ts,
            room_id=client.room_id,
            uid=getattr(message, "uid", 0) or 0,
            uname=getattr(message, "uname", "") or "",
            price=price,
            message=getattr(message, "message", "") or "",
            duration_s=int(getattr(message, "time", 60)),
            message_trans=getattr(message, "message_trans", None),
            sc_id=getattr(message, "id", None),
        )
        self._dispatch(record)

    def _on_user_toast_v2(self, client: blivedm.BLiveClient, message: web_models.UserToastV2Message):
        SafetyGuard.assert_read_only("guard_received")
        guard_level = int(getattr(message, "guard_level", 3))
        # Source 2 repeats the paid toast as a giveaway notice.
        if getattr(message, "source", 0) == 2:
            return
        num = int(getattr(message, "num", 1))

        record = LiveGuardRecord(
            timestamp=time.time(),
            room_id=client.room_id,
            uid=getattr(message, "uid", 0) or 0,
            uname=getattr(message, "username", "") or "",
            guard_level=guard_level if 1 <= guard_level <= 3 else 3,
            num=num,
            unit=getattr(message, "unit", "月"),
            cny_value=float(getattr(message, "price", 0)) * num / 1000.0,
        )
        self._dispatch(record)


class LiveCollector:
    """Manages the blivedm client session lifecycle with session authentication."""

    def __init__(
        self,
        room_id: int,
        sessdata: str | None = None,
        event_queue: asyncio.Queue[LiveEvent] | None = None,
        listener: Callable[[LiveEvent], Any] | None = None,
    ):
        self.room_id = room_id
        self.sessdata = sessdata
        self.queue: asyncio.Queue[LiveEvent] = event_queue or asyncio.Queue(maxsize=100000)
        self.listener = listener
        self._session: aiohttp.ClientSession | None = None
        self._client: blivedm.BLiveClient | None = None
        self._handler = LiveHandler(self.queue, listener=self.listener)

    @property
    def overflow_count(self) -> int:
        return self._handler.overflow_count

    @property
    def failure(self) -> Exception | None:
        return self._handler.failure

    def _init_session(self) -> aiohttp.ClientSession:
        cookies = http.cookies.SimpleCookie()
        if self.sessdata:
            cookies["SESSDATA"] = self.sessdata
            cookies["SESSDATA"]["domain"] = "bilibili.com"

        session = aiohttp.ClientSession()
        if self.sessdata:
            session.cookie_jar.update_cookies(cookies)
        return session

    async def start(self) -> None:
        """Connect to the room WebSocket comet server with credentials."""
        SafetyGuard.assert_read_only("start_live_collector")
        self._session = self._init_session()
        self._client = blivedm.BLiveClient(self.room_id, session=self._session)
        self._client.set_handler(self._handler)
        self._client.start()
        logger.info("LiveCollector started for room %d (auth=%s)", self.room_id, bool(self.sessdata))

    async def stop(self) -> None:
        """Gracefully disconnect and tear down network sessions."""
        self._handler.stopping = True
        try:
            if self._client:
                await self._client.stop_and_close()
        finally:
            self._client = None
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None
        logger.info("LiveCollector stopped for room %d", self.room_id)

    async def join(self) -> None:
        """Wait until client terminates or disconnects."""
        if self._client:
            await self._client.join()
