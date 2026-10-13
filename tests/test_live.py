"""Unit tests for Bilibili live stream acquisition and data module (blisolver.live).

Enforces:
1. Strict read-only account safety (TOS anti-ban invariants).
2. Live room monitoring and status detection.
3. Normalized data models for Danmaku, Gift, SuperChat, and Guard buy.
4. LiveSession lifecycle, real-time JSONL persistence, and bundle aggregation.
5. CLI parser and dispatch integration.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blisolver.cli import build_parser, parse_args
from blisolver.live.collector import LiveHandler
from blisolver.live.models import (
    LiveDanmakuRecord,
    LiveGiftRecord,
    LiveGuardRecord,
    LiveSessionSummary,
    LiveSuperChatRecord,
)
from blisolver.live.monitor import LiveMonitor, RoomInfoError
from blisolver.live.safety import AccountSecurityViolation, SafetyGuard
from blisolver.live.session import LiveSession

# --- 1. Data Models ------------------------------------------------------------------------


class TestLiveModels:
    def test_danmaku_record_serialization(self):
        record = LiveDanmakuRecord(
            timestamp=1700000000.0,
            room_id=21452505,
            uid=123456,
            uname="TestViewer",
            msg="Hello BliSolver!",
            medal_name="粉丝团",
            medal_level=12,
            guard_level=3,
        )
        data = record.model_dump()
        assert data["event_type"] == "danmaku"
        assert data["msg"] == "Hello BliSolver!"
        assert data["medal_name"] == "粉丝团"
        assert data["medal_level"] == 12

        # Roundtrip via json
        json_str = record.model_dump_json()
        restored = LiveDanmakuRecord.model_validate_json(json_str)
        assert restored.uname == "TestViewer"
        assert restored.guard_level == 3

    def test_gift_record_monetization_calculation(self):
        gold_gift = LiveGiftRecord(
            timestamp=1700000001.0,
            room_id=21452505,
            uid=1001,
            uname="GifterA",
            gift_name="小心心",
            gift_id=30001,
            num=10,
            coin_type="gold",
            total_coin=1000,
            cny_value=1.0,
        )
        assert gold_gift.cny_value == 1.0
        assert gold_gift.coin_type == "gold"

        silver_gift = LiveGiftRecord(
            timestamp=1700000002.0,
            room_id=21452505,
            uid=1002,
            uname="GifterB",
            gift_name="辣条",
            gift_id=1,
            num=5,
            coin_type="silver",
            total_coin=500,
            cny_value=0.0,
        )
        assert silver_gift.cny_value == 0.0
        assert silver_gift.coin_type == "silver"

    def test_super_chat_record(self):
        sc = LiveSuperChatRecord(
            timestamp=1700000003.0,
            room_id=21452505,
            uid=2001,
            uname="SCUser",
            price=100.0,
            message="Support the stream!",
            start_time=1700000003,
            end_time=1700000063,
        )
        assert sc.event_type == "super_chat"
        assert sc.price == 100.0
        assert sc.message == "Support the stream!"

    def test_guard_record_pricing(self):
        guard_captain = LiveGuardRecord(
            timestamp=1700000004.0,
            room_id=21452505,
            uid=3001,
            uname="CaptainFan",
            guard_level=3,
            num=1,
            unit="月",
            cny_value=198.0,
        )
        assert guard_captain.guard_level == 3
        assert guard_captain.cny_value == 198.0


# --- 2. SafetyGuard (TOS Compliance) ------------------------------------------------------


class TestSafetyGuard:
    def test_assert_read_only_permits_safe_actions(self):
        SafetyGuard.assert_read_only("danmaku_received")
        SafetyGuard.assert_read_only("gift_received")
        SafetyGuard.assert_read_only("room/v1/Room/get_info")
        SafetyGuard.assert_read_only("query_status", "https://api.live.bilibili.com/room/v1/Room/get_info")

    def test_assert_read_only_blocks_writing_or_interactive_actions(self):
        forbidden_attempts = [
            ("send_danmaku", "https://api.live.bilibili.com/msg/send"),
            ("like_stream", "https://api.live.bilibili.com/like/click"),
            ("post_comment", "https://api.bilibili.com/x/v2/reply/add"),
            ("send_gift", "https://api.live.bilibili.com/gift/v2/live/bag_send"),
            ("follow_anchor", "https://api.bilibili.com/x/relation/modify"),
            ("share_stream", "https://api.live.bilibili.com/share"),
        ]
        for action, url in forbidden_attempts:
            with pytest.raises(AccountSecurityViolation) as exc_info:
                SafetyGuard.assert_read_only(action, url)
            assert "violation" in str(exc_info.value).lower() or "read-only" in str(exc_info.value).lower()

    def test_wait_jitter_range(self):
        async def _test():
            guard = SafetyGuard(min_interval_s=15.0, max_interval_s=30.0)
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                waited = await guard.wait_jitter(min_s=15.0, max_s=30.0)
                assert 15.0 <= waited <= 30.0
                mock_sleep.assert_awaited_once_with(waited)
        asyncio.run(_test())

    def test_safe_headers_generation(self):
        guard = SafetyGuard(sessdata="sample_cookie_val_12345")
        headers = guard.safe_headers(room_id=21452505)
        assert "User-Agent" in headers
        assert "21452505" in headers.get("Referer", "")
        assert "SESSDATA=sample_cookie_val_12345" in headers.get("Cookie", "")

    def test_credential_masking(self):
        assert SafetyGuard.mask_credential(None) == "<none>"
        assert SafetyGuard.mask_credential("short") == "***"
        masked = SafetyGuard.mask_credential("abcdef1234567890xyz")
        assert masked.startswith("abcd...")
        assert masked.endswith("0xyz")
        assert "123456789" not in masked


# --- 3. LiveMonitor (Status Polling) -------------------------------------------------------


class TestLiveMonitor:
    def test_check_status_live_stream(self):
        async def _test():
            mock_response_data = {
                "code": 0,
                "msg": "ok",
                "data": {
                    "room_id": 1024,
                    "short_id": 0,
                    "uid": 99999,
                    "live_status": 1,
                    "title": "Bilibili Live Test Room",
                    "cover": "http://example.com/cover.jpg",
                    "live_time": "2026-10-13 12:00:00",
                    "area_name": "虚拟主播",
                },
            }

            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response_data)

            mock_session = MagicMock()
            mock_session.closed = False
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            monitor = LiveMonitor(room_id=1024, session=mock_session)
            status = await monitor.check_status()

            assert status["is_live"] is True
            assert status["live_status"] == 1
            assert status["real_room_id"] == 1024
            assert status["anchor_uid"] == 99999
            assert status["title"] == "Bilibili Live Test Room"
        asyncio.run(_test())

    def test_check_status_offline(self):
        async def _test():
            mock_response_data = {
                "code": 0,
                "data": {
                    "room_id": 1024,
                    "uid": 99999,
                    "live_status": 0,
                    "title": "Offline Room",
                },
            }
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response_data)

            mock_session = MagicMock()
            mock_session.closed = False
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            monitor = LiveMonitor(room_id=1024, session=mock_session)
            status = await monitor.check_status()

            assert status["is_live"] is False
            assert status["live_status"] == 0
        asyncio.run(_test())

    def test_check_status_api_error(self):
        async def _test():
            mock_response_data = {"code": 19002000, "message": "Room not found"}
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response_data)

            mock_session = MagicMock()
            mock_session.closed = False
            mock_session.get.return_value.__aenter__.return_value = mock_resp

            monitor = LiveMonitor(room_id=99999999, session=mock_session)
            with pytest.raises(RoomInfoError):
                await monitor.check_status()
        asyncio.run(_test())


# --- 4. LiveHandler & Collector Event Dispatch --------------------------------------------


class TestLiveHandler:
    def test_on_danmaku_dispatch(self):
        queue: asyncio.Queue = asyncio.Queue()
        dispatched_events = []
        handler = LiveHandler(queue, listener=dispatched_events.append)

        client = SimpleNamespace(room_id=21452505)
        msg = SimpleNamespace(
            timestamp=1700000010000,  # millisecond format
            uid=444555,
            uname="DanmakuSender",
            msg="666666",
            mode=1,
            font_size=25,
            color=0xFFFFFF,
            medal_name="测试勋章",
            medal_level=10,
            guard_level=3,
            is_admin=False,
            emoticon_url=None,
            emoticon_options=None,
        )

        handler._on_danmaku(client, msg)

        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert isinstance(item, LiveDanmakuRecord)
        assert item.uname == "DanmakuSender"
        assert item.msg == "666666"
        assert item.medal_name == "测试勋章"
        assert len(dispatched_events) == 1

    def test_on_gift_dispatch(self):
        queue: asyncio.Queue = asyncio.Queue()
        handler = LiveHandler(queue)

        client = SimpleNamespace(room_id=21452505)
        msg = SimpleNamespace(
            timestamp=1700000011,
            uid=666777,
            uname="GenerousUser",
            gift_name="B坷垃",
            gift_id=20002,
            num=2,
            coin_type="gold",
            total_coin=19800,
            guard_level=2,
            action="投喂",
        )

        handler._on_gift(client, msg)

        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert isinstance(item, LiveGiftRecord)
        assert item.gift_name == "B坷垃"
        assert item.cny_value == 19.80
        assert item.num == 2

    def test_on_super_chat_dispatch(self):
        queue: asyncio.Queue = asyncio.Queue()
        handler = LiveHandler(queue)

        client = SimpleNamespace(room_id=21452505)
        msg = SimpleNamespace(
            start_time=1700000012,
            end_time=1700000072,
            uid=888999,
            uname="SCMaster",
            price=50.0,
            message="Support from tester",
        )

        handler._on_super_chat(client, msg)

        assert queue.qsize() == 1
        item = queue.get_nowait()
        assert isinstance(item, LiveSuperChatRecord)
        assert item.price == 50.0
        assert item.message == "Support from tester"


# --- 5. LiveSession Lifecycle and Aggregation ----------------------------------------------


class TestLiveSession:
    def test_aggregation_calculations(self, tmp_path: Path):
        session = LiveSession(room_id=12345, out_dir=tmp_path)
        session._start_time = datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC)
        session._end_time = datetime(2026, 10, 13, 11, 0, 0, tzinfo=UTC)
        session._room_info = {
            "real_room_id": 12345,
            "title": "Unit Test Stream",
            "anchor_uid": 88888,
            "raw": {"uname": "TestAnchor"},
        }

        # Populate synthetic events
        session._all_events = [
            # Danmaku
            LiveDanmakuRecord(timestamp=1.0, room_id=12345, uid=1, uname="Alice", msg="Hi"),
            LiveDanmakuRecord(timestamp=2.0, room_id=12345, uid=1, uname="Alice", msg="Hello"),
            LiveDanmakuRecord(timestamp=3.0, room_id=12345, uid=2, uname="Bob", msg="Hi Alice"),
            # Gold gift (1000 gold = 1.0 CNY)
            LiveGiftRecord(
                timestamp=4.0,
                room_id=12345,
                uid=3,
                uname="Charlie",
                gift_name="小心心",
                gift_id=1,
                num=10,
                coin_type="gold",
                total_coin=10000,
                cny_value=10.0,
            ),
            # Silver gift (0 CNY)
            LiveGiftRecord(
                timestamp=5.0,
                room_id=12345,
                uid=4,
                uname="Dave",
                gift_name="辣条",
                gift_id=2,
                num=5,
                coin_type="silver",
                total_coin=500,
                cny_value=0.0,
            ),
            # SuperChat (50 CNY)
            LiveSuperChatRecord(
                timestamp=6.0,
                room_id=12345,
                uid=5,
                uname="Eve",
                price=50.0,
                message="Nice stream!",
            ),
            # Guard Buy (198 CNY)
            LiveGuardRecord(
                timestamp=7.0,
                room_id=12345,
                uid=6,
                uname="Frank",
                guard_level=3,
                num=1,
                cny_value=198.0,
            ),
        ]

        summary = session.aggregate()

        assert summary.room_id == 12345
        assert summary.total_danmaku_count == 3
        assert summary.unique_danmaku_senders == 2  # Alice (uid 1) and Bob (uid 2)
        assert summary.total_gold_cny == 10.0
        assert summary.total_silver_coins == 500
        assert summary.total_super_chat_cny == 50.0
        assert summary.total_guard_cny == 198.0
        assert summary.total_monetization_cny == 10.0 + 50.0 + 198.0  # 258.00

        # Top chatters check
        assert len(summary.top_chatters) == 2
        assert summary.top_chatters[0]["uname"] == "Alice"
        assert summary.top_chatters[0]["count"] == 2

        # Top gifts check
        assert len(summary.top_gifts) == 2

    def test_write_bundles(self, tmp_path: Path):
        session = LiveSession(room_id=54321, out_dir=tmp_path)
        session._start_time = datetime(2026, 10, 13, 10, 0, 0, tzinfo=UTC)
        session._end_time = datetime(2026, 10, 13, 10, 30, 0, tzinfo=UTC)
        session._room_info = {"title": "Bundle Test"}

        summary = session.aggregate()
        session.write_bundles(summary)

        # Check JSON bundle
        assert session.bundle_json_path.exists()
        bundle_json_data = json.loads(session.bundle_json_path.read_text(encoding="utf-8"))
        assert bundle_json_data["room_id"] == 54321
        assert bundle_json_data["total_danmaku_count"] == 0

        # Check Markdown bundle
        assert session.bundle_md_path.exists()
        md_text = session.bundle_md_path.read_text(encoding="utf-8")
        assert "# 直播数据会话报告" in md_text
        assert "54321" in md_text

    def test_stream_async_iterator(self, tmp_path: Path):
        async def _test():
            session = LiveSession(room_id=999, out_dir=tmp_path)
            session._is_active = True

            event = LiveDanmakuRecord(timestamp=1.0, room_id=999, uid=10, uname="Streamer", msg="Ping")
            await session.event_queue.put(event)

            received = []
            gen = session.stream()

            async def consume():
                async for ev in gen:
                    received.append(ev)
                    break

            await asyncio.wait_for(consume(), timeout=2.0)
            assert len(received) == 1
            assert received[0].msg == "Ping"
        asyncio.run(_test())


# --- 6. CLI Integration -------------------------------------------------------------------


class TestLiveCLI:
    def test_cli_parser_recognizes_live_flags(self):
        p = build_parser()
        args = p.parse_args([
            "live", "21452505",
            "--wait",
            "--duration", "120",
            "--out", "out/custom-live",
            "--sessdata", "test_sess",
            "--json",
        ])
        assert args.command == "live"
        assert args.room_id == "21452505"
        assert args.wait is True
        assert args.duration == 120.0
        assert args.out == "out/custom-live"
        assert args.sessdata == "test_sess"
        assert args.as_json is True

    def test_cli_parser_url_format(self):
        p = build_parser()
        args = p.parse_args(["live", "https://live.bilibili.com/21452505"])
        assert args.command == "live"
        assert "21452505" in args.room_id

    def test_run_live_dispatch(self, tmp_path: Path, capsys):
        from blisolver.cli import _run_live

        args = parse_args([
            "live", "21452505",
            "--out", str(tmp_path),
            "--json",
        ])

        mock_summary = LiveSessionSummary(
            room_id=21452505,
            real_room_id=21452505,
            title="Test",
            anchor_name="Host",
            anchor_uid=111,
            start_time="2026-10-13T10:00:00",
            end_time="2026-10-13T11:00:00",
            duration_s=3600.0,
            total_danmaku_count=42,
            unique_danmaku_senders=15,
            total_gold_cny=100.0,
            total_silver_coins=0,
            total_super_chat_cny=50.0,
            total_guard_cny=198.0,
            total_monetization_cny=348.0,
            super_chats=[],
            guards=[],
            top_gifts=[],
            top_chatters=[],
        )

        with patch("blisolver.live.session.LiveSession.run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_summary
            exit_code = _run_live(args)
            assert exit_code == 0

        captured = capsys.readouterr()
        # stdout should have valid JSON
        result = json.loads(captured.out)
        assert result["ok"] is True
        assert result["room_id"] == 21452505
        assert result["summary"]["total_danmaku_count"] == 42
        assert result["summary"]["total_monetization_cny"] == 348.0
