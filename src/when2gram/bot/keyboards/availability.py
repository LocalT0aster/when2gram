from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from when2gram.domain.availability import SLOT_MINUTES, SLOTS_PER_DAY, is_selected

HEADER_STYLE = "primary"
SUCCESS_STYLE = "success"
PRIMARY_STYLE = "primary"
SLOTS_PER_PAGE = 60


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
    start_minute: int = 9 * 60,
    end_minute: int = 24 * 60,
    page_start: int = 0,
) -> InlineKeyboardMarkup:
    if len(counts) != SLOTS_PER_DAY:
        raise ValueError(f"expected {SLOTS_PER_DAY} counts, got {len(counts)}")
    if start_minute % SLOT_MINUTES or end_minute % SLOT_MINUTES:
        raise ValueError("availability window must align to 15-minute slots")
    slot_count = (end_minute - start_minute) // SLOT_MINUTES
    if not 0 < slot_count <= SLOTS_PER_DAY:
        raise ValueError("availability window must fit within one day")
    if not 0 <= page_start < slot_count or page_start % 4:
        raise ValueError("availability page must start on an hour within the window")
    page_end = min(page_start + SLOTS_PER_PAGE, slot_count)

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":00", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":15", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":30", callback_data="noop", style=HEADER_STYLE),
            InlineKeyboardButton(text=":45", callback_data="noop", style=HEADER_STYLE),
        ]
    ]

    for first_slot in range(page_start, page_end, 4):
        hour = (start_minute + first_slot * SLOT_MINUTES) // 60
        row = [InlineKeyboardButton(text=f"{hour:02d}", callback_data="noop", style=HEADER_STYLE)]
        for quarter in range(4):
            slot = first_slot + quarter
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

    if slot_count > SLOTS_PER_PAGE:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Earlier",
                    callback_data=f"{callback_prefix}:earlier" if page_start else "noop",
                ),
                InlineKeyboardButton(
                    text="Later",
                    callback_data=(
                        f"{callback_prefix}:later" if page_end < slot_count else "noop"
                    ),
                ),
            ]
        )

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
