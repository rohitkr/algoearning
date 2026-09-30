from datetime import UTC, datetime, timedelta

import pytest
from ae_core.billing import RunningPlan, next_period

NOW = datetime(2026, 10, 1, tzinfo=UTC)
DAY = timedelta(days=1)


def test_first_purchase_starts_now() -> None:
    p = next_period(NOW, "pro", 99_900, "month", None)
    assert (p.start, p.end, p.extends_current, p.credit_paise) == (NOW, NOW + 30 * DAY, False, 0)
    assert next_period(NOW, "pro", 999_000, "year", None).end == NOW + 365 * DAY


def test_renewing_the_same_plan_extends_from_its_end() -> None:
    running = RunningPlan("pro", 99_900, "month", NOW + 10 * DAY)
    p = next_period(NOW, "pro", 99_900, "month", running)
    assert p.extends_current and p.end == NOW + 40 * DAY  # paying early loses nothing


def test_an_expired_plan_does_not_carry_over() -> None:
    running = RunningPlan("pro", 99_900, "month", NOW - DAY)
    p = next_period(NOW, "pro_plus", 249_900, "month", running)
    assert p.end == NOW + 30 * DAY and p.credit_paise == 0


def test_upgrade_credits_the_unused_value() -> None:
    running = RunningPlan("pro", 99_900, "month", NOW + 15 * DAY)  # half a Pro month left = ₹499.50
    p = next_period(NOW, "pro_plus", 249_900, "month", running)
    assert p.credit_paise == 49_950 and not p.extends_current
    assert p.end - NOW == pytest.approx(30 * DAY + timedelta(days=30 * 49_950 / 249_900), abs=timedelta(seconds=1))


def test_downgrade_credits_more_days() -> None:
    running = RunningPlan("pro_plus", 249_900, "month", NOW + 15 * DAY)
    p = next_period(NOW, "pro", 99_900, "month", running)
    assert p.credit_paise == 124_950
    assert p.end - NOW == pytest.approx(30 * DAY + timedelta(days=30 * 124_950 / 99_900), abs=timedelta(seconds=1))
