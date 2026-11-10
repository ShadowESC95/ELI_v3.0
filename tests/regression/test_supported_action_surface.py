def _extract_supported_actions():
    """The executor's SUPPORTED_ACTIONS as it holds them: its own list and every registered action (the
    source's literal names only the executor's own since registering an action makes it supported)."""
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    return {str(a).upper() for a in SUPPORTED_ACTIONS}


def test_user_facing_routable_admin_actions_are_supported():
    supported = _extract_supported_actions()

    expected = {
        "EXPLAIN_LAST_RESPONSE",
        "REFRESH_USER_INFO",
        "SELF_REPORT",
        "SELF_UPDATE",
        "USER_IDENTITY_SUMMARY",
    }

    missing = expected - supported
    assert not missing, f"Missing from SUPPORTED_ACTIONS: {sorted(missing)}"
