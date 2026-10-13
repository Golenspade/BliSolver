"""BliSolver Live Streaming Subsystem (blisolver.live).

Provides automated live stream monitoring, session authentication, real-time
event collection (danmaku, gifts, superchats, guards), and session aggregation.
"""

from .collector import LiveCollector, LiveHandler
from .models import (
    LiveBaseEvent,
    LiveDanmakuRecord,
    LiveEvent,
    LiveGiftRecord,
    LiveGuardRecord,
    LiveSessionSummary,
    LiveSuperChatRecord,
    LiveSystemEvent,
)
from .monitor import LiveMonitor, RoomInfoError
from .safety import AccountSecurityViolation, SafetyGuard, SafetyViolationError
from .session import LiveSession

__all__ = (
    "AccountSecurityViolation",
    "LiveBaseEvent",
    "LiveCollector",
    "LiveDanmakuRecord",
    "LiveEvent",
    "LiveGiftRecord",
    "LiveGuardRecord",
    "LiveHandler",
    "LiveMonitor",
    "LiveSession",
    "LiveSessionSummary",
    "LiveSuperChatRecord",
    "LiveSystemEvent",
    "RoomInfoError",
    "SafetyGuard",
    "SafetyViolationError",
)
