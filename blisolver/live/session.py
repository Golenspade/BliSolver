"""Full live session lifecycle orchestrator and post-stream aggregator.

Manages:
1. Streaming persistence to `events.jsonl` with per-event flush.
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

from pydantic import TypeAdapter

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
        self.poll_interval_s = 30.0
        self._owns_output = False
        self._capture_complete = True
        self._dropped_event_count = 0
        self._finished = False

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
        with open(self.jsonl_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
            while self._is_active or not self.event_queue.empty():
                try:
                    event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
                except TimeoutError:
                    continue

                line = json.dumps(event.model_dump(), ensure_ascii=False)
                f.write(line + "\n")
                f.flush()
                self._all_events.append(event)
                self.event_queue.task_done()

    async def start(self) -> dict[str, Any]:
        """Initiate session: wait for broadcaster to go live if configured, then connect."""
        SafetyGuard.assert_read_only("start_live_session")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Refuse ambiguous append/resume rather than mixing broadcasts.
        if self.jsonl_path.exists():
            raise FileExistsError(f"Session journal already exists: {self.jsonl_path}")
        self.jsonl_path.touch(exist_ok=False)
        self._owns_output = True
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
        errors = []
        # Stop the producer before allowing the writer to finish draining.
        if self._collector:
            try:
                await self._collector.stop()
            except Exception as exc:  # noqa: BLE001 - finish all cleanup, then re-raise
                errors.append(exc)
            finally:
                if self._collector.overflow_count:
                    self._dropped_event_count = self._collector.overflow_count
                    errors.append(RuntimeError("Live event queue overflow: capture is incomplete"))
                if self._collector.failure is not None:
                    errors.append(self._collector.failure)
                self._collector = None
        self._is_active = False
        if self._monitor:
            try:
                await self._monitor.close()
            except Exception as exc:  # noqa: BLE001 - finish all cleanup, then re-raise
                errors.append(exc)
            finally:
                self._monitor = None
        if self._writer_task:
            try:
                await self._writer_task
            except Exception as exc:  # noqa: BLE001 - finish all cleanup, then re-raise
                errors.append(exc)
            finally:
                self._writer_task = None

        self._finished = True
        self._end_time = datetime.now(UTC)
        if errors:
            self._capture_complete = False
        summary = self.aggregate()
        self.write_bundles(summary)
        if errors:
            raise errors[0]
        return summary

    async def run(self) -> LiveSessionSummary:
        """Run the session lifecycle from start until stream ends or max_duration expires."""
        failure = None
        try:
            await self.start()
            deadline = (
                asyncio.get_running_loop().time() + self.max_duration_s
                if self.max_duration_s is not None else None
            )
            while True:
                remaining = deadline - asyncio.get_running_loop().time() if deadline else None
                if remaining is not None and remaining <= 0:
                    break
                await asyncio.sleep(min(self.poll_interval_s, remaining)
                                    if remaining is not None else self.poll_interval_s)
                if self._writer_task.done():
                    await self._writer_task
                    raise RuntimeError("Live journal writer stopped unexpectedly")
                if self._collector.failure is not None:
                    raise self._collector.failure
                if self._collector.overflow_count:
                    raise RuntimeError("Live event queue overflow: capture is incomplete")
                info = await self._monitor.check_status()
                if info["is_live"] is False:
                    break
        except BaseException as exc:
            self._capture_complete = False
            failure = exc
            raise
        finally:
            if self._owns_output:
                try:
                    summary = await self.stop()
                except Exception:
                    if failure is None:
                        raise
                    logger.exception("Cleanup failed while handling session failure")
        return summary

    def stream(self) -> AsyncIterator[LiveEvent]:
        """Replay the session journal with an independent cursor per consumer.

        Slow consumers never block collection or steal persistence events. Each
        reader starts at the beginning and can finish draining after stop.
        """
        async def _generator() -> AsyncIterator[LiveEvent]:
            adapter = TypeAdapter(LiveEvent)
            while not self.jsonl_path.exists():
                if self._finished:
                    return
                await asyncio.sleep(0.01)
            with self.jsonl_path.open(encoding="utf-8") as journal:
                while True:
                    position = journal.tell()
                    line = journal.readline()
                    if line.endswith("\n"):
                        yield adapter.validate_json(line)
                    else:
                        journal.seek(position)
                        if self._finished and (
                            self._writer_task is None or self._writer_task.done()
                        ):
                            break
                        await asyncio.sleep(0.01)
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
            capture_complete=self._capture_complete,
            dropped_event_count=self._dropped_event_count,
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
            f"- **采集完整**: {summary.capture_complete}",
            f"- **队列溢出丢失事件数**: {summary.dropped_event_count}",
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
