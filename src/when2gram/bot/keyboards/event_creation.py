import calendar
from collections.abc import Collection, Sequence
from datetime import date
from typing import Protocol

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


class _EventDayInfo(Protocol):
    day: date


class _EventInfo(Protocol):
    title: str
    token: str
    days: Sequence[_EventDayInfo]


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


def response_target_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="1 reply"),
                KeyboardButton(text="3 replies"),
                KeyboardButton(text="5 replies"),
            ],
            [KeyboardButton(text="10 replies")],
            [KeyboardButton(text="Don't notify")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def time_range_keyboard(
    start_hour: int,
    end_hour: int,
    *,
    pending_start_hour: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for first_hour in range(0, 24, 4):
        row = []
        for hour in range(first_hour, first_hour + 4):
            style = "success" if start_hour <= hour < end_hour else None
            if hour == pending_start_hour:
                style = "primary"
            row.append(
                InlineKeyboardButton(
                    text=f"{hour:02d}", callback_data=f"new:time:{hour}", style=style
                )
            )
        rows.append(row)
    rows.append([InlineKeyboardButton(text="24", callback_data="new:time:24")])
    rows.append(
        [
            InlineKeyboardButton(text="Reset", callback_data="new:time:reset", style="danger"),
            InlineKeyboardButton(text="Continue", callback_data="new:time:done", style="success"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def event_preview_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Share invitation",
                    switch_inline_query=f"event:{token}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="My availability", callback_data=f"event:availability:{token}"
                ),
                InlineKeyboardButton(
                    text="View responses", callback_data=f"event:responses:{token}"
                ),
            ],
        ]
    )


def event_responses_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="View availability", callback_data=f"event:responses:{token}"
                )
            ]
        ]
    )


def organizer_events_keyboard(events: Sequence[_EventInfo]) -> InlineKeyboardMarkup:
    rows = []
    for event in events:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{event.title} ({event.days[-1].day:%d %b})",
                    callback_data=f"events:manage:{event.token}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_selected_days(days: Sequence[date]) -> str:
    return ", ".join(f"{day.day} {day:%b %Y}" for day in days)
