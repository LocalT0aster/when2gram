"""Bot API 10.4 widget transport and availability handling for aiogram 3.31.

This extension consumes widget_query in aiogram's existing long poller.
Remove it when aiogram adds first-class support for the new update.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Any, ClassVar

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from when2gram.bot.keyboards.event_creation import (
    event_preview_keyboard,
    event_responses_keyboard,
)
from when2gram.bot.routers.inline import refresh_inline_invitations
from when2gram.db.models import Event, Response
from when2gram.db.repositories import (
    event_is_expired,
    get_event_availability_masks,
    get_event_by_token,
    get_user_availability_masks,
    save_submitted_availability,
    upsert_user,
)
from when2gram.domain.availability import aggregate_counts

log = logging.getLogger(__name__)


class SendWidget(TelegramMethod[Message]):
    __returning__: ClassVar[type[Message]] = Message
    __api_method__: ClassVar[str] = "sendWidget"

    chat_id: int
    html: str
    chat_data: str
    allow_queries: bool = True
    reply_markup: InlineKeyboardMarkup | None = None


class AnswerWidgetQuery(TelegramMethod[bool]):
    __returning__: ClassVar[type[bool]] = bool
    __api_method__: ClassVar[str] = "answerWidgetQuery"

    widget_query_id: str
    data: str


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _future_days(event: Event) -> list[date]:
    return [event_day.day for event_day in event.days if event_day.day >= date.today()]


async def _day_payload(
    session: AsyncSession,
    event: Event,
    user_id: int,
    index: int,
    *,
    initial: bool = False,
) -> dict[str, Any]:
    days = _future_days(event)
    if not 0 <= index < len(days):
        raise ValueError("This event day is no longer available")
    day = days[index]
    mask = (
        await get_user_availability_masks(
            session, event_id=event.id, user_id=user_id, days=[day]
        )
    )[0]
    response = await session.get(Response, (event.id, user_id))
    submitted = bool(response and response.submitted_at is not None)
    respondents, masks = await get_event_availability_masks(session, event.id)
    event_day_index = next(i for i, event_day in enumerate(event.days) if event_day.day == day)
    counts = aggregate_counts(masks[event_day_index])
    slot_count = (event.end_minute - event.start_minute) // 15
    result: dict[str, Any] = {
        "ok": 1,
        "i": index,
        "days": len(days),
        "day": day.isoformat(),
        "mask": format(mask, "x"),
        "counts": counts[:slot_count],
        "responses": respondents,
        "submitted": submitted,
    }
    result.update(title=event.title[:72], start=event.start_minute, end=event.end_minute)
    if initial:
        result["t"] = event.token
    return result


@lru_cache(maxsize=1)
def _widget_html() -> str:
    return files("when2gram.bot").joinpath("availability_widget.html").read_text("utf-8")


async def send_availability_widget(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    event: Event,
    user_id: int,
    *,
    organizer: bool,
) -> bool:
    """Post a private-chat widget. Return False for known API rejection."""
    if not _future_days(event) or event_is_expired(event):
        return False
    async with session_factory() as session:
        payload = await _day_payload(session, event, user_id, 0, initial=True)
    serialized = _json(payload)
    if len(serialized.encode("utf-8")) > 1024:
        # A huge title and large respondent counts can exceed chat_data limits.
        payload["title"] = event.title[:24]
        payload["counts"] = []
        serialized = _json(payload)
    if len(serialized.encode("utf-8")) > 1024:
        raise ValueError("Widget chat_data exceeds Telegram's limit")
    html = _widget_html()
    markup = (
        event_preview_keyboard(event.token)
        if organizer
        else event_responses_keyboard(event.token)
    )
    markup.inline_keyboard.append(
        [InlineKeyboardButton(text="Classic grid", callback_data=f"event:classic:{event.token}")]
    )
    try:
        await bot(
            SendWidget(
                chat_id=user_id,
                html=html,
                chat_data=serialized,
                reply_markup=markup,
            )
        )
    except TelegramBadRequest as exc:
        log.warning("Widget rejected by Telegram; using keyboard: %s", exc)
        return False
    return True


async def handle_widget_query(
    query: dict[str, Any],
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify sender and event on every request; never trust the HTML state."""
    query_id = query.get("id")
    if not isinstance(query_id, str):
        return

    answer: dict[str, Any] = {"ok": 0, "error": "Invalid request"}
    event_id: int | None = None
    organizer_id: int | None = None
    title = ""
    target_reached = False
    user = query.get("from")
    chat = query.get("chat")
    data = query.get("data")
    try:
        if not isinstance(user, dict) or not isinstance(chat, dict):
            raise ValueError("Private chat required")
        user_id = user.get("id")
        if (
            not isinstance(user_id, int)
            or chat.get("id") != user_id
            or chat.get("type") != "private"
        ):
            raise ValueError("Private chat required")
        kind = query.get("type")
        if kind is not None and (
            not isinstance(kind, dict) or kind.get("type") != "data"
        ):
            raise ValueError("Unsupported query type")
        if not isinstance(data, str) or len(data.encode("utf-8")) > 64:
            raise ValueError("Invalid action")
        parts = data.split(":")
        action = parts[0]
        if not (
            (action == "v" and len(parts) == 3)
            or (action == "s" and len(parts) == 6)
        ):
            raise ValueError("Invalid action")
        token = parts[1]
        if not token:
            raise ValueError("Invalid event")

        if action == "v":
            index_text = parts[2]
            if not index_text.isascii() or not index_text.isdigit() or len(index_text) > 4:
                raise ValueError("Invalid day")
            index = int(index_text)
        else:
            date_text, start_hex, end_hex, mask_hex = parts[2:]
            if len(date_text) != 8 or not date_text.isascii() or not date_text.isdigit():
                raise ValueError("Invalid date")
            if any(not chunk or len(chunk) > 24 for chunk in (start_hex, end_hex, mask_hex)):
                raise ValueError("Invalid availability")
            try:
                requested_day = date.fromisoformat(
                    f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}"
                )
                start_minute, end_minute, mask = (
                    int(start_hex, 16),
                    int(end_hex, 16),
                    int(mask_hex, 16),
                )
            except ValueError as exc:
                raise ValueError("Invalid availability") from exc

        async with session_factory() as session, session.begin():
            event = await get_event_by_token(session, token)
            if event is None or event_is_expired(event):
                raise ValueError("Invitation expired")
            days = _future_days(event)
            if action == "s":
                if (
                    requested_day not in days
                    or (start_minute, end_minute) != (event.start_minute, event.end_minute)
                ):
                    raise ValueError("Schedule changed; reopen the invitation")
                index = days.index(requested_day)
                if mask < 0 or mask >> ((event.end_minute - event.start_minute) // 15):
                    raise ValueError("Availability outside event time")
            if not 0 <= index < len(days):
                raise ValueError("Event day unavailable")
            if action == "s":
                await upsert_user(
                    session,
                    telegram_id=user_id,
                    username=user.get("username"),
                    first_name=user.get("first_name") or "Participant",
                )
                result = await save_submitted_availability(
                    session,
                    event_id=event.id,
                    user_id=user_id,
                    days=[days[index]],
                    masks=[mask],
                )
                event_id = event.id
                organizer_id = event.organizer_id
                title = event.title
                target_reached = result.target_reached
            answer = await _day_payload(session, event, user_id, index)
    except ValueError as exc:
        answer = {"ok": 0, "error": str(exc)[:100]}
    except Exception:
        log.exception("Widget query failed")
        answer = {"ok": 0, "error": "Temporary server error"}

    serialized = _json(answer)
    if len(serialized.encode("utf-8")) > 1024:
        answer["counts"] = []
        serialized = _json(answer)
    try:
        await bot(AnswerWidgetQuery(widget_query_id=query_id, data=serialized))
    except Exception:
        log.exception("Unable to answer widget query")
    if event_id is not None:
        try:
            await refresh_inline_invitations(bot, session_factory, event_id)
            if target_reached and organizer_id is not None:
                await bot.send_message(
                    organizer_id, f"{title} reached {answer['responses']} replies."
                )
        except Exception:
            log.exception("Widget availability saved, but notification failed")


class WidgetDispatcher(Dispatcher):
    """Dispatch widget updates before aiogram's 10.3 update-type switch."""

    async def _listen_update(self, update: Update, **kwargs: Any) -> Any:
        extra = update.model_extra or {}
        query = extra.get("widget_query")
        if isinstance(query, dict):
            await handle_widget_query(
                query,
                bot=kwargs["bot"],
                session_factory=kwargs["session_factory"],
            )
            return True
        return await super()._listen_update(update, **kwargs)