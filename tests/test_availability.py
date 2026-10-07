import pytest

from when2gram.domain.availability import (
    ALL_SLOTS_MASK,
    SLOTS_PER_DAY,
    aggregate_counts,
    best_slots,
    clamp_mask,
    is_selected,
    slot_label,
    toggle_range,
    toggle_slot,
)


def test_day_has_96_slots() -> None:
    assert SLOTS_PER_DAY == 96
    assert slot_label(0) == "00:00"
    assert slot_label(95) == "23:45"
    assert slot_label(0, start_minute=9 * 60) == "09:00"


def test_toggle_slot_round_trip() -> None:
    mask = toggle_slot(0, 7)
    assert is_selected(mask, 7)
    assert toggle_slot(mask, 7) == 0


def test_toggle_range_sets_or_clears_an_inclusive_interval() -> None:
    mask = toggle_range(0, 5, 2)
    assert [is_selected(mask, slot) for slot in range(2, 6)] == [True, True, True, True]
    assert toggle_range(mask, 2, 5) == 0


def test_toggle_range_clears_when_its_start_is_already_selected() -> None:
    mask = toggle_slot(0, 1)
    assert toggle_range(mask, 1, 3) == 0


def test_toggle_range_uses_first_tapped_slot_when_selecting_backwards() -> None:
    mask = toggle_slot(0, 5)
    assert toggle_range(mask, 5, 2) == 0


def test_clamp_mask_removes_bits_outside_day() -> None:
    assert clamp_mask(ALL_SLOTS_MASK | (1 << 63)) == ALL_SLOTS_MASK


def test_aggregate_counts() -> None:
    first = toggle_slot(toggle_slot(0, 0), 1)
    second = toggle_slot(toggle_slot(0, 1), 2)
    counts = aggregate_counts([first, second])
    assert counts[:4] == [1, 2, 1, 0]


def test_best_slots_tie_breaks_by_time() -> None:
    counts = [0] * SLOTS_PER_DAY
    counts[10] = 3
    counts[3] = 3
    counts[5] = 2
    assert best_slots(counts, limit=3) == [3, 10, 5]


def test_invalid_slot_rejected() -> None:
    with pytest.raises(ValueError):
        toggle_slot(0, SLOTS_PER_DAY)
