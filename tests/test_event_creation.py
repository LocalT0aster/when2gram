from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from when2gram.bot.keyboards.event_creation import (
    date_picker_keyboard,
    event_preview_keyboard,
    format_selected_days,
)
from when2gram.bot.routers.event_creation import NewEvent, begin_new_event
from when2gram.db.models import Base, Event, User
from when2gram.db.repositories import create_event
from when2gram.db.session import create_engine, create_session_factory


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
    assert persisted_event.response_target == 5
    assert len(persisted_event.token) >= 16
    assert [event_day.day for event_day in persisted_event.days] == [first_day, second_day]
    assert organizer is not None
    assert organizer.username == "organizer"

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
                response_target=None,
            )

    await engine.dispose()
