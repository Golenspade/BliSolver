"""Offline replay through the pinned upstream models and final consumer artifacts."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("aiohttp")
web = pytest.importorskip("blivedm.models.web")

from blisolver.live.collector import LiveHandler
from blisolver.live.monitor import LiveMonitor, RoomInfoError
from blisolver.live.session import LiveSession


def test_replay_fanout_accounting_and_slow_consumer(tmp_path):
    async def replay():
        session = LiveSession(1, tmp_path)
        session._is_active = True
        session._writer_task = asyncio.create_task(session._event_writer_loop())
        handler = LiveHandler(session.event_queue)
        client = SimpleNamespace(room_id=1)

        async def consume(delay):
            items = []
            async for event in session.stream():
                items.append(event.model_dump())
                await asyncio.sleep(delay)
            return items

        fast = asyncio.create_task(consume(0))
        slow = asyncio.create_task(consume(0.001))
        for index in range(100):
            handler._on_danmaku(client, web.DanmakuMessage(
                timestamp=1700000000000, uid=index + 1, uname="观众", msg=f"内容 {index}",
                admin=1, privilege_type=3,
            ))
            await asyncio.sleep(0)
        for source in (0, 2):
            handler._on_user_toast_v2(client, web.UserToastV2Message(
                uid=1, username="观众", guard_level=3, num=1, price=138000,
                unit="*3天", source=source,
            ))
        summary = await session.stop()
        a, b = await asyncio.gather(fast, slow)
        persisted = [json.loads(line) for line in session.jsonl_path.read_text().splitlines()]
        assert a == b == persisted
        assert len(a) == 101
        assert all(e["is_admin"] and e["guard_level"] == 3 for e in a[:100])
        assert a[99]["msg"] == "内容 99"
        assert summary.total_danmaku_count == 100
        assert summary.total_guard_cny == 138
        assert summary.guards[0].unit == "*3天"
        assert json.loads(session.bundle_json_path.read_text())["total_guard_cny"] == 138
        assert "138.00" in session.bundle_md_path.read_text()
        # Existing artifacts cannot be silently mixed or overwritten.
        previous = session.jsonl_path.read_bytes()
        again = LiveSession(1, tmp_path, wait_for_live=False)
        with pytest.raises(FileExistsError):
            await again.run()
        assert session.jsonl_path.read_bytes() == previous
    asyncio.run(replay())


@pytest.mark.parametrize("failure", [False, True])
def test_lifecycle_unknown_then_end_or_error(tmp_path, failure):
    async def replay():
        monitor = AsyncMock()
        monitor.check_status.side_effect = [
            {"is_live": True, "real_room_id": 1}, {"is_live": None},
            RoomInfoError("HTTP 503") if failure else {"is_live": False},
        ]
        collector = AsyncMock()
        collector.failure = None
        collector.overflow_count = 0
        session = LiveSession(1, tmp_path, wait_for_live=False, max_duration_s=10)
        session.poll_interval_s = 0.001
        with patch("blisolver.live.session.LiveMonitor", return_value=monitor), patch(
            "blisolver.live.session.LiveCollector", return_value=collector
        ):
            if failure:
                with pytest.raises(RoomInfoError):
                    await session.run()
            else:
                await session.run()
        assert monitor.check_status.await_count == 3
        collector.stop.assert_awaited_once()
        monitor.close.assert_awaited_once()
        assert session.bundle_json_path.exists()
        assert json.loads(session.bundle_json_path.read_text())["capture_complete"] is not failure
    asyncio.run(replay())


def test_transport_failure_is_unknown():
    async def replay():
        from unittest.mock import MagicMock

        import aiohttp
        network = MagicMock()
        network.closed = False
        network.get.side_effect = aiohttp.ClientConnectionError("offline")
        result = await LiveMonitor(1, session=network).check_status()
        assert result["is_live"] is None
    asyncio.run(replay())


def test_ingress_overflow_is_visible():
    queue = asyncio.Queue(maxsize=1)
    handler = LiveHandler(queue)
    client = SimpleNamespace(room_id=1)
    message = web.DanmakuMessage(timestamp=1700000000000, msg="overflow")
    handler._on_danmaku(client, message)
    handler._on_danmaku(client, message)
    assert handler.overflow_count == 1
    assert queue.qsize() == 1


def test_start_failure_closes_resources(tmp_path):
    async def replay():
        monitor = AsyncMock()
        monitor.check_status.side_effect = RoomInfoError("HTTP 503")
        session = LiveSession(1, tmp_path, wait_for_live=False)
        with (
            patch("blisolver.live.session.LiveMonitor", return_value=monitor),
            pytest.raises(RoomInfoError, match="503"),
        ):
            await session.run()
        monitor.close.assert_awaited_once()
        assert session.bundle_json_path.exists()
    asyncio.run(replay())


def test_collector_start_failure_and_cleanup_error_preserve_cause(tmp_path):
    async def replay():
        monitor = AsyncMock()
        monitor.check_status.return_value = {"is_live": True, "real_room_id": 1}
        collector = AsyncMock()
        collector.failure = None
        collector.overflow_count = 0
        collector.start.side_effect = ValueError("original startup failure")
        collector.stop.side_effect = RuntimeError("cleanup failure")
        session = LiveSession(1, tmp_path, wait_for_live=False)
        with (
            patch("blisolver.live.session.LiveMonitor", return_value=monitor),
            patch("blisolver.live.session.LiveCollector", return_value=collector),
            pytest.raises(ValueError, match="original startup failure"),
        ):
            await session.run()
        collector.stop.assert_awaited_once()
        monitor.close.assert_awaited_once()
        assert not json.loads(session.bundle_json_path.read_text())["capture_complete"]
    asyncio.run(replay())


def test_cancelled_run_drains_and_marks_partial(tmp_path):
    async def replay():
        monitor = AsyncMock()
        monitor.check_status.return_value = {"is_live": True, "real_room_id": 1}
        collector = AsyncMock()
        collector.failure = None
        collector.overflow_count = 0
        session = LiveSession(1, tmp_path, wait_for_live=False)
        with (
            patch("blisolver.live.session.LiveMonitor", return_value=monitor),
            patch("blisolver.live.session.LiveCollector", return_value=collector),
        ):
            running = asyncio.create_task(session.run())
            while not session._is_active:
                await asyncio.sleep(0)
            handler = LiveHandler(session.event_queue)
            handler._on_danmaku(SimpleNamespace(room_id=1), web.DanmakuMessage(
                timestamp=1700000000000, msg="cancelled content",
            ))
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        collector.stop.assert_awaited_once()
        monitor.close.assert_awaited_once()
        payload = json.loads(session.bundle_json_path.read_text())
        assert payload["total_danmaku_count"] == 1
        assert payload["capture_complete"] is False
        assert len([event async for event in session.stream()]) == 1
    asyncio.run(replay())


def test_shutdown_stops_producer_before_final_drain(tmp_path):
    async def replay():
        session = LiveSession(1, tmp_path)
        session._is_active = True
        session._writer_task = asyncio.create_task(session._event_writer_loop())
        handler = LiveHandler(session.event_queue)
        collector = AsyncMock()
        collector.failure = None
        collector.overflow_count = 0

        async def final_event():
            assert session._is_active
            handler._on_danmaku(SimpleNamespace(room_id=1), web.DanmakuMessage(
                timestamp=1700000000000, msg="last producer event",
            ))
            await asyncio.sleep(0)

        collector.stop.side_effect = final_event
        session._collector = collector
        summary = await session.stop()
        assert summary.total_danmaku_count == 1
        assert summary.capture_complete
        assert session.event_queue.empty()
        assert [e.msg async for e in session.stream()] == ["last producer event"]
    asyncio.run(replay())


def test_overflow_fails_and_delivers_explicit_partial_bundle(tmp_path):
    async def replay():
        session = LiveSession(1, tmp_path)
        session._is_active = True
        session.event_queue = asyncio.Queue(maxsize=1)
        handler = LiveHandler(session.event_queue)
        client = SimpleNamespace(room_id=1)
        for message in ("accepted", "overflow"):
            handler._on_danmaku(client, web.DanmakuMessage(
                timestamp=1700000000000, msg=message,
            ))
        collector = AsyncMock()
        collector.failure = None
        collector.overflow_count = handler.overflow_count
        session._collector = collector
        session._writer_task = asyncio.create_task(session._event_writer_loop())
        with pytest.raises(RuntimeError, match="incomplete"):
            await session.stop()
        payload = json.loads(session.bundle_json_path.read_text())
        assert payload["capture_complete"] is False
        assert payload["dropped_event_count"] == 1
        assert payload["total_danmaku_count"] == 1
        assert [e.msg async for e in session.stream()] == ["accepted"]
    asyncio.run(replay())


def test_consumer_waits_through_delayed_session_start(tmp_path):
    async def replay():
        monitor = AsyncMock()
        entered = asyncio.Event()
        release = asyncio.Event()
        session = LiveSession(1, tmp_path, wait_for_live=False, max_duration_s=0)

        async def initial_status():
            entered.set()
            await release.wait()
            return {"is_live": True, "real_room_id": 1}

        async def emit():
            handler = LiveHandler(session.event_queue)
            handler._on_danmaku(SimpleNamespace(room_id=1), web.DanmakuMessage(
                timestamp=1700000000000, msg="after delayed startup",
            ))

        async def consume():
            return [event.msg async for event in session.stream()]

        monitor.check_status.side_effect = initial_status
        collector = AsyncMock()
        collector.failure = None
        collector.overflow_count = 0
        collector.start.side_effect = emit
        with (
            patch("blisolver.live.session.LiveMonitor", return_value=monitor),
            patch("blisolver.live.session.LiveCollector", return_value=collector),
        ):
            consumer = asyncio.create_task(consume())
            await asyncio.sleep(0)
            running = asyncio.create_task(session.run())
            await entered.wait()
            await asyncio.sleep(0.03)
            assert not consumer.done()
            release.set()
            await running
            assert await asyncio.wait_for(consumer, 1) == ["after delayed startup"]
    asyncio.run(replay())


def test_real_upstream_fatal_task_is_not_complete_capture(tmp_path):
    async def replay():
        import blivedm

        from blisolver.live.collector import LiveCollector

        monitor = AsyncMock()
        monitor.check_status.return_value = {"is_live": True, "real_room_id": 1}

        async def fatal(*args):
            raise RuntimeError("fatal upstream startup")

        session = LiveSession(1, tmp_path, wait_for_live=False, max_duration_s=1)
        session.poll_interval_s = 0.02
        with (
            patch("blisolver.live.session.LiveMonitor", return_value=monitor),
            patch.object(blivedm.BLiveClient, "_network_coroutine", fatal),
            pytest.raises(RuntimeError, match="fatal upstream startup"),
        ):
            await session.run()
        payload = json.loads(session.bundle_json_path.read_text())
        assert payload["capture_complete"] is False
        assert not session._is_active
        assert session._collector is None
        monitor.close.assert_awaited_once()
        # Requested healthy shutdown is not itself a failed capture.
        collector = LiveCollector(1)
        collector._handler.stopping = True
        collector._handler.on_client_stopped(SimpleNamespace(room_id=1), None)
        assert collector.failure is None
    asyncio.run(replay())
