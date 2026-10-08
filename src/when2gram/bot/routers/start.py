from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from when2gram.bot.keyboards.event_creation import HOME_TEXT, home_keyboard

router = Router(name=__name__)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        f"{HOME_TEXT}\n\n/new - create an event\n/events - manage your events",
        reply_markup=home_keyboard(),
    )


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
