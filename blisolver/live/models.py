"""Data contracts for Bilibili live streaming events and session aggregations.

Downstream consumers (analytics, Atlas bundles, AI agents) operate against these
strongly-typed, validated Pydantic models rather than raw undocumented dicts.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LiveModel(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class LiveBaseEvent(LiveModel):
    event_type: str = Field(description="Event discriminator: danmaku, gift, super_chat, guard, system")
    timestamp: float = Field(description="Event content timestamp (epoch seconds)")
    room_id: int = Field(description="Target live room ID")
    uid: int = Field(default=0, description="User mid / UID (0 if anonymous/unauthenticated)")
    uname: str = Field(default="", description="User display name")


class LiveDanmakuRecord(LiveBaseEvent):
    event_type: Literal["danmaku"] = "danmaku"
    msg: str = Field(description="Danmaku text content")
    mode: int = Field(default=1, description="Display mode (1=scroll, 4=bottom, 5=top)")
    font_size: int = Field(default=25)
    color: int = Field(default=0xFFFFFF, description="RGB color integer")
    medal_name: str | None = Field(default=None, description="Fans medal name")
    medal_level: int = Field(default=0, description="Fans medal level")
    guard_level: int = Field(default=0, description="0=None, 1=Governor, 2=Admiral, 3=Captain")
    is_admin: bool = Field(default=False, description="Whether sender is room admin / moderator")
    emoticon_url: str | None = Field(default=None, description="Image URL if sticker/emoticon")


class LiveGiftRecord(LiveBaseEvent):
    event_type: Literal["gift"] = "gift"
    gift_name: str = Field(description="Gift display name")
    gift_id: int = Field(description="Platform gift ID")
    num: int = Field(default=1, ge=1, description="Quantity of gift items")
    coin_type: Literal["gold", "silver"] = Field(default="gold", description="Battery (gold) or silver")
    total_coin: int = Field(default=0, ge=0, description="Total cost in seeds/batteries")
    cny_value: float = Field(default=0.0, ge=0.0, description="Estimated CNY equivalent value")
    guard_level: int = Field(default=0, description="Sender fleet status")
    action: str = Field(default="投喂", description="Action descriptor, e.g., '投喂' or '赠送'")


class LiveSuperChatRecord(LiveBaseEvent):
    event_type: Literal["super_chat"] = "super_chat"
    price: float = Field(ge=0.0, description="SuperChat payment amount in CNY")
    message: str = Field(description="SuperChat message text")
    duration_s: int = Field(default=60, ge=1, description="Screen pin duration in seconds")
    message_trans: str | None = Field(default=None, description="Optional Japanese or foreign translation")
    sc_id: int | None = Field(default=None, description="Platform SuperChat ID")


class LiveGuardRecord(LiveBaseEvent):
    event_type: Literal["guard"] = "guard"
    guard_level: int = Field(ge=1, le=3, description="1=Governor(总督), 2=Admiral(提督), 3=Captain(舰长)")
    num: int = Field(default=1, ge=1, description="Duration unit count, e.g., 1 month")
    unit: str = Field(default="月", description="Unit string ('月')")
    cny_value: float = Field(default=0.0, ge=0.0, description="Estimated CNY value")


class LiveSystemEvent(LiveBaseEvent):
    event_type: Literal["system"] = "system"
    action: str = Field(description="System event name: live_start, live_end, heartbeat, warning")
    detail: dict[str, Any] = Field(default_factory=dict)


LiveEvent = Annotated[
    LiveDanmakuRecord | LiveGiftRecord | LiveSuperChatRecord | LiveGuardRecord | LiveSystemEvent,
    Field(discriminator="event_type")
]


class LiveSessionSummary(LiveModel):
    """Aggregated financial and interaction summary for an entire live broadcast."""

    room_id: int
    real_room_id: int
    title: str = ""
    anchor_name: str = ""
    anchor_uid: int = 0
    start_time: str = Field(description="ISO 8601 start timestamp")
    end_time: str | None = Field(default=None, description="ISO 8601 completion timestamp")
    duration_s: float = Field(default=0.0, ge=0.0)

    capture_complete: bool = True
    dropped_event_count: int = Field(default=0, ge=0)

    # Core volume metrics
    total_danmaku_count: int = Field(default=0, ge=0)
    unique_danmaku_senders: int = Field(default=0, ge=0)

    # Financial monetization metrics
    total_gold_cny: float = Field(default=0.0, ge=0.0, description="Total gift revenue (CNY)")
    total_silver_coins: int = Field(default=0, ge=0)
    total_super_chat_cny: float = Field(default=0.0, ge=0.0, description="Total SuperChat revenue (CNY)")
    total_guard_cny: float = Field(default=0.0, ge=0.0, description="Total Fleet buy revenue (CNY)")
    total_monetization_cny: float = Field(default=0.0, ge=0.0, description="Sum of gifts, SC, and guards")

    # Itemized breakdowns for downstream analysis
    super_chats: list[LiveSuperChatRecord] = Field(default_factory=list)
    guards: list[LiveGuardRecord] = Field(default_factory=list)
    top_gifts: list[dict[str, Any]] = Field(default_factory=list)
    top_chatters: list[dict[str, Any]] = Field(default_factory=list)
