from when2gram.bot.keyboards.availability import availability_keyboard
from when2gram.domain.availability import SLOTS_PER_DAY


def test_keyboard_has_header_15_hours_and_actions() -> None:
    markup = availability_keyboard([0] * SLOTS_PER_DAY, respondent_count=0)
    assert len(markup.inline_keyboard) == 17
    assert len(markup.inline_keyboard[0]) == 5
    assert markup.inline_keyboard[0][0].text == ""
    assert all(len(row) == 5 for row in markup.inline_keyboard[1:16])
    assert len(markup.inline_keyboard[-1]) == 2
