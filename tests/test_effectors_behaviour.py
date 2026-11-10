"""Behavioural unit tests for effector handlers on their side-effect-free paths."""
import eli.execution.executor_enhanced  # noqa: F401  (registers all effectors)
from eli.execution.effectors.registry import get


def _contract(r, action):
    assert isinstance(r, dict)
    assert r.get("action") == action
    assert "content" in r and "response" in r
    assert isinstance(r.get("ok"), bool)
    return r


def test_set_timer_missing_args_refuses():
    r = _contract(get("SET_TIMER")("SET_TIMER", {}), "SET_TIMER")
    assert r["ok"] is False


def test_set_alarm_missing_args_refuses():
    r = _contract(get("SET_ALARM")("SET_ALARM", {}), "SET_ALARM")
    assert r["ok"] is False


def test_background_jobs_returns_list():
    r = _contract(get("BACKGROUND_JOBS")("BACKGROUND_JOBS", {}), "BACKGROUND_JOBS")
    assert r["ok"] is True


def test_check_job_without_id_prompts(monkeypatch):
    # No id means the running job, else the latest; with no jobs at all there is nothing to report.
    # The registry is process-wide, so an earlier test's job must not answer for this one.
    class _NoJobs:
        def list(self, *a, **k):
            return []

        def get(self, *a, **k):
            return None

    monkeypatch.setattr("eli.runtime.background_tasks.get_background_tasks", lambda: _NoJobs())
    r = _contract(get("CHECK_JOB")("CHECK_JOB", {}), "CHECK_JOB")
    assert r["ok"] is False


def test_check_job_unknown_id():
    r = _contract(get("CHECK_JOB")("CHECK_JOB", {"job_id": 999999}), "CHECK_JOB")
    assert r["ok"] is False


def test_web_search_no_query_refuses():
    r = _contract(get("WEB_SEARCH")("WEB_SEARCH", {}), "WEB_SEARCH")
    assert r["ok"] is False


def test_get_weather_no_location_refuses():
    r = _contract(get("GET_WEATHER")("GET_WEATHER", {}), "GET_WEATHER")
    assert r["ok"] is False and r.get("error") == "missing_location"


def test_the_place_last_asked_about_is_kept_for_that_conversation(monkeypatch):
    """Kept under the API's session id alone: a desktop turn has none, so the place was never kept
    there, and two callers with the same session id shared it."""
    from eli.kernel import request_context as rc
    monkeypatch.setattr("eli.core.netguard.should_block_network", lambda: False)
    monkeypatch.setattr("eli.plugins.weather.plugin.get_weather",
                        lambda place, day=None: {"ok": True, "location": f"{place}, Coastland",
                                                 "content": f"Mild in {place}.", "response": f"Mild in {place}."})

    def asked(conversation, args):
        token = rc.turn_facts_var.set({"conversation": conversation})
        try:
            return get("GET_WEATHER")("GET_WEATHER", args)
        finally:
            rc.turn_facts_var.reset(token)

    assert asked("engine:ann:desk", {"location": "Porto"})["ok"] is True
    assert asked("engine:ann:desk", {})["content"] == "Mild in Porto."
    assert asked("engine:bo:desk", {}).get("error") == "missing_location"


def test_pomodoro_status_when_idle():
    r = _contract(get("POMODORO_STATUS")("POMODORO_STATUS", {}), "POMODORO_STATUS")
    assert r["ok"] is True


def test_pomodoro_start_status_stop_roundtrip():
    _contract(get("POMODORO_START")("POMODORO_START", {"minutes": 25}), "POMODORO_START")
    st = _contract(get("POMODORO_STATUS")("POMODORO_STATUS", {}), "POMODORO_STATUS")
    assert "running" in st["content"].lower() or st["ok"] is True
    _contract(get("POMODORO_STOP")("POMODORO_STOP", {}), "POMODORO_STOP")


def test_date_actions_deterministic():
    for act in ("DATE", "GET_DATE"):
        r = _contract(get(act)(act, {}), act)
        assert r["ok"] is True and r["content"]


def test_calendar_actions_answer_from_the_real_calendar(tmp_path, monkeypatch):
    """These were stubs that said "not configured" and called it a success."""
    monkeypatch.setenv("ELI_AGENDA_DB", str(tmp_path / "agenda.sqlite3"))
    monkeypatch.setenv("ELI_CALENDAR_FILE", str(tmp_path / "calendar.ics"))
    # nothing said earlier: "add it" is read against the user's last half hour of messages
    monkeypatch.setattr("eli.execution.effectors.datetime_calendar.agenda_turn_context", lambda args: ("", []))
    listed = _contract(get("LIST_EVENTS")("LIST_EVENTS", {}), "LIST_EVENTS")
    assert listed["ok"] is True and "not configured" not in listed["content"]
    asked = _contract(get("ADD_EVENT")("ADD_EVENT", {}), "ADD_EVENT")
    assert asked["ok"] is False and asked["content"].startswith("I can add that. When is it?")


def test_ocr_image_missing_path_refuses():
    r = _contract(get("OCR_IMAGE")("OCR_IMAGE", {}), "OCR_IMAGE")
    assert r["ok"] is False


def test_analyze_image_missing_path_refuses():
    r = _contract(get("ANALYZE_IMAGE")("ANALYZE_IMAGE", {}), "ANALYZE_IMAGE")
    assert r["ok"] is False


def test_analyze_csv_missing_path_refuses():
    r = _contract(get("ANALYZE_CSV")("ANALYZE_CSV", {}), "ANALYZE_CSV")
    assert r["ok"] is False


def test_analyze_pdf_missing_path_refuses():
    r = _contract(get("ANALYZE_PDF")("ANALYZE_PDF", {}), "ANALYZE_PDF")
    assert r["ok"] is False


def test_persona_lock_status_reads_state():
    _contract(get("PERSONA_LOCK_STATUS")("PERSONA_LOCK_STATUS", {}), "PERSONA_LOCK_STATUS")


def test_plugin_list_reads_registry():
    _contract(get("PLUGIN_LIST")("PLUGIN_LIST", {}), "PLUGIN_LIST")


def test_proactive_status_reads_daemon():
    _contract(get("PROACTIVE_STATUS")("PROACTIVE_STATUS", {}), "PROACTIVE_STATUS")


def test_cpu_usage_reads_metrics():
    r = _contract(get("CPU_USAGE")("CPU_USAGE", {}), "CPU_USAGE")
    assert r["ok"] is True


def test_the_chronal_check_gives_the_clocks_facts_for_the_model_to_word():
    """It answered with one fixed sentence calling the alignment "nominal", which nothing had checked."""
    r = _contract(get("CHECK_CHRONAL_ALIGNMENT")("CHECK_CHRONAL_ALIGNMENT", {}), "CHECK_CHRONAL_ALIGNMENT")
    assert r["ok"] is True and r["explain_in_words"] is True
    assert "nominal" not in r["content"].lower()
    assert r["content"].startswith("Local time: ") and "Time zone: " in r["content"] and "UTC: " in r["content"]
