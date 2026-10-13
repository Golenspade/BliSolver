"""Full live session lifecycle orchestrator and post-stream aggregator.

Manages:
1. Streaming persistence to `events.jsonl` in real time (zero data loss on crash).
2. Live stream iteration (async generator) for downstream real-time consumers.
3. Stream conclusion aggregation: monetization totals, viewer censuses, Top chatters.
4. Final delivery bundles: `live-bundle.json` and human-readable `live-bundle.md`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .collector import LiveCollector
from .models import (
    LiveDanmakuRecord,
    LiveEvent,
    LiveGiftRecord,
    LiveGuardRecord,
    LiveSessionSummary,
    LiveSuperChatRecord,
)
from .monitor import LiveMonitor
from .safety import SafetyGuard

logger = logging.getLogger(__name__)


class LiveSession:
    """Coordinates a complete live broadcast session from start to post-stream bundles."""

    def __init__(
        self,
        room_id: int,
        out_dir: Path | str,
        sessdata: str | None = None,
        wait_for_live: bool = True,
        max_duration_s: float | None = None,
        safety: SafetyGuard | None = None,
    ):
        self.room_id = room_id
        self.out_dir = Path(out_dir)
        self.sessdata = sessdata
        self.wait_for_live = wait_for_live
        self.max_duration_s = max_duration_s
        self.safety = safety or SafetyGuard()

        self.event_queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=100000)
        self._collector: LiveCollector | None = None
        self._monitor: LiveMonitor | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._is_active = False

        # In-memory accumulators for post-stream aggregation
        self._all_events: list[LiveEvent] = []
        self._room_info: dict[str, Any] = {}
        self._start_time: datetime | None = None
        self._end_time: datetime | None = None

    @property
    def jsonl_path(self) -> Path:
        return self.out_dir / "events.jsonl"

    @property
    def bundle_json_path(self) -> Path:
        return self.out_dir / "live-bundle.json"

    @property
    def bundle_md_path(self) -> Path:
        return self.out_dir / "live-bundle.md"

    async def _event_writer_loop(self) -> None:
        """Background coroutine that continuously appends incoming events to JSONL."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:  # noqa: ASYNC230
            while self._is_active or not self.event_queue.empty():
                try:
                    event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
                except TimeoutError:
                    continue

                self._all_events.append(event)
                line = json.dumps(event.model_dump(), ensure_ascii=False)
                f.write(line + "\n")
                f.flush()
                self.event_queue.task_done()

    async def start(self) -> dict[str, Any]:
        """Initiate session: wait for broadcaster to go live if configured, then connect."""
        SafetyGuard.assert_read_only("start_live_session")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._monitor = LiveMonitor(self.room_id, safety=self.safety)

        if self.wait_for_live:
            logger.info("Waiting for room %s to go live...", self.room_id)
            self._room_info = await self._monitor.wait_until_live()
        else:
            self._room_info = await self._monitor.check_status()

        self._start_time = datetime.now(UTC)
        self._is_active = True
        self._writer_task = asyncio.create_task(self._event_writer_loop())

        self._collector = LiveCollector(
            room_id=self._room_info.get("real_room_id", self.room_id),
            sessdata=self.sessdata,
            event_queue=self.event_queue,
        )
        await self._collector.start()
        logger.info("LiveSession connected to room %s", self.room_id)
        return self._room_info

    async def stop(self) -> LiveSessionSummary:
        """Gracefully disconnect, flush pending stream events, and build delivery bundles."""
        self._is_active = False
        if self._collector:
            await self._collector.stop()
            self._collector = None

        if self._monitor:
            await self._monitor.close()
            self._monitor = None

        if self._writer_task:
            await self._writer_task
            self._writer_task = None

        self._end_time = datetime.now(UTC)
        summary = self.aggregate()
        self.write_bundles(summary)
        return summary

    async def run(self) -> LiveSessionSummary:
        """Run the session lifecycle from start until stream ends or max_duration expires."""
        await self.start()
        try:
            if self.max_duration_s:
                await asyncio.sleep(self.max_duration_s)
            else:
                while True:
                    await asyncio.sleep(30)
                    info = await self._monitor.check_status()
                    if not info["is_live"]:
                        logger.info("Room %s stream has ended.", self.room_id)
                        break
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Session interrupted by user or cancellation.")
        return await self.stop()

    def stream(self) -> AsyncIterator[LiveEvent]:
        """Expose an asynchronous iterator for downstream consumers processing events live."""
        async def _generator() -> AsyncIterator[LiveEvent]:
            while self._is_active or not self.event_queue.empty():
                try:
                    event = await asyncio.wait_for(self.event_queue.get(), timeout=0.5)
                    yield event
                except TimeoutError:
                    continue
        return _generator()

    def aggregate(self) -> LiveSessionSummary:
        """Compute comprehensive statistics across all collected events."""
        start_iso = self._start_time.isoformat() if self._start_time else datetime.now(UTC).isoformat()
        end_iso = self._end_time.isoformat() if self._end_time else datetime.now(UTC).isoformat()
        duration_s = (self._end_time - self._start_time).total_seconds() if (self._start_time and self._end_time) else 0.0

        danmaku_records: list[LiveDanmakuRecord] = []
        gift_records: list[LiveGiftRecord] = []
        super_chat_records: list[LiveSuperChatRecord] = []
        guard_records: list[LiveGuardRecord] = []

        chatters_counter: Counter[str] = Counter()
        gifts_counter: Counter[str] = Counter()
        unique_senders: set[int] = set()

        total_gold_cny = 0.0
        total_silver_coins = 0
        total_super_chat_cny = 0.0
        total_guard_cny = 0.0

        for ev in self._all_events:
            if isinstance(ev, LiveDanmakuRecord):
                danmaku_records.append(ev)
                if ev.uid > 0:
                    unique_senders.add(ev.uid)
                if ev.uname:
                    chatters_counter[ev.uname] += 1
            elif isinstance(ev, LiveGiftRecord):
                gift_records.append(ev)
                gifts_counter[ev.gift_name] += ev.num
                if ev.coin_type == "gold":
                    total_gold_cny += ev.cny_value
                else:
                    total_silver_coins += ev.total_coin
            elif isinstance(ev, LiveSuperChatRecord):
                super_chat_records.append(ev)
                total_super_chat_cny += ev.price
            elif isinstance(ev, LiveGuardRecord):
                guard_records.append(ev)
                total_guard_cny += ev.cny_value

        top_chatters = [{"uname": uname, "count": count} for uname, count in chatters_counter.most_common(10)]
        top_gifts = [{"gift_name": name, "num": num} for name, num in gifts_counter.most_common(10)]

        total_monetization = round(total_gold_cny + total_super_chat_cny + total_guard_cny, 2)

        return LiveSessionSummary(
            room_id=self.room_id,
            real_room_id=self._room_info.get("real_room_id", self.room_id),
            title=self._room_info.get("title", ""),
            anchor_name=self._room_info.get("raw", {}).get("uname", ""),
            anchor_uid=self._room_info.get("anchor_uid", 0),
            start_time=start_iso,
            end_time=end_iso,
            duration_s=round(duration_s, 2),
            total_danmaku_count=len(danmaku_records),
            unique_danmaku_senders=len(unique_senders),
            total_gold_cny=round(total_gold_cny, 2),
            total_silver_coins=total_silver_coins,
            total_super_chat_cny=round(total_super_chat_cny, 2),
            total_guard_cny=round(total_guard_cny, 2),
            total_monetization_cny=total_monetization,
            super_chats=super_chat_records,
            guards=guard_records,
            top_gifts=top_gifts,
            top_chatters=top_chatters,
        )

    def write_bundles(self, summary: LiveSessionSummary) -> None:
        """Render JSON and Markdown summary bundles."""
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Authoritative JSON bundle
        bundle_payload = summary.model_dump()
        self.bundle_json_path.write_text(
            json.dumps(bundle_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 2. Human/Agent-readable Markdown report
        md_lines = [
            f"# 直播数据会话报告: {summary.title or summary.room_id}",
            "",
            "## 基本信息",
            f"- **房间号**: {summary.room_id} (实际长号: {summary.real_room_id})",
            f"- **主播 UID**: {summary.anchor_uid}",
            f"- **开播时间**: {summary.start_time}",
            f"- **结束时间**: {summary.end_time or '进行中'}",
            f"- **有效时长**: {summary.duration_s / 60.0:.1f} 分钟",
            "",
            "## 核心互动指标",
            f"- **总弹幕发言数**: {summary.total_danmaku_count}",
            f"- **独立发言观众**: {summary.unique_danmaku_senders}",
            f"- **营收估算总额**: ¥{summary.total_monetization_cny:.2f}",
            f"  - 礼物打赏: ¥{summary.total_gold_cny:.2f}",
            f"  - 醒目留言 (SC): ¥{summary.total_super_chat_cny:.2f}",
            f"  - 大航海购买: ¥{summary.total_guard_cny:.2f}",
            "",
            "## Top 礼物",
        ]
        if summary.top_gifts:
            for g in summary.top_gifts:
                md_lines.append(f"- {g['gift_name']}: ×{g['num']}")
        else:
            md_lines.append("- (无礼物赠送记录)")

        md_lines.extend(["", "## SuperChat 醒目留言清单"])
        if summary.super_chats:
            for sc in summary.super_chats:
                md_lines.append(f"- **¥{sc.price:.1f}** [{sc.uname}]: {sc.message}")
        else:
            md_lines.append("- (无 SuperChat 记录)")

        md_lines.extend(["", "## 大航海上舰清单"])
        if summary.guards:
            guard_map = {1: "总督", 2: "提督", 3: "舰长"}
            for gd in summary.guards:
                md_lines.append(f"- ⚓ **{gd.uname}** 开通了 {guard_map.get(gd.guard_level, '舰长')} ({gd.num}{gd.unit})")
        else:
            md_lines.append("- (无大航海记录)")

        md_lines.extend(["", "## 活跃发言者 Top 10"])
        for c in summary.top_chatters:
            md_lines.append(f"- {c['uname']}: {c['count']} 条")

        self.bundle_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        logger.info("Saved live bundles to %s", self.out_dir)
