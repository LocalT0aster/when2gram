from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from when2gram.bot.keyboards.availability import availability_keyboard
from when2gram.bot.keyboards.event_creation import (
    date_picker_keyboard,
    event_preview_keyboard,
    format_selected_days,
    response_target_keyboard,
    time_range_keyboard,
)
from when2gram.bot.routers.event_creation import (
    NewEvent,
    _response_target_from_text,
    _toggle_cross_day_range,
    begin_new_event,
)
from when2gram.bot.routers.inline import event_invitation_query, refresh_inline_invitations
from when2gram.db.models import AvailabilityDay, Base, Event, EventDay, Response, User
from when2gram.db.repositories import create_event, save_submitted_availability, upsert_user
from when2gram.db.session import create_engine, create_session_factory
from when2gram.domain.availability import SLOTS_PER_DAY


@pytest.mark.asyncio
async def test_new_command_starts_in_a_private_chat() -> None:
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    message = MagicMock()
    message.chat.type = "private"
    message.answer = AsyncMock()

    await begin_new_event(message, state)

    assert await state.get_state() == NewEvent.title.state
    message.answer.assert_awaited_once_with("What should this event be called?")
    await storage.close()


@pytest.mark.asyncio
async def test_inline_query_returns_an_event_invitation(monkeypatch) -> None:
    event = Event(
        id=3,
        token="opaque-token",
        organizer_id=42,
        title="Thesis meeting",
        start_minute=9 * 60,
        end_minute=24 * 60,
        days=[EventDay(day=date(2026, 10, 8))],
    )
    session = MagicMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=session_context)
    inline_query = MagicMock()
    inline_query.query = "event:opaque-token"
    inline_query.answer = AsyncMock()
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=MagicMock(username="when2grambot"))

    monkeypatch.setattr(
        "when2gram.bot.routers.inline.get_event_by_token", AsyncMock(return_value=event)
    )
    monkeypatch.setattr(
        "when2gram.bot.routers.inline.submitted_response_count", AsyncMock(return_value=2)
    )

    await event_invitation_query(inline_query, bot, session_factory)

    result = inline_query.answer.await_args.args[0][0]
    assert result.id == "event:opaque-token"
    assert result.reply_markup.inline_keyboard[0][0].url == (
        "https://t.me/when2grambot?start=opaque-token"
    )


def test_date_picker_marks_selection_and_disables_past_days() -> None:
    today = date(2026, 10, 8)
    markup = date_picker_keyboard(date(2026, 10, 1), {today}, today=today)
    buttons = [button for row in markup.inline_keyboard for button in row]

    selected = next(button for button in buttons if button.text == "8")
    past = next(button for button in buttons if button.text == "7")

    assert selected.callback_data == "new:date:20261008"
    assert selected.style == "success"
    assert past.callback_data == "noop"


def test_preview_shares_the_event_token() -> None:
    markup = event_preview_keyboard("opaque-token")

    assert markup.inline_keyboard[0][0].switch_inline_query == "event:opaque-token"


def test_format_selected_days() -> None:
    days = [date(2026, 10, 8), date(2026, 10, 10)]
    assert format_selected_days(days) == "8 Oct 2026, 10 Oct 2026"


def test_availability_keyboard_scopes_organizer_callbacks() -> None:
    markup = availability_keyboard(
        [0] * SLOTS_PER_DAY,
        respondent_count=1,
        callback_prefix="availability",
        day_label="1/2",
        can_go_next=True,
    )

    assert markup.inline_keyboard[1][1].callback_data == "availability:slot:0"
    assert markup.inline_keyboard[-2][2].callback_data == "availability:next"
    assert markup.inline_keyboard[-1][1].callback_data == "availability:done"


def test_availability_keyboard_pages_a_full_day_event() -> None:
    markup = availability_keyboard(
        [0] * SLOTS_PER_DAY,
        respondent_count=1,
        start_minute=0,
        end_minute=24 * 60,
    )

    assert markup.inline_keyboard[1][0].text == "00"
    assert markup.inline_keyboard[1][1].callback_data == "grid:slot:0"
    assert markup.inline_keyboard[-2][1].callback_data == "grid:later"

    later_markup = availability_keyboard(
        [0] * SLOTS_PER_DAY,
        respondent_count=1,
        start_minute=0,
        end_minute=24 * 60,
        page_start=60,
    )

    assert later_markup.inline_keyboard[1][0].text == "15"
    assert later_markup.inline_keyboard[1][1].callback_data == "grid:slot:60"


def test_cross_day_range_fills_every_intermediate_event_day() -> None:
    masks = [0, 0, 0]

    _toggle_cross_day_range(
        masks,
        start_day_index=0,
        start_slot=4,
        end_day_index=2,
        end_slot=19,
        slot_count=60,
    )

    assert masks == [((1 << 16) - 1) << 4] * 3


@pytest.mark.asyncio
async def test_refresh_inline_invitations_uses_current_response_count(monkeypatch) -> None:
    event = Event(
        id=3,
        token="opaque-token",
        organizer_id=42,
        title="Thesis meeting",
        start_minute=9 * 60,
        end_minute=24 * 60,
        days=[EventDay(day=date(2026, 10, 8))],
    )
    session = MagicMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=session_context)
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=MagicMock(username="when2grambot"))
    bot.edit_message_text = AsyncMock()

    monkeypatch.setattr(
        "when2gram.bot.routers.inline.get_event_by_id", AsyncMock(return_value=event)
    )
    monkeypatch.setattr(
        "when2gram.bot.routers.inline.submitted_response_count", AsyncMock(return_value=4)
    )
    monkeypatch.setattr(
        "when2gram.bot.routers.inline.get_inline_invite_message_ids",
        AsyncMock(return_value=["one", "two"]),
    )

    await refresh_inline_invitations(bot, session_factory, event.id)

    assert bot.edit_message_text.await_count == 2
    assert {
        call.kwargs["inline_message_id"] for call in bot.edit_message_text.await_args_list
    } == {"one", "two"}
    assert "4 replied" in bot.edit_message_text.await_args_list[0].kwargs["text"]


def test_time_range_keyboard_supports_midnight_as_an_end_hour() -> None:
    markup = time_range_keyboard(9, 24)

    assert markup.inline_keyboard[-2][0].callback_data == "new:time:24"
    assert markup.inline_keyboard[-1][1].callback_data == "new:time:done"


def test_response_target_reply_keyboard_accepts_common_and_custom_values() -> None:
    keyboard = response_target_keyboard()

    assert keyboard.keyboard[-1][0].text == "Don't notify"
    assert _response_target_from_text("3 replies") == 3
    assert _response_target_from_text("12") == 12
    assert _response_target_from_text("Don't notify") is None


@pytest.mark.asyncio
async def test_create_event_persists_organizer_and_unique_ordered_days(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    first_day = date(2026, 10, 8)
    second_day = date(2026, 10, 10)

    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="  Thesis meeting  ",
            days=[second_day, first_day, first_day],
            start_minute=9 * 60,
            end_minute=24 * 60,
            response_target=5,
        )
        event_id = event.id

    async with session_factory() as session:
        persisted_event = await session.scalar(
            select(Event).options(selectinload(Event.days)).where(Event.id == event_id)
        )
        organizer = await session.get(User, 42)

    assert persisted_event is not None
    assert persisted_event.title == "Thesis meeting"
    assert persisted_event.start_minute == 9 * 60
    assert persisted_event.end_minute == 24 * 60
    assert persisted_event.response_target == 5
    assert len(persisted_event.token) >= 16
    assert [event_day.day for event_day in persisted_event.days] == [first_day, second_day]
    assert organizer is not None
    assert organizer.username == "organizer"
    assert organizer.preferred_start_hour == 9
    assert organizer.preferred_end_hour == 24

    async with session_factory() as session, session.begin():
        await save_submitted_availability(
            session,
            event_id=event_id,
            user_id=42,
            masks=[1, 1 << 59],
        )

    async with session_factory() as session:
        response = await session.get(Response, (event_id, 42))
        availability_days = list(
            await session.scalars(
                select(AvailabilityDay)
                .where(AvailabilityDay.user_id == 42)
                .order_by(AvailabilityDay.event_day_id)
            )
        )

    assert response is not None
    assert response.submitted_at is not None
    assert [availability_day.slot_mask for availability_day in availability_days] == [1, 1 << 59]

    async with session_factory() as session:
        with pytest.raises(ValueError, match="event time range"):
            await save_submitted_availability(
                session,
                event_id=event_id,
                user_id=42,
                masks=[1 << 60, 0],
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_submission_notifies_once_after_the_response_target(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    event_day = date(2026, 10, 8)

    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=1,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="Planning",
            days=[event_day],
            start_minute=0,
            end_minute=24 * 60,
            response_target=2,
        )
        event_id = event.id
        await upsert_user(session, telegram_id=2, username="guest", first_name="Grace")

    async with session_factory() as session, session.begin():
        first = await save_submitted_availability(
            session, event_id=event_id, user_id=1, masks=[1 << 95]
        )
    async with session_factory() as session, session.begin():
        second = await save_submitted_availability(
            session, event_id=event_id, user_id=2, masks=[1]
        )
    async with session_factory() as session, session.begin():
        repeated = await save_submitted_availability(
            session, event_id=event_id, user_id=2, masks=[1]
        )

    assert first.response_count == 1
    assert not first.target_reached
    assert second.response_count == 2
    assert second.target_reached
    assert repeated.response_count == 2
    assert not repeated.target_reached

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_event_rejects_missing_days(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        with pytest.raises(ValueError, match="at least one day"):
            await create_event(
                session,
                organizer_id=42,
                organizer_username=None,
                organizer_first_name="Ada",
                title="Planning",
                days=[],
                start_minute=9 * 60,
                end_minute=24 * 60,
                response_target=None,
            )

    await engine.dispose()
