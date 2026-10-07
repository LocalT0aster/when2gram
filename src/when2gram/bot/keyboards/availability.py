from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from when2gram.domain.availability import SLOTS_PER_DAY, is_selected

HEADER_STYLE = "primary"
SUCCESS_STYLE = "success"
PRIMARY_STYLE = "primary"


def _availability_style(count: int, respondent_count: int) -> str | None:
    if respondent_count <= 0 or count <= 0:
        return None
    ratio = count / respondent_count
    if ratio >= 0.70:
        return SUCCESS_STYLE
    if ratio >= 0.40:
        return PRIMARY_STYLE
    return None


def availability_keyboard(
    counts: Sequence[int],
    *,
    respondent_count: int,
    selected_mask: int = 0,
    callback_prefix: str = "grid",
    range_start: int | None = None,
    day_label: str | None = None,
    can_go_previous: bool = False,
    can_go_next: bool = False,
) -> InlineKeyboardMarkup:
    if len(counts) != SLOTS_PER_DAY:
        raise ValueError(f"expected {SLOTS_PER_DAY} counts, got {len(counts)}")

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":00", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":15", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":30", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":45", callback_data="noop", style=HEADER_STYLE),
        ]
    ]

    for hour_offset, hour in enumerate(range(9, 24)):
        row = [InlineKeyboardButton(text=f"{hour:02d}", callback_data="noop", style=HEADER_STYLE)]
        for quarter in range(4):
            slot = hour_offset * 4 + quarter
            count = counts[slot]
            marker = "●" if slot == range_start else "✓" if is_selected(selected_mask, slot) else ""
            row.append(
                InlineKeyboardButton(
                    text=f"{marker}{count}",
                    callback_data=f"{callback_prefix}:slot:{slot}",
                    style=_availability_style(count, respondent_count),
                )
            )
        rows.append(row)

    if day_label is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="‹",
                    callback_data=f"{callback_prefix}:previous" if can_go_previous else "noop",
                ),
                InlineKeyboardButton(text=day_label, callback_data="noop", style=HEADER_STYLE),
                InlineKeyboardButton(
                    text="›",
                    callback_data=f"{callback_prefix}:next" if can_go_next else "noop",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="Clear", callback_data=f"{callback_prefix}:clear", style="danger"
            ),
            InlineKeyboardButton(
                text="Done", callback_data=f"{callback_prefix}:done", style="success"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
