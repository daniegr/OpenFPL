"""Web-app service helpers that are pure enough to unit test."""
from app.services import unlimited_applies


def test_unlimited_transfers_only_for_the_opening_gameweek():
    assert unlimited_applies(True, 0, 1, 1)           # pre-deadline, plan starts GW1
    assert not unlimited_applies(True, 0, 3, 1)       # planning ahead from GW3
    assert not unlimited_applies(True, 2, 3, 3)       # stale flag after deadlines
    assert not unlimited_applies(False, 0, 1, 1)      # no flag
