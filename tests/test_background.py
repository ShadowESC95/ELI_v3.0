"""Tests for the background task manager, the cost/should-background heuristic,
and the CHECK_JOB / BACKGROUND_JOBS executor actions. No model required."""

import pytest
import time

from eli.runtime.background_tasks import BackgroundTasks


# ── background task manager ─────────────────────────────────────────────────

def test_background_job_marks_inference_background_so_it_yields():
    # A background job's model calls must be marked background so a foreground turn
    # preempts them (no more 200-530s memory waits queued behind a PDF/codegen job).
    import eli.cognition.gguf_inference as gi
    from eli.runtime.background_tasks import BackgroundTasks
    gi.set_background_inference(False)  # this (main) thread = foreground
    bt = BackgroundTasks(max_workers=1)
    seen = {}
    try:
        def _job():
            seen["bg"] = gi.is_background_inference()
            return "ok"
        bt.wait(bt.submit("job", _job), timeout=5)
        assert seen.get("bg") is True            # job inference is background → yields
        assert gi.is_background_inference() is False  # foreground thread untouched
    finally:
        bt.shutdown()


def test_background_runs_and_reports():
    from eli.runtime.background_tasks import BackgroundTasks
    bt = BackgroundTasks(max_workers=2)
    try:
        def _work(x):
            time.sleep(0.3)
            return {"ok": True, "script_path": f"/tmp/x{x}.py", "solved": True}
        jid = bt.submit("codegen", _work, 7)
        assert bt.get(jid)["status"] in ("queued", "running")
        done = bt.wait(jid, timeout=5)
        assert done["status"] == "done"
        assert done["result"]["solved"] is True
        assert "/tmp/x7.py" in done["note"]
    finally:
        bt.shutdown()


def test_background_captures_failure():
    from eli.runtime.background_tasks import BackgroundTasks
    bt = BackgroundTasks(max_workers=1)
    try:
        def boom():
            raise ValueError("nope")
        jid = bt.submit("fail", boom)
        bt.wait(jid, timeout=5)
        t = bt.get(jid)
        assert t["status"] == "failed" and "ValueError" in t["error"]
    finally:
        bt.shutdown()


# ── cost / should-background heuristic ──────────────────────────────────────

def test_cost_light_vs_heavy():
    from eli.coding.cost import should_background
    assert should_background("add two numbers")["background"] is False
    heavy = should_background("run a monte carlo simulation and benchmark the optimisation")
    assert heavy["background"] is True


def test_cost_explicit_phrasing_wins():
    from eli.coding.cost import should_background
    assert should_background("implement dijkstra in the background and notify me")["background"] is True
    assert should_background("run a huge monte carlo simulation right now")["background"] is False


def test_cost_open_ended_backgrounds():
    # Open-ended/hard tasks must background (they ran foreground and blocked the
    # UI for minutes before this fix).
    from eli.coding.cost import should_background
    assert should_background("solve the P vs NP problem")["background"] is True
    assert should_background("design a compiler from scratch")["background"] is True
    # …but a trivial script stays foreground.
    assert should_background("write a function to add two numbers")["background"] is False


@pytest.fixture
def held_jobs(monkeypatch):
    """Jobs are taken, not run. Cancelling one that had started did not stop it: it ran on after its
    test, loaded the stand-in model, and wrote n_ctx=1 into the real runtime snapshot, which a later
    test then read as this machine's window."""
    from eli.runtime.background_tasks import get_background_tasks
    taken = []
    monkeypatch.setattr(get_background_tasks(), "submit", lambda name, fn, **kw: taken.append(name) or len(taken))
    return taken


def test_fix_file_backgrounds_by_default(held_jobs):
    from eli.execution.executor_enhanced import _maybe_background_code_work
    result = _maybe_background_code_work("FIX_FILE", {"path": "eli/memory/memory.py"})
    assert result is not None
    assert result.get("background") is True
    assert "job #" in result["content"].lower()
    assert held_jobs == ["FIX_FILE: eli/memory/memory.py"]


def test_examine_sweep_backgrounds(held_jobs):
    from eli.execution.executor_enhanced import _maybe_background_code_work
    result = _maybe_background_code_work(
        "EXAMINE_CODE", {"request": "examine the codebase for bugs"})
    assert result is not None
    assert result.get("background") is True
    assert held_jobs == ["EXAMINE_CODE: examine the codebase for bugs"]


def test_examine_recall_skips_background():
    from eli.execution.executor_enhanced import _maybe_background_code_work
    result = _maybe_background_code_work(
        "EXAMINE_CODE", {"request": "list the errors you found earlier"})
    assert result is None


# ── executor job-inspection actions ─────────────────────────────────────────

def test_executor_check_and_list_jobs():
    from eli.runtime.background_tasks import get_background_tasks
    from eli.execution.executor_enhanced import execute
    bt = get_background_tasks()
    jid = bt.submit("unit-test-job", lambda: {"ok": True, "script_path": "/tmp/u.py", "solved": True})
    bt.wait(jid, timeout=5)

    r = execute("CHECK_JOB", {"job_id": jid})
    assert r["ok"] and "done" in r["content"].lower() and "/tmp/u.py" in r["content"]

    r2 = execute("BACKGROUND_JOBS", {})
    assert r2["ok"] and f"#{jid}" in r2["content"]

    r3 = execute("CHECK_JOB", {"job_id": 999999})
    assert r3["ok"] is False and "no background job" in r3["content"].lower()


def test_a_job_reports_how_far_it_has_got_and_what_is_left():
    import threading
    from eli.runtime import background_tasks as bgt
    from eli.execution.executor_enhanced import execute
    bt = bgt.get_background_tasks()
    go, half = threading.Event(), threading.Event()

    def work():
        bgt.report(0, 4, "reading")
        bgt.report(2, 4, "reading")
        half.set()
        go.wait(5)
        return {"ok": True, "content": "read"}

    jid = bt.submit("Analyse 4 PDFs: physics", work)
    assert half.wait(5)
    bt._tasks[jid].progress["since"] -= 60          # two of four took a minute: a minute to go
    t = bt.get(jid)
    assert 55 <= t["eta_s"] <= 65
    assert t["progress"]["done"] == 2 and t["progress"]["total"] == 4 and t["eta_s"] is not None
    msg = execute("CHECK_JOB", {})["content"]                 # no number: the running job
    assert f"#{jid}" in msg and "Reading: 2 of 4" in msg and "left" in msg
    from eli.execution.router_enhanced import route
    assert route("cool, do you have an eta?")["action"] == "CHECK_JOB"
    assert route("how long is the eiffel tower?")["action"] != "CHECK_JOB"
    go.set()
    bt.wait(jid, timeout=5)
    assert bt.get(jid)["eta_s"] is None


def test_a_job_whose_handler_reports_failure_failed():
    bt = BackgroundTasks(max_workers=1)
    jid = bt.submit("reads nothing", lambda: {"ok": False, "error": "the model returned nothing"})
    job = bt.wait(jid, timeout=5)
    assert job["status"] == "failed" and "returned nothing" in job["error"]


def test_a_scheduled_job_has_not_been_working_while_it_waits():
    bt = BackgroundTasks(max_workers=1)
    jid = bt.schedule("overnight", lambda: 1, delay=3600)
    assert bt.get(jid)["elapsed_s"] == 0.0
    bt.cancel(jid)


def test_the_record_outlives_the_session_and_a_cut_off_job_reads_as_cut_off(tmp_path):
    import threading
    ledger = str(tmp_path / "runtime" / "jobs.json")
    release = threading.Event()
    bt = BackgroundTasks(max_workers=2, ledger=ledger)
    done = bt.submit("finished", lambda: {"ok": True, "content": "2 of 2 read"},
                     meta={"targets": ["/a"]})
    bt.wait(done, timeout=5)
    cut = bt.submit("still going", release.wait, meta={"targets": ["/b"]})
    for _ in range(200):
        if bt.get(cut)["status"] == "running":
            break
        time.sleep(0.01)
    again = BackgroundTasks(max_workers=1, ledger=ledger)     # what the next start finds
    release.set()
    bt.wait(cut, timeout=5)
    assert again.get(done)["status"] == "done" and again.get(done)["note"] == "2 of 2 read"
    assert again.get(cut)["status"] == "interrupted" and again.get(cut)["meta"]["targets"] == ["/b"]
    assert again.submit("next", lambda: 1) > cut        # ids carry on


def test_someone_is_told_when_their_job_ends():
    heard = []
    bt = BackgroundTasks(max_workers=1)
    bt.add_listener(heard.append)
    bt.wait(bt.submit("job", lambda: {"ok": True}, meta={"tell": True}), timeout=5)
    time.sleep(0.05)
    assert heard and heard[0]["status"] == "done" and heard[0]["meta"]["tell"] is True


def test_what_is_going_on_is_a_line_and_the_result_is_there_when_asked_for():
    from eli.execution.executor_enhanced import execute
    from eli.runtime.background_tasks import get_background_tasks
    bt = get_background_tasks()
    long_report = "2 of 2 read.\n\n## Summary\n\n" + "a long report " * 40
    jid = bt.submit("a report", lambda: {"ok": True, "content": long_report})
    bt.wait(jid, timeout=5)
    asked = execute("CHECK_JOB", {"text": "what is happening? eta on that job?"})["content"]
    assert asked.startswith("Nothing is running.") and "a long report" not in asked
    assert "a long report" in execute("CHECK_JOB", {"text": "show me the result"})["content"]
    assert "a long report" in execute("CHECK_JOB", {"job_id": jid})["content"]


def test_a_job_waits_for_the_model_and_a_call_cut_short_runs_again(monkeypatch):
    from eli.cognition import gguf_inference as gi
    calls = []

    def fake(prompt="", system=None, **kw):
        calls.append(prompt)
        if len(calls) == 1:
            gi._bg_tls.cut_short = True
            return "part of an ans"
        return "the whole answer"

    monkeypatch.setattr(gi, "_chat_completion_impl", fake)
    gi.set_job_inference(True)
    try:
        assert gi.chat_completion("read this", system="s") == "the whole answer"
        assert len(calls) == 2
    finally:
        gi.set_job_inference(False)


def test_what_fails_after_a_job_goes_on_the_jobs_own_record():
    bt = BackgroundTasks(max_workers=1)
    def _broken(job):
        raise RuntimeError("no way to reach them")
    bt.add_listener(_broken)
    jid = bt.submit("job", lambda: {"ok": True})
    bt.wait(jid, timeout=5)
    deadline = time.monotonic() + 5
    while "after_job_error" not in bt.get(jid)["meta"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert bt.get(jid)["meta"]["after_job_error"] == "no way to reach them"


def test_a_read_that_fails_is_said_in_the_prompt_not_left_out(monkeypatch):
    from eli.runtime import live_reads
    def _no_session():
        raise RuntimeError("bus unavailable")
    monkeypatch.setattr(live_reads, "_media_line", _no_session)
    assert "could not be read" in live_reads.live_facts("what song is this?")


def test_a_job_started_by_someones_own_message_tells_them_when_it_ends():
    """Only the PDF-folder reading asked to be told: a code examination someone asked for ended
    without a word, and asked where the results were, the model invented reasons."""
    import contextvars
    from eli.kernel import request_context as rc
    bt = BackgroundTasks(max_workers=1)
    try:
        def in_a_turn():
            rc.turn_facts_var.set({"request_id": "r1", "autonomous": False})
            rc.user_id_var.set("someone")
            return bt.submit("examine the code", lambda: {"ok": True})
        asked = contextvars.copy_context().run(in_a_turn)
        assert bt.get(asked)["meta"]["tell"] is True and bt.get(asked)["meta"]["user_id"] == "someone"

        def autonomous():
            rc.turn_facts_var.set({"request_id": "r2", "autonomous": True})
            return bt.submit("a chore", lambda: {"ok": True})
        chore = contextvars.copy_context().run(autonomous)
        daemon = bt.submit("a daemon's job", lambda: {"ok": True})
        quiet = contextvars.copy_context().run(
            lambda: (rc.turn_facts_var.set({"autonomous": False}), bt.submit("x", lambda: 1, meta={"tell": False}))[1])
        for jid in (chore, daemon, quiet):
            assert not bt.get(jid)["meta"].get("tell"), jid
    finally:
        bt.shutdown()


def test_tell_me_when_it_is_done_marks_a_running_job_not_a_finished_one():
    import threading
    bt = BackgroundTasks(max_workers=1)
    gate = threading.Event()
    try:
        jid = bt.submit("long one", lambda: gate.wait(5))
        assert bt.tell_when_done(jid) is True and bt.get(jid)["meta"]["tell"] is True
        gate.set()
        bt.wait(jid, timeout=5)
        assert bt.tell_when_done(jid) is False and bt.tell_when_done(999) is False
    finally:
        gate.set()
        bt.shutdown()


def test_a_job_i_asked_to_hear_about_is_told_in_the_conversation_in_elis_words(monkeypatch):
    """It reached only the reminder queue as the job manager's line: nothing in the conversation, so a
    later "how did it go?" had nothing to read. It is worded by the model from the record (held to
    it), kept as ELI's turn of the conversation that asked, and shown in the chat."""
    import queue
    from types import SimpleNamespace
    import eli.cognition.inference_broker as ib
    import eli.memory as mem_mod
    from eli.planning.proactive_daemon import ProactiveDaemon
    kept, prompts = [], []
    daemon = ProactiveDaemon.__new__(ProactiveDaemon)
    daemon.suggestion_queue = queue.Queue()
    monkeypatch.setattr(mem_mod, "get_memory", lambda: SimpleNamespace(
        add_conversation_turn=lambda role, content, session_id=None, user_id=None: kept.append(
            (role, content, session_id, user_id))))
    monkeypatch.setattr(ib, "get_broker", lambda: SimpleNamespace(
        infer=lambda prompt, system="", **k: prompts.append((prompt, k)) or "Your folder reading has finished: 2 of 2 read."))
    monkeypatch.setattr("eli.cognition.output_governor.govern_output", lambda text, **k: text)
    job = {"id": 7, "name": "read a folder", "status": "done", "note": "2 of 2 read",
           "meta": {"tell": True, "user_id": "someone", "session_id": "s-1"}}
    said = daemon.tell_job_ended(job, "Job 7 (read a folder) done: 2 of 2 read")
    assert said == "Your folder reading has finished: 2 of 2 read."
    assert "2 of 2 read" in prompts[0][0] and prompts[0][1].get("background") is True
    assert kept == [("assistant", said, "s-1", "someone")]
    kind, data = daemon.suggestion_queue.get_nowait()
    assert kind == "reminder" and data["suggestion"] == said
    monkeypatch.setattr(ib, "get_broker", lambda: SimpleNamespace(
        infer=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("GGUF model not ready"))))
    assert daemon.tell_job_ended(job, "Job 7 (read a folder) done: 2 of 2 read") == "Job 7 (read a folder) done: 2 of 2 read"


def test_tell_me_when_job_n_is_done_asks_the_job_manager_and_results_find_the_job_in_hand(monkeypatch):
    """"tell me when job 3 is done" routed with notify, and the action only reported the state;
    "results?" (plural) was not a job word, and a bare "results?" searched the web for the word."""
    import threading
    from eli.execution.executor_enhanced import execute
    from eli.execution.router_enhanced import route
    from eli.runtime.background_tasks import get_background_tasks
    bt = get_background_tasks()
    gate = threading.Event()
    try:
        jid = bt.submit("long one", lambda: gate.wait(5), meta={"tell": False})
        routed = route(f"tell me when job {jid} is done")
        assert routed["action"] == "CHECK_JOB" and routed["args"]["notify"] is True
        out = execute("CHECK_JOB", routed["args"])
        assert out["will_tell"] is True and bt.get(jid)["meta"]["tell"] is True
        assert route(f"job {jid} results?")["action"] == "CHECK_JOB"
        bare = route("results?")
        assert bare["action"] == "CHECK_JOB" and str(bare["args"]["job_id"]) == str(jid)
    finally:
        gate.set()
        bt.wait(jid, timeout=5)
