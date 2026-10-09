from datetime import date, datetime, timedelta
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
    event_edit_keyboard,
    event_preview_keyboard,
    format_selected_days,
    response_target_keyboard,
    time_range_keyboard,
)
from when2gram.bot.routers.event_creation import (
    NewEvent,
    _invitation_start_target,
    _response_target_from_text,
    _toggle_cross_day_range,
    begin_new_event,
    delete_owned_event,
    list_organized_events,
    manage_organized_event,
    receive_title,
)
from when2gram.bot.routers.inline import event_invitation_query, refresh_inline_invitations
from when2gram.db.models import AvailabilityDay, Base, Event, EventDay, Response, User
from when2gram.db.repositories import (
    create_event,
    delete_event,
    delete_expired_events,
    get_organized_events,
    get_slot_respondents,
    save_submitted_availability,
    update_event,
    upsert_user,
    withdraw_submitted_availability,
)
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
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("What should this event be called?",)
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == (
        "new:cancel"
    )
    await storage.close()


@pytest.mark.asyncio
async def test_title_input_is_deleted_and_reuses_the_creation_prompt() -> None:
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=1))
    await state.set_state(NewEvent.title)
    await state.set_data({"prompt_message_id": 99})
    message = MagicMock()
    message.text = "Planning"
    message.chat.id = 1
    message.delete = AsyncMock()
    message.answer = AsyncMock()
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()

    await receive_title(message, state, bot)

    assert await state.get_state() == NewEvent.dates.state
    message.delete.assert_awaited_once()
    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.await_args.kwargs["message_id"] == 99
    assert bot.edit_message_text.await_args.kwargs["text"].startswith("Select one or more dates")
    message.answer.assert_not_awaited()
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
        "https://t.me/when2grambot?start=responses_opaque-token"
    )
    assert result.reply_markup.inline_keyboard[1][0].url == (
        "https://t.me/when2grambot?start=opaque-token"
    )


def test_invitation_response_link_opens_the_read_only_view() -> None:
    assert _invitation_start_target("responses_opaque-token") == ("opaque-token", True)
    assert _invitation_start_target("opaque-token") == ("opaque-token", False)


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
    assert markup.inline_keyboard[2][0].callback_data == "event:edit:opaque-token"
    assert markup.inline_keyboard[2][1].callback_data == "event:delete:opaque-token"


def test_event_edit_keyboard_scopes_all_actions_to_the_event() -> None:
    markup = event_edit_keyboard("opaque-token")
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert [button.callback_data for button in buttons] == [
        "event:edit:title:opaque-token",
        "event:edit:dates:opaque-token",
        "event:edit:time:opaque-token",
        "event:edit:target:opaque-token",
        "events:manage:opaque-token",
    ]


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
    assert [button.text for button in keyboard.keyboard[0]] == ["1", "2", "3", "4", "5"]
    assert _response_target_from_text("3") == 3
    assert _response_target_from_text("12") == 12
    assert _response_target_from_text("Don't notify") is None


def test_creation_keyboards_expose_back_and_cancel_actions() -> None:
    date_buttons = [
        button
        for row in date_picker_keyboard(
            date(2026, 10, 1),
            [],
            back_callback="new:back:title",
            cancel_callback="new:cancel",
        ).inline_keyboard
        for button in row
    ]
    time_buttons = [
        button
        for row in time_range_keyboard(
            9,
            24,
            back_callback="new:back:dates",
            cancel_callback="new:cancel",
        ).inline_keyboard
        for button in row
    ]
    target_keyboard = response_target_keyboard()

    assert "new:back:title" not in [button.callback_data for button in date_buttons]
    assert "new:cancel" in [button.callback_data for button in date_buttons]
    assert "new:back:dates" in [button.callback_data for button in time_buttons]
    assert "new:cancel" in [button.callback_data for button in time_buttons]
    assert all(
        button.text not in {"Back", "Cancel"}
        for row in target_keyboard.keyboard
        for button in row
    )
    assert [button.text for button in date_buttons[-2:]] == ["Cancel", "Continue"]
    assert [button.text for button in time_buttons[-4:]] == ["Back", "Cancel", "Reset", "Continue"]

    selected_date_buttons = [
        button
        for row in date_picker_keyboard(
            date(2026, 10, 1),
            [date(2026, 10, 8)],
            back_callback="new:back:title",
            cancel_callback="new:cancel",
        ).inline_keyboard
        for button in row
    ]
    assert "new:back:title" in [button.callback_data for button in selected_date_buttons]
    assert [button.text for button in selected_date_buttons[-3:]] == ["Back", "Cancel", "Continue"]


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

    async with session_factory() as session, session.begin():
        remaining_responses = await withdraw_submitted_availability(
            session,
            event_id=event_id,
            user_id=42,
        )

    async with session_factory() as session:
        assert await session.get(Response, (event_id, 42)) is None
        assert list(
            await session.scalars(select(AvailabilityDay).where(AvailabilityDay.user_id == 42))
        ) == []
    assert remaining_responses == 0

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

    async with session_factory() as session:
        respondents = await get_slot_respondents(
            session,
            event_id=event_id,
            day=event_day,
            slot=95,
        )

    assert respondents == [("organizer", "Ada", True), ("guest", "Grace", False)]

    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_events_are_hidden_and_deleted(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    expired_day = date(2026, 10, 8)
    future_day = date(2026, 10, 10)

    async with session_factory() as session, session.begin():
        expired_event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="Expired",
            days=[expired_day],
            start_minute=9 * 60,
            end_minute=10 * 60,
            response_target=None,
        )
        future_event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="Future",
            days=[future_day],
            start_minute=9 * 60,
            end_minute=10 * 60,
            response_target=None,
        )
        expired_event_id = expired_event.id

    now = datetime(2026, 10, 9, 12)
    async with session_factory() as session:
        events = await get_organized_events(session, 42, now=now)
    assert [event.id for event in events] == [future_event.id]

    async with session_factory() as session, session.begin():
        deleted_count = await delete_expired_events(session, now=now)
    async with session_factory() as session:
        assert await session.get(Event, expired_event_id) is None
        assert await session.get(Event, future_event.id) is not None
    assert deleted_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_schedule_edits_clear_responses_and_event_deletion_cascades(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    first_day = date.today() + timedelta(days=2)
    second_day = first_day + timedelta(days=1)

    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="Planning",
            days=[first_day],
            start_minute=9 * 60,
            end_minute=12 * 60,
            response_target=3,
        )
        await save_submitted_availability(
            session,
            event_id=event.id,
            user_id=42,
            masks=[1],
        )

    async with session_factory() as session, session.begin():
        renamed = await update_event(session, event_id=event.id, title="Renamed")
    assert renamed.title == "Renamed"

    async with session_factory() as session:
        assert await session.get(Response, (event.id, 42)) is not None

    async with session_factory() as session, session.begin():
        rescheduled = await update_event(
            session,
            event_id=event.id,
            days=[first_day, second_day],
            start_minute=10 * 60,
            end_minute=13 * 60,
        )
    assert [event_day.day for event_day in rescheduled.days] == [first_day, second_day]
    assert rescheduled.target_notified_at is None

    async with session_factory() as session:
        assert await session.get(Response, (event.id, 42)) is None
        assert list(await session.scalars(select(AvailabilityDay))) == []

    async with session_factory() as session, session.begin():
        assert await delete_event(session, event.id)
    async with session_factory() as session:
        assert await session.get(Event, event.id) is None
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


@pytest.mark.asyncio
async def test_submission_rejects_passed_days(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)

    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="Planning",
            days=[date.today() + timedelta(days=3)],
            start_minute=9 * 60,
            end_minute=24 * 60,
            response_target=None,
        )

    async with session_factory() as session:
        with pytest.raises(ValueError, match="passed days"):
            await save_submitted_availability(
                session,
                event_id=event.id,
                user_id=42,
                masks=[0],
                days=[date(2020, 1, 1)],
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_events_command_lists_only_organizers_upcoming_events(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    future_day = date.today() + timedelta(days=2)

    async with session_factory() as session, session.begin():
        own_event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="My talk",
            days=[future_day],
            start_minute=9 * 60,
            end_minute=10 * 60,
            response_target=None,
        )
        await create_event(
            session,
            organizer_id=7,
            organizer_username="other",
            organizer_first_name="Bob",
            title="Not mine",
            days=[future_day],
            start_minute=9 * 60,
            end_minute=10 * 60,
            response_target=None,
        )

    message = MagicMock()
    message.chat.type = "private"
    message.from_user = MagicMock()
    message.from_user.id = 42
    message.answer = AsyncMock()

    await list_organized_events(message, session_factory)

    markup = message.answer.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [button.callback_data for button in buttons] == [
        f"events:manage:{own_event.token}"
    ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_manage_event_requires_organizer_ownership(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=42, user_id=42))

    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="My talk",
            days=[date.today() + timedelta(days=2)],
            start_minute=9 * 60,
            end_minute=10 * 60,
            response_target=None,
        )
    stranger_callback = MagicMock()
    stranger_callback.data = f"events:manage:{event.token}"
    stranger_callback.from_user = MagicMock()
    stranger_callback.from_user.id = 7
    stranger_callback.message = MagicMock()
    stranger_callback.message.edit_text = AsyncMock()
    stranger_callback.answer = AsyncMock()

    await manage_organized_event(stranger_callback, state, session_factory)
    stranger_callback.answer.assert_awaited_once_with(
        "Only the organizer can manage this event", show_alert=True
    )
    stranger_callback.message.edit_text.assert_not_awaited()

    owner_callback = MagicMock()
    owner_callback.data = f"events:manage:{event.token}"
    owner_callback.from_user = MagicMock()
    owner_callback.from_user.id = 42
    owner_callback.message = MagicMock()
    owner_callback.message.edit_text = AsyncMock()
    owner_callback.answer = AsyncMock()

    await manage_organized_event(owner_callback, state, session_factory)
    owner_callback.message.edit_text.assert_awaited_once()
    assert "My talk" in owner_callback.message.edit_text.await_args.args[0]
    markup = owner_callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].switch_inline_query == f"event:{event.token}"

    await storage.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_only_the_organizer_can_confirm_event_deletion(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'when2gram.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=42, user_id=42))

    async with session_factory() as session, session.begin():
        event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="My talk",
            days=[date.today() + timedelta(days=2)],
            start_minute=9 * 60,
            end_minute=10 * 60,
            response_target=None,
        )

    async with session_factory() as session, session.begin():
        remaining_event = await create_event(
            session,
            organizer_id=42,
            organizer_username="organizer",
            organizer_first_name="Ada",
            title="Another event",
            days=[date.today() + timedelta(days=3)],
            start_minute=9 * 60,
            end_minute=10 * 60,
            response_target=None,
        )

    stranger_callback = MagicMock()
    stranger_callback.data = f"event:delete:confirm:{event.token}"
    stranger_callback.from_user = MagicMock()
    stranger_callback.from_user.id = 7
    stranger_callback.message = MagicMock()
    stranger_callback.message.edit_text = AsyncMock()
    stranger_callback.answer = AsyncMock()

    await delete_owned_event(stranger_callback, state, session_factory)
    stranger_callback.answer.assert_awaited_once_with(
        "Only the organizer can delete this event", show_alert=True
    )
    async with session_factory() as session:
        assert await session.get(Event, event.id) is not None

    owner_callback = MagicMock()
    owner_callback.data = f"event:delete:confirm:{event.token}"
    owner_callback.from_user = MagicMock()
    owner_callback.from_user.id = 42
    owner_callback.message = MagicMock()
    owner_callback.message.edit_text = AsyncMock()
    owner_callback.answer = AsyncMock()

    await delete_owned_event(owner_callback, state, session_factory)
    owner_callback.message.edit_text.assert_awaited_once()
    assert owner_callback.message.edit_text.await_args.args[0] == "Your upcoming events:"
    markup = owner_callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"events:manage:{remaining_event.token}"
    async with session_factory() as session:
        assert await session.get(Event, event.id) is None

    await storage.close()
    await engine.dispose()
