from aggregator.validator import FactPreservationValidator


def test_number_preservation_accepts_same_number() -> None:
    result = FactPreservationValidator().validate(
        "Суд назначил Ивану Иванову 5 лет колонии.",
        "Иван Иванов получил 5 лет колонии.",
    )
    assert result.valid is True


def test_number_preservation_rejects_changed_number() -> None:
    result = FactPreservationValidator().validate(
        "По данным ведомства, пострадали 47 человек.",
        "Ведомство сообщило о 74 пострадавших.",
    )
    assert result.valid is False
    assert any("74" in error for error in result.errors)


def test_date_preservation_rejects_changed_date() -> None:
    result = FactPreservationValidator().validate(
        "Заседание назначено на 12 октября.",
        "Заседание пройдет 13 октября.",
    )
    assert result.valid is False
    assert any("13 октября" in error for error in result.errors)


def test_new_entity_is_rejected() -> None:
    result = FactPreservationValidator().validate(
        "Иван Иванов выступил в суде.",
        "Петр Петров выступил в суде.",
    )
    assert result.valid is False
