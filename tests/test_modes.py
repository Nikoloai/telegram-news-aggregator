from aggregator.modes import RewriteMode, classify_mode


def test_hard_news_classification() -> None:
    assert classify_mode("При обстреле погибли люди") == RewriteMode.HARD_NEWS


def test_analysis_classification() -> None:
    assert classify_mode("Госдума приняла новый закон о налогах") == RewriteMode.ANALYSIS


def test_ironic_classification() -> None:
    assert classify_mode("Роскомнадзор объяснил очередную блокировку") == RewriteMode.IRONIC


def test_hard_news_overrides_irony() -> None:
    assert classify_mode("После блокировки задержали журналиста") == RewriteMode.HARD_NEWS
