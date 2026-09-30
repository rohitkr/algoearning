from datetime import date

from ae_core.reports import ClosedTrade, Day, daily, max_drawdown, summarize

D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)


def test_daily_and_cumulative() -> None:
    days = daily([ClosedTrade(D2, -300), ClosedTrade(D1, 1000), ClosedTrade(D1, -200), ClosedTrade(D3, 500)])
    assert days == [Day(D1, 800, 2, 800), Day(D2, -300, 1, 500), Day(D3, 500, 1, 1000)]


def test_drawdown_from_the_running_peak() -> None:
    days = daily([ClosedTrade(D1, 1000), ClosedTrade(D2, -1500), ClosedTrade(D3, 2000)])
    assert max_drawdown(days) == -1500
    assert max_drawdown(daily([ClosedTrade(D1, -400)])) == -400  # the peak starts at zero
    assert max_drawdown([]) == 0


def test_summary() -> None:
    s = summarize([ClosedTrade(D1, 1000), ClosedTrade(D1, -250), ClosedTrade(D2, 500), ClosedTrade(D3, -250)])
    assert (s.total_pnl, s.trades, s.wins, s.losses, s.win_rate) == (1000, 4, 2, 2, 0.5)
    assert (s.avg_win, s.avg_loss, s.profit_factor) == (750, -250, 3.0)
    assert s.best_day and s.best_day.day == D1 and s.worst_day and s.worst_day.day == D3
    assert s.trading_days == 3 and s.max_drawdown == -250
    empty = summarize([])
    assert (empty.total_pnl, empty.win_rate, empty.best_day, empty.profit_factor) == (0, None, None, None)
