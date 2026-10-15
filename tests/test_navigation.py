from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from when2gram.bot.routers import event_creation_router, inline_router, start_router
from when2gram.bot.routers.event_creation import NewEvent


@pytest.fixture(scope="module")
def navigation_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(event_creation_router)
    dispatcher.include_router(inline_router)
    dispatcher.include_router(start_router)
    return dispatcher


def navigation_update(data: str) -> Update:
    return Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="navigation-click",
            from_user=User(id=42, is_bot=False, first_name="Ada"),
            chat_instance="private-chat",
            data=data,
            message=Message(
                message_id=99,
                date=datetime.now(UTC),
                chat=Chat(id=42, type="private"),
                text="Old menu",
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        "new:date:20261020",
        "new:time:done",
        "edit:dates:done",
        "edit:time:done",
        "availability:slot:0",
        "availability:done",
        "responses:slot:0",
        "responses:back",
        "grid:done",
    ],
)
async def test_lost_state_navigation_prompts_restart(
    navigation_dispatcher: Dispatcher, monkeypatch, data: str
) -> None:
    async with Bot(token="123456:test-token") as bot:
        request = AsyncMock(return_value=True)
        monkeypatch.setattr(bot.session, "make_request", request)
        state = navigation_dispatcher.fsm.get_context(bot=bot, chat_id=42, user_id=42)
        await state.clear()

        result = await navigation_dispatcher.feed_update(bot, navigation_update(data))

        assert result is not UNHANDLED
        request.assert_awaited_once()
        method = request.await_args.args[1]
        assert isinstance(method, AnswerCallbackQuery)
        assert method.show_alert is True
        assert method.text == "This menu has expired. Send /start to open a fresh menu."
        assert await state.get_state() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["home:new", "new:time:reset", "noop"])
async def test_active_or_stateless_navigation_is_not_intercepted(
    navigation_dispatcher: Dispatcher, monkeypatch, data: str
) -> None:
    async with Bot(token="123456:test-token") as bot:
        request = AsyncMock(return_value=True)
        monkeypatch.setattr(bot.session, "make_request", request)
        state = navigation_dispatcher.fsm.get_context(bot=bot, chat_id=42, user_id=42)
        await state.clear()
        if data == "new:time:reset":
            await state.set_state(NewEvent.time_range)
            await state.set_data({"start_hour": 9, "end_hour": 24, "pending_start_hour": 10})

        result = await navigation_dispatcher.feed_update(bot, navigation_update(data))

        assert result is not UNHANDLED
        methods = [call.args[1] for call in request.await_args_list]
        answers = [method for method in methods if isinstance(method, AnswerCallbackQuery)]
        assert len(answers) == 1
        assert answers[0].show_alert is not True
        if data != "noop":
            assert any(isinstance(method, EditMessageText) for method in methods)
        if data == "home:new":
            assert await state.get_state() == NewEvent.title.state
        await state.clear()
