"""Behaviour locks for desktop control acting on the thing the user named.

Every case here is a defect observed in a live 2.3.15 session:

  * "maximise prime video" maximised the terminal. The router extracted
    window_name="prime video" correctly; the executor's MAXIMISE_WINDOW branch
    never read args at all and ran `wmctrl -r :ACTIVE:` unconditionally.
    MINIMISE_WINDOW, twelve lines above it, did read the name — the two halves
    of window control had drifted apart.
  * "open a browser on KITE" and "open browser KITE" were captured whole as app
    names and answered with "not installed on this machine — would you like me
    to download it?". OPEN_BROWSER has accepted a query all along; three
    separate matchers claimed the phrase first.
  * "click enter", "click enter button" and "move cursor right" moved nothing
    while the executor reported success for all three: unrecognised actions
    fell into an `else` that returned ok=True without touching the mouse,
    clicks required both x and y, relative movement was never implemented,
    and "enter" is a key that had arrived in the mouse-button slot.
"""
from pathlib import Path

import pytest

from eli.execution import router_enhanced as router
from eli.execution import executor_enhanced as ex
from eli.system import portable_app_control as pac
from eli.execution.router_enhanced import route


def _route(text: str) -> dict:
    out = router.route(text)
    assert isinstance(out, dict) and "action" in out, f"{text!r} -> {out!r}"
    return out


# ── a browser request is a browser request ─────────────────────────────────
@pytest.mark.parametrize("phrase,query", [
    ("open a browser on KITE", "kite"),
    ("open browser KITE", "kite"),
    ("open a web browser on quantum field theory", "quantum field theory"),
    ("open browser for quantum mechanics", "quantum mechanics"),
    ("open the browser and search KITE", "kite"),
])
def test_browser_with_a_subject_opens_a_search(phrase, query):
    out = _route(phrase)
    assert out["action"] == "OPEN_BROWSER", f"{phrase!r} -> {out['action']}"
    assert (out.get("args") or {}).get("query", "").lower() == query


@pytest.mark.parametrize("phrase", ["open browser", "open the browser", "open a browser"])
def test_bare_browser_opens_the_default_browser(phrase):
    out = _route(phrase)
    assert out["action"] == "OPEN_BROWSER", f"{phrase!r} -> {out['action']}"
    assert not (out.get("args") or {}).get("query")


@pytest.mark.parametrize("phrase", [
    "open a browser on KITE", "open browser KITE", "open browser",
    "open a web browser on quantum field theory",
])
def test_browser_requests_never_become_app_installs(phrase):
    """The observed failure was an offer to install a program called
    'a browser on kite'."""
    out = _route(phrase)
    assert out["action"] != "OPEN_APP", f"{phrase!r} still routes to OPEN_APP"


# ── things that must keep working ──────────────────────────────────────────
@pytest.mark.parametrize("phrase,action", [
    ("open firefox", "OPEN_APP"),
    ("open spotify", "OPEN_APP"),
    ("open a terminal", "OPEN_APP"),
    ("open camera", "OPEN_APP"),
    ("open steam", "OPEN_APP"),
    ("open downloads", "OPEN_FILE_SYSTEM"),
    ("open home folder", "OPEN_FILE_SYSTEM"),
    ("open the trash", "OPEN_FILE_SYSTEM"),
    ("open github.com", "OPEN_URL"),
])
def test_other_open_targets_are_unchanged(phrase, action):
    assert _route(phrase)["action"] == action, f"{phrase!r} regressed"


def test_named_browsers_still_launch_as_apps():
    """'open firefox' is an app launch; only the generic word is a surface."""
    out = _route("open firefox")
    assert out["action"] == "OPEN_APP"
    assert "firefox" in str(out.get("args"))


# ── maximise must act on the named window, or on nothing ───────────────────
def test_maximise_uses_the_named_window(monkeypatch):
    seen = {}

    def _fake_maximize(name):
        seen["name"] = name
        return {"ok": True, "action": "MAXIMIZE_APP", "content": f"Maximised {name}."}

    monkeypatch.setattr(pac, "maximize_app", _fake_maximize)
    res = ex.execute("MAXIMISE_WINDOW", {"window_name": "prime video"})
    assert seen.get("name") == "prime video", "the named window was discarded again"
    assert res.get("ok") is True


@pytest.mark.parametrize("key", ["name", "target", "app", "window_name", "window"])
def test_maximise_reads_every_target_key_the_router_emits(monkeypatch, key):
    seen = {}
    monkeypatch.setattr(pac, "maximize_app",
                        lambda name: seen.setdefault("name", name) and None
                        or {"ok": True, "action": "MAXIMIZE_APP", "content": "ok"})
    ex.execute("MAXIMISE_WINDOW", {key: "prime video"})
    assert seen.get("name") == "prime video", f"args[{key!r}] ignored"


def test_maximise_does_not_fall_back_to_the_active_window(monkeypatch):
    """Acting on a different window than the one named is the defect itself."""
    monkeypatch.setattr(pac, "maximize_app",
                        lambda name: {"ok": False, "action": "MAXIMIZE_APP",
                                      "content": f"Could not find a window for: {name}"})
    res = ex.execute("MAXIMISE_WINDOW", {"window_name": "prime video"})
    assert res.get("ok") is False, "a missing named window silently maximised something else"
    assert "prime video" in str(res.get("content", ""))


@pytest.mark.parametrize("bare", [{}, {"name": ""}, {"name": "current window"},
                                  {"name": "it"}, {"name": "this"}])
def test_bare_maximise_still_means_the_active_window(monkeypatch, bare):
    called = {"n": 0}

    def _fake(name):
        called["n"] += 1
        return {"ok": True, "content": "should not be reached"}

    monkeypatch.setattr(pac, "maximize_app", _fake)
    ex.execute("MAXIMISE_WINDOW", bare)
    assert called["n"] == 0, f"{bare!r} was treated as a named target"


def test_maximize_app_exists_for_every_platform():
    src = Path("eli/system/portable_app_control.py").read_text(encoding="utf-8")
    block = src[src.index("def maximize_app"):]
    for plat in ('"linux"', '"darwin"', '"windows"', '"android"'):
        assert plat in block, f"maximize_app has no {plat} branch"


# ── the mouse must not report work it did not do ───────────────────────────
@pytest.mark.parametrize("args", [
    {"action": "click", "button": "purple"},
    {"action": "levitate"},
    {"action": "move"},
    {"action": "move", "direction": "sideways"},
    {},
])
def test_impossible_mouse_requests_fail_honestly(args):
    res = ex.execute("MOUSE_CONTROL", args)
    assert res.get("ok") is False, f"{args!r} reported success without acting"
    assert str(res.get("content", "")).strip(), "failed silently with no explanation"


def test_key_in_the_button_slot_is_pressed_not_clicked(monkeypatch):
    """'click enter' is a keystroke. It used to be an unclickable button."""
    import eli.perception.os_controller as osc
    pressed = {}
    monkeypatch.setattr(osc, "press_key",
                        lambda k: pressed.setdefault("key", k) and None
                        or {"ok": True, "content": f"Pressed {k}"})
    res = ex.execute("MOUSE_CONTROL", {"action": "click", "button": "enter"})
    assert pressed.get("key") == "enter", "the key was not routed to the keyboard"
    assert res.get("ok") is True
    assert res.get("rerouted_from") == "MOUSE_CONTROL"


def _strip_comments(code: str) -> str:
    """Structural checks must read code, not the comments describing the bug
    being prevented — those legitimately quote the old broken values."""
    return "\n".join(line.split("#", 1)[0] for line in code.splitlines())


def _mouse_branch() -> str:
    # MOUSE_CONTROL is handled by its effector since the inline executor branch was retired.
    import inspect

    from eli.execution.effectors import input_control
    return _strip_comments(inspect.getsource(input_control._h_mouse_control))


def test_mouse_branch_has_no_unconditional_success():
    branch = _mouse_branch()
    assert "performed" not in branch, "the phantom-success message is back"
    # Every success return must be reached from a backend result, never from
    # a bare else. The backends are the only places that build an ok=True.
    assert branch.count('"ok": True') == 1, (
        "more than one success path in MOUSE_CONTROL — the single one is the "
        "backend loop, which only runs after a backend reports it acted"
    )


def test_mouse_branch_supports_relative_movement():
    branch = _mouse_branch()
    assert "mousemove_relative" in branch or "moveRel" in branch, \
        "relative movement ('move cursor right') is unimplemented again"


def test_wayland_click_releases_the_button():
    """The old ydotool codes sent a press with no release — a stuck button —
    and named the wrong buttons (0x40001 is right-down, not left-click)."""
    branch = _mouse_branch()
    assert "0x40001" not in branch and "0x40002" not in branch, \
        "the press-without-release ydotool codes are back"
    assert "0xC0" in branch, "left click should be 0xC0 (down+up) for ydotool"


@pytest.mark.parametrize("phrase,direction,amount", [
    ("move cursor right", "right", 100),
    ("move the mouse left 50 pixels", "left", 50),
    ("nudge pointer down by 25 px", "down", 25),
    ("move mouse up 200", "up", 200),
])
def test_relative_mouse_movement_routes(phrase, direction, amount):
    out = _route(phrase)
    assert out["action"] == "MOUSE_CONTROL", f"{phrase!r} -> {out['action']}"
    args = out.get("args") or {}
    assert args.get("action") == "move"
    assert args.get("direction") == direction
    assert int(args.get("amount") or args.get("pixels") or 0) == amount


@pytest.mark.parametrize("phrase,query", [
    ("click the submit button", "submit"),
    ("click submit button", "submit"),
    ("press the cancel button", "cancel"),
    ("tap the ok button", "ok"),
])
def test_click_named_button_routes_to_screen_locate(phrase, query):
    out = _route(phrase)
    assert out["action"] == "SCREEN_LOCATE", f"{phrase!r} -> {out['action']}"
    args = out.get("args") or {}
    assert args.get("click") is True
    assert query in str(args.get("query") or "").lower()


def test_click_at_cursor_routes_mouse_control():
    out = _route("click at cursor")
    assert out["action"] == "MOUSE_CONTROL"
    assert (out.get("args") or {}).get("action") == "click"


# ── Linux is two platforms: X11 and Wayland ────────────────────────────────
def test_display_server_is_detected():
    ds = pac.display_server()
    assert ds in {"wayland", "x11", ""}, ds


def test_wayland_failure_explains_itself():
    """wmctrl/xdotool see only XWayland windows, so under Wayland they fail
    silently. A bare 'Failed to maximise window' tells the user nothing."""
    advice = pac._wayland_window_advice("maximise Prime Video")
    low = advice.lower()
    assert "wayland" in low
    assert "kdotool" in low or "xwayland" in low
    assert "maximise prime video" in low


def test_wayland_capable_tools_are_attempted():
    src = Path("eli/system/portable_app_control.py").read_text(encoding="utf-8")
    block = src[src.index("def maximize_app"):]
    assert "_wayland_window_tools()" in block, "Wayland tools are never tried"


@pytest.mark.parametrize("platform_marker,forbidden", [
    ("win", "apt install"),
    ("darwin", "apt install"),
])
def test_mouse_advice_is_not_linux_only(platform_marker, forbidden):
    """The fallback told Windows and macOS users to `apt install xdotool`."""
    branch = _mouse_branch()
    idx = branch.index(platform_marker)
    window = branch[idx:idx + 400]
    assert forbidden not in window, f"{platform_marker} advice still says {forbidden!r}"


def test_macos_mouse_advice_mentions_accessibility():
    """macOS blocks synthetic input entirely until the app is granted
    Accessibility permission — without that note the failure looks like a bug."""
    branch = _mouse_branch()
    assert "Accessibility" in branch


# "Opened app: X" only when X is running afterwards.
#
# Spotify started from the AppImage died on the bundle's libraries a moment after the launch,
# and ELI had already said "Opened app: Spotify": the launch call returning was taken as the app
# being open.
@pytest.fixture
def linux_spotify(monkeypatch, tmp_path):
    monkeypatch.setattr(pac, "_timings_file", lambda: tmp_path / "app_timings.json")
    monkeypatch.setattr(pac, "_system", lambda: "linux")
    monkeypatch.setattr(pac, "resolve_app", lambda q: pac.AppCandidate(
        name="Spotify", command=["spotify"], desktop_id="spotify.desktop", source="desktop"))
    monkeypatch.setattr(pac.shutil, "which", lambda c, *a, **k: f"/usr/bin/{c}")
    monkeypatch.setattr(pac, "_popen", lambda args: True)
    monkeypatch.setattr(pac, "_ready_signal", lambda target, needles: "")   # no window or player yet
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    return monkeypatch


def test_an_app_that_dies_at_once_is_not_reported_open(linux_spotify):
    clock = iter(range(1000))
    linux_spotify.setattr("time.monotonic", lambda: next(clock))
    linux_spotify.setattr(pac, "_app_running", lambda needles, loose=True: False)
    r = pac.open_app("spotify")
    assert r["ok"] is False and "closed straight away" in r["content"]


def test_an_app_that_stays_up_is_open(linux_spotify):
    # not up before the launch, up after it, and its window shows
    linux_spotify.setattr(pac, "_app_running", lambda needles, loose=True: loose and "spotify" in needles)
    linux_spotify.setattr(pac, "_ready_signal", lambda target, needles: "window")
    r = pac.open_app("spotify")
    assert r["ok"] is True and r["ready"] is True and r["ready_by"] == "window"


def test_an_app_is_ready_when_its_window_or_player_is_up_and_that_time_is_kept(linux_spotify):
    """'open spotify and play ...' asked a player that was not there yet: the launch returning was
    taken as the app being ready. It is ready when its window or its player name shows."""
    clock = iter(x * 0.5 for x in range(1000))
    linux_spotify.setattr("time.monotonic", lambda: next(clock))
    linux_spotify.setattr(pac, "_app_running", lambda needles, loose=True: loose)
    checks = iter(["", "", "", "media player"])
    linux_spotify.setattr(pac, "_ready_signal", lambda target, needles: next(checks))
    r = pac.open_app("spotify")
    assert r["ready"] is True and r["ready_by"] == "media player" and r["ready_after_s"] > 0
    assert pac._timings()["ready"]["spotify"] == [r["ready_after_s"]]


def test_the_wait_is_measured_from_the_apps_past_launches(linux_spotify):
    pac._record_timing("ready", "spotify", 4.0)
    pac._record_timing("ready", "spotify", 6.0)
    assert pac.wait_bound("ready", "spotify") == pac._WAIT_FACTOR * 6.0
    assert pac.wait_bound("ready", "an-app-never-timed") == pac._WAIT_FACTOR * 6.0   # this machine's slowest
    # running but never shown within that: said so, not "Opened"
    clock = iter(range(1000))
    linux_spotify.setattr("time.monotonic", lambda: next(clock))
    linux_spotify.setattr(pac, "_app_running", lambda needles, loose=True: loose)
    r = pac.open_app("spotify")
    assert r["ok"] is True and r["ready"] is False and "has not appeared yet" in r["content"]


def test_the_needles_name_the_app():
    target = pac.AppCandidate(name="LibreOffice Writer", command=["/usr/bin/libreoffice"],
                              desktop_id="libreoffice-writer.desktop")
    needles = pac._process_needles(target)
    assert "libreoffice" in needles and "writer" in needles


def test_only_the_program_counts_not_its_arguments(monkeypatch):
    import psutil

    class P:
        def __init__(self, pid, name, cmdline):
            self.info = {"pid": pid, "name": name, "cmdline": cmdline}

    procs = [P(10, "playerctl", ["playerctl", "-p", "spotify", "status"]),
             P(11, "grep", ["grep", "spotify"]),
             P(12, "gnome-shell", ["/usr/bin/gnome-shell"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert not pac._app_running(["spotify"])
    procs.append(P(13, "spotify", ["/usr/share/spotify/spotify", "--uri=x"]))
    assert pac._app_running(["spotify"])


def test_before_a_launch_only_the_program_itself_counts(monkeypatch):
    import psutil

    class P:
        def __init__(self, pid, name, cmdline):
            self.info = {"pid": pid, "name": name, "cmdline": cmdline}

    procs = [P(10, "gsd-xsettings", ["/usr/libexec/gsd-xsettings"]),
             P(11, "java", ["/usr/bin/java", "-jar", "/opt/jdownloader/JDownloader.jar"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert not pac._app_running(["settings"], loose=False)
    assert pac._app_running(["jdownloader"])                 # stayed up, run by java
    procs.append(P(12, "soffice.bin", ["/usr/lib/libreoffice/program/soffice.bin", "--writer"]))
    assert pac._app_running(["libreoffice"], loose=False)


def test_a_vendor_name_is_not_the_app():
    target = pac.AppCandidate(name="Calculator", command=["gnome-calculator"],
                              desktop_id="org.gnome.Calculator.desktop")
    assert "gnome" not in pac._process_needles(target)


# "click compose" finds Compose on the screen and clicks it; "find the word compose" finds it.
#
# Live: "click compose" went to the model's guess, which picked dictation and replied
# "<click> compose </click>"; "find the word compose" searched the web for "word compose".
@pytest.mark.parametrize("said,query", [
    ("click compose", "compose"), ("click on compose", "compose"), ("tap send", "send"),
    ("click the inbox", "inbox"), ("click the submit button", "submit"),
])
def test_click_finds_and_clicks(said, query):
    r = route(said)
    assert r["action"] == "SCREEN_LOCATE" and r["args"]["query"] == query and r["args"].get("click") is True


@pytest.mark.parametrize("said,query", [("find the word compose", "compose"),
                                        ("find the text sign in on the page", "sign in")])
def test_find_the_word_looks_on_the_screen(said, query):
    r = route(said)
    assert r["action"] == "SCREEN_LOCATE" and r["args"]["query"] == query and not r["args"].get("click")


@pytest.mark.parametrize("said,action", [("left click", "GAZE_CLICK"), ("click play", "MEDIA_CONTROL"),
                                         ("press enter", "KEYBOARD"), ("find cheap flights to rome", "OPEN_BROWSER")])
def test_mouse_media_keys_and_web_are_unchanged(said, action):
    assert route(said)["action"] == action


# ── closing: the app is asked, and "Closed" is said once it is gone ──────────────
@pytest.fixture
def gnome_text_editor(monkeypatch, tmp_path):
    """A native Wayland window, which wmctrl cannot see, of an app with its own quit action."""
    from eli.system import process_guard
    monkeypatch.setattr(pac, "_timings_file", lambda: tmp_path / "app_timings.json")
    monkeypatch.setattr(pac, "_system", lambda: "linux")
    monkeypatch.setattr(pac, "resolve_app", lambda q: pac.AppCandidate(
        name="Text Editor", command=["gnome-text-editor"], desktop_id="org.gnome.TextEditor.desktop",
        source="desktop"))
    monkeypatch.setattr(pac.shutil, "which", lambda c, *a, **k: f"/usr/bin/{c}" if c in ("wmctrl", "gapplication") else None)
    asked, signalled = [], []

    import subprocess

    def run(args, timeout=8.0):
        asked.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")   # wmctrl lists no window

    monkeypatch.setattr(pac, "_run", run)
    monkeypatch.setattr(process_guard, "safe_pkill", lambda *a, **k: signalled.append(a) or {"ok": True, "killed": 1})
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    return monkeypatch, asked, signalled


def test_a_plain_close_asks_the_app_and_never_signals_it(gnome_text_editor):
    """'close text editor' on GNOME Wayland ended the editor by SIGTERM, without its save prompt."""
    mp, asked, signalled = gnome_text_editor
    running = iter([True, True, False])          # running when asked, gone a moment later
    mp.setattr(pac, "_app_running", lambda needles, loose=True: next(running))
    r = pac.close_app("text editor")
    assert r["ok"] is True and r["content"] == "Closed Text Editor."
    assert ["/usr/bin/gapplication", "action", "org.gnome.TextEditor", "quit"] in asked
    assert signalled == []


def test_an_app_still_open_after_being_asked_is_said_so_and_left_alone(gnome_text_editor):
    mp, asked, signalled = gnome_text_editor
    clock = iter(range(1000))
    mp.setattr("time.monotonic", lambda: next(clock))
    mp.setattr(pac, "_app_running", lambda needles, loose=True: True)   # a save prompt is up
    r = pac.close_app("text editor")
    assert r["ok"] is False and r["asked_to_close"] and r["handled"] and "still open" in r["content"]
    assert signalled == []



def test_an_app_left_open_after_being_asked_is_not_then_signalled_by_the_executor(gnome_text_editor):
    """close_app said the editor was still open (a save prompt, maybe) and marked it handled, but the
    executor dropped every result that was not ok and went on to the effector, which signalled it."""
    import itertools
    mp, asked, signalled = gnome_text_editor
    clock = itertools.count()
    mp.setattr("time.monotonic", lambda: next(clock))
    mp.setattr(pac, "_app_running", lambda needles, loose=True: True)
    window_closes = []
    mp.setattr("eli.execution.effectors.window_control._run_ok", lambda argv, timeout=5: window_closes.append(argv) or False)
    r = ex.execute("CLOSE_APP", {"name": "text editor"})
    assert r["ok"] is False and r.get("handled") and "still open" in r["content"], r
    assert signalled == [] and window_closes == []
