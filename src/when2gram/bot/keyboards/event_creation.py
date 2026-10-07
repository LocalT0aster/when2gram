import calendar
from collections.abc import Collection, Sequence
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def date_picker_keyboard(
    month: date,
    selected_days: Collection[date],
    *,
    today: date | None = None,
) -> InlineKeyboardMarkup:
    """Build a month picker whose selected dates are shown in green."""
    if today is None:
        today = date.today()

    rows = [
        [
            InlineKeyboardButton(text="‹", callback_data="new:month:-1"),
            InlineKeyboardButton(
                text=month.strftime("%B %Y"), callback_data="noop", style="primary"
            ),
            InlineKeyboardButton(text="›", callback_data="new:month:1"),
        ],
        [
            InlineKeyboardButton(text=weekday, callback_data="noop", style="primary")
            for weekday in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
        ],
    ]

    for week in calendar.monthcalendar(month.year, month.month):
        row: list[InlineKeyboardButton] = []
        for day_number in week:
            if day_number == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
                continue

            candidate = date(month.year, month.month, day_number)
            if candidate < today:
                row.append(InlineKeyboardButton(text=str(day_number), callback_data="noop"))
                continue

            row.append(
                InlineKeyboardButton(
                    text=str(day_number),
                    callback_data=f"new:date:{candidate:%Y%m%d}",
                    style="success" if candidate in selected_days else None,
                )
            )
        rows.append(row)

    rows.append(
        [InlineKeyboardButton(text="Continue", callback_data="new:dates:done", style="success")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def response_target_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 reply", callback_data="new:target:1"),
                InlineKeyboardButton(text="3 replies", callback_data="new:target:3"),
                InlineKeyboardButton(text="5 replies", callback_data="new:target:5"),
            ],
            [
                InlineKeyboardButton(text="10 replies", callback_data="new:target:10"),
                InlineKeyboardButton(text="Custom", callback_data="new:target:custom"),
            ],
            [InlineKeyboardButton(text="No notification", callback_data="new:target:none")],
        ]
    )


def event_preview_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Share invitation",
                    switch_inline_query=f"event:{token}",
                )
            ]
        ]
    )


def format_selected_days(days: Sequence[date]) -> str:
    return ", ".join(f"{day.day} {day:%b %Y}" for day in days)
