from rewriter.llm import SYSTEM_PROMPT


def test_prompt_requires_self_contained_specific_details() -> None:
    assert "понятен без перехода по ссылке" in SYSTEM_PROMPT
    assert "полное название сервиса" in SYSTEM_PROMPT
    assert "кто что сделал, когда и что именно произошло" in SYSTEM_PROMPT
    assert "что было указано неверно" in SYSTEM_PROMPT
    assert "сохраняй дословно" in SYSTEM_PROMPT
    assert "противопоставляй только факты" in SYSTEM_PROMPT


def test_prompt_requires_critical_but_proportionate_public_interest_angle():
    assert "критической редакционной позицией" in SYSTEM_PROMPT
    assert "что произошло → где система дала сбой → чем это обернулось для людей" in SYSTEM_PROMPT
    assert "причина пока неизвестна" in SYSTEM_PROMPT
    assert "Локальную проблему не превращай в общенациональную угрозу" in SYSTEM_PROMPT
    assert "без выдуманных медицинских прогнозов" in SYSTEM_PROMPT
