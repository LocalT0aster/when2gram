from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from when2gram.bot.keyboards.availability import availability_keyboard
from when2gram.domain.availability import SLOTS_PER_DAY, is_selected, toggle_range

router = Router(name=__name__)


class GridDemo(StatesGroup):
    selecting = State()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "When2Gram is a Telegram-native group availability planner.\n\n"
        "Use /grid to preview the 09:00–24:00, 15-minute availability selector."
    )


@router.message(Command("grid"))
async def grid_demo(message: Message, state: FSMContext) -> None:
    await state.set_state(GridDemo.selecting)
    await state.set_data({"mask": 0, "range_start": None})
    await message.answer(
        "Availability grid prototype — tap a start time, then an end time.",
        reply_markup=availability_keyboard([0] * SLOTS_PER_DAY, respondent_count=1),
    )


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(GridDemo.selecting, F.data.startswith("grid:slot:"))
async def toggle_grid_slot(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return

    try:
        slot = int(callback.data.rsplit(":", maxsplit=1)[1])
    except ValueError:
        await callback.answer("Invalid slot", show_alert=True)
        return

    data = await state.get_data()
    mask = int(data.get("mask", 0))
    range_start = data.get("range_start")
    if not isinstance(range_start, int):
        await state.update_data(range_start=slot)
        counts = [1 if is_selected(mask, index) else 0 for index in range(SLOTS_PER_DAY)]
        await callback.message.edit_text(
            "Availability grid prototype — now tap the end time.",
            reply_markup=availability_keyboard(
                counts,
                respondent_count=1,
                selected_mask=mask,
                range_start=slot,
            ),
        )
        await callback.answer("Start selected")
        return

    mask = toggle_range(mask, range_start, slot)
    await state.update_data(mask=mask, range_start=None)
    counts = [1 if is_selected(mask, index) else 0 for index in range(SLOTS_PER_DAY)]
    await callback.message.edit_text(
        "Availability grid prototype — tap a start time, then an end time.",
        reply_markup=availability_keyboard(counts, respondent_count=1, selected_mask=mask),
    )
    await callback.answer()


@router.callback_query(GridDemo.selecting, F.data == "grid:clear")
async def clear_grid(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.update_data(mask=0, range_start=None)
    await callback.message.edit_text(
        "Availability grid prototype — tap a start time, then an end time.",
        reply_markup=availability_keyboard([0] * SLOTS_PER_DAY, respondent_count=1),
    )
    await callback.answer("Cleared")


@router.callback_query(GridDemo.selecting, F.data == "grid:done")
async def finish_grid(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Saved")
    if callback.message is not None:
        await callback.message.edit_text("Grid prototype saved. Use /grid to try again.")
