from rewriter.llm import SYSTEM_PROMPT


def test_prompt_requires_self_contained_specific_details() -> None:
    assert "понятен без перехода по ссылке" in SYSTEM_PROMPT
    assert "полное название сервиса" in SYSTEM_PROMPT
    assert "кто что сделал, когда и что именно произошло" in SYSTEM_PROMPT
