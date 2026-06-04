from puzzle_ops.renderer import AppState
from puzzle_ops.server import update_state_from_query


def test_invalid_country_query_does_not_corrupt_state():
    state = AppState(country="日本")

    update_state_from_query(state, {"country": ["æ\x97¥æ\x9c¬"], "view": ["dashboard"]})

    assert state.country == "日本"
    assert state.view == "dashboard"
