from aggregator.modes import RewriteMode, classify_mode


def test_hard_news_classification() -> None:
    assert classify_mode("При обстреле погибли люди") == RewriteMode.HARD_NEWS


def test_analysis_classification() -> None:
    assert classify_mode("Госдума приняла новый закон о налогах") == RewriteMode.ANALYSIS


def test_ironic_classification() -> None:
    assert classify_mode("Роскомнадзор объяснил очередную блокировку") == RewriteMode.IRONIC


def test_satirical_classification() -> None:
    assert classify_mode("Власти представили очередное импортозамещение") == RewriteMode.SATIRICAL


def test_hard_news_overrides_irony() -> None:
    assert classify_mode("После блокировки задержали журналиста") == RewriteMode.HARD_NEWS


def test_hard_news_overrides_satire() -> None:
    assert classify_mode("На фестивале пропаганды погибли люди") == RewriteMode.HARD_NEWS


def test_award_news_is_not_forced_to_hard_news_by_background_war_reference() -> None:
    assert classify_mode(
        "Кадыров получил знак «Почетный архитектор России»",
        "Награду вручили на открытии бульвара.\nРанее он получал награды во время войны.",
    ) == RewriteMode.SATIRICAL


def test_victim_in_lead_overrides_an_innocuous_headline() -> None:
    assert classify_mode("Власти открыли фестиваль", "При открытии погибли люди.") == RewriteMode.HARD_NEWS


def test_health_victims_override_satirical_background():
    assert classify_mode("После торжественного открытия произошло массовое отравление",
                         "Чиновники объявили импортозамещение.") == RewriteMode.HARD_NEWS
