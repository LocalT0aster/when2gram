"""Regression checks for the Bot API 10.4 compatibility shim."""

import json
import zlib
from datetime import date, timedelta
from importlib.resources import files
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Update

from when2gram.bot import widgets
from when2gram.db.models import Event, EventDay
from when2gram.db.repositories import SubmissionResult


def test_html_payload_fits_transport_and_latency_budgets() -> None:
    html = files("when2gram.bot").joinpath("availability_widget.html").read_bytes()
    assert len(html) <= 14_600
    assert len(html) <= 32_768
    assert len(zlib.compress(html, level=9)) <= 4096
    assert b"TelegramWidget.request" not in html  # calls go through T.request
    assert b"T.request(action)" in html


@pytest.mark.asyncio
async def test_widget_update_reaches_handler_in_existing_dispatcher(monkeypatch) -> None:
    capture = AsyncMock()
    monkeypatch.setattr(widgets, "handle_widget_query", capture)
    dispatcher = widgets.WidgetDispatcher()
    query = {
        "id": "widget-1",
        "from": {"id": 42, "is_bot": False, "first_name": "Ada"},
        "chat": {"id": 42, "type": "private"},
        "data": "v:opaque:0",
        "type": {"type": "data"},
        "chat_instance": "private",
    }
    async with Bot(token="123456:testing") as bot:
        update = Update.model_validate(
            {"update_id": 1, "widget_query": query}, context={"bot": bot}
        )
        await dispatcher.feed_update(bot, update, session_factory="sessions")
    capture.assert_awaited_once()
    assert capture.await_args.args == (query,)
    assert capture.await_args.kwargs["session_factory"] == "sessions"


@pytest.mark.asyncio
async def test_old_schedule_cannot_write_availability(monkeypatch) -> None:
    future = date.today() + timedelta(days=1)
    event = Event(
        id=10,
        token="opaque",
        title="Review",
        organizer_id=42,
        start_minute=600,
        end_minute=1200,
        days=[EventDay(day=future)],
    )
    monkeypatch.setattr(widgets, "get_event_by_token", AsyncMock(return_value=event))
    monkeypatch.setattr(widgets, "event_is_expired", lambda _: False)
    save = AsyncMock()
    monkeypatch.setattr(widgets, "save_submitted_availability", save)

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.begin.return_value = session
    factory = MagicMock(return_value=session)
    bot = AsyncMock()
    request = {
        "id": "widget-2",
        "from": {"id": 42, "username": "ada", "first_name": "Ada"},
        "chat": {"id": 42, "type": "private"},
        # 540 = 0x21c: a stale widget's old schedule.
        "data": f"s:opaque:{future:%Y%m%d}:21c:4b0:1",
    }
    await widgets.handle_widget_query(request, bot, factory)
    save.assert_not_awaited()
    method = bot.await_args.args[0]
    assert isinstance(method, widgets.AnswerWidgetQuery)
    answer = json.loads(method.data)
    assert answer["ok"] == 0
    assert "Schedule changed" in answer["error"]


@pytest.mark.asyncio
async def test_widget_rejects_queries_from_other_chats() -> None:
    bot = AsyncMock()
    request = {
        "id": "widget-3",
        "from": {"id": 42},
        "chat": {"id": -100123, "type": "supergroup"},
        "data": "v:opaque:0",
    }
    await widgets.handle_widget_query(request, bot, None)
    answer = json.loads(bot.await_args.args[0].data)
    assert answer["ok"] == 0
    assert "Private chat" in answer["error"]


@pytest.mark.asyncio
async def test_payload_counts_and_hex_mask(monkeypatch) -> None:
    future = date.today() + timedelta(days=1)
    event = Event(
        id=10,
        token="opaque",
        title="Review",
        organizer_id=42,
        start_minute=9 * 60,
        end_minute=10 * 60,
        days=[EventDay(day=future)],
    )
    monkeypatch.setattr(widgets, "get_user_availability_masks", AsyncMock(return_value=[3]))
    monkeypatch.setattr(
        widgets, "get_event_availability_masks", AsyncMock(return_value=(2, [[3, 2]]))
    )
    session = AsyncMock()
    session.get.return_value = None
    payload = await widgets._day_payload(session, event, 42, 0, initial=True)
    assert payload["mask"] == "3"
    assert payload["counts"] == [1, 2, 0, 0]
    assert payload["responses"] == 2
    assert payload["t"] == "opaque"


@pytest.mark.asyncio
async def test_widget_accepts_explicit_empty_availability(monkeypatch) -> None:
    future = date.today() + timedelta(days=1)
    event = Event(
        id=10,
        token="opaque",
        title="Review",
        organizer_id=42,
        start_minute=540,
        end_minute=600,
        days=[EventDay(day=future)],
    )
    monkeypatch.setattr(widgets, "get_event_by_token", AsyncMock(return_value=event))
    monkeypatch.setattr(widgets, "event_is_expired", lambda _: False)
    monkeypatch.setattr(widgets, "upsert_user", AsyncMock())
    monkeypatch.setattr(widgets, "refresh_inline_invitations", AsyncMock())
    save = AsyncMock(return_value=SubmissionResult(response_count=1, target_reached=False))
    monkeypatch.setattr(widgets, "save_submitted_availability", save)
    monkeypatch.setattr(
        widgets,
        "_day_payload",
        AsyncMock(
            return_value={
                "ok": 1,
                "i": 0,
                "days": 1,
                "day": future.isoformat(),
                "mask": "0",
                "counts": [0] * 4,
                "responses": 1,
                "submitted": True,
                "title": "Review",
                "start": 540,
                "end": 600,
            }
        ),
    )
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.begin.return_value = session
    bot = AsyncMock()
    await widgets.handle_widget_query(
        {
            "id": "widget-4",
            "from": {"id": 42, "first_name": "Ada"},
            "chat": {"id": 42, "type": "private"},
            "data": f"s:opaque:{future:%Y%m%d}:21c:258:0",
        },
        bot,
        MagicMock(return_value=session),
    )
    assert save.await_args.kwargs["masks"] == [0]
    assert json.loads(bot.await_args.args[0].data)["ok"] == 1