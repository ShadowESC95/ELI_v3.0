"""Behaviour locks for process-kill safety in CLOSE_APP.

Every case here is a defect observed in a live 2.3.15 session:

  * "close file" logged the user out of their desktop. CLOSE_APP fell through
    to a bare `pkill -f <name>` loop; `-f` matches the *full command line* of
    every process as a regex, and `dbus-daemon --session ... --nopidfile`
    contains "file". On the reporting machine that pattern resolved to three
    dbus-daemon processes (system bus, session bus, at-spi bus) plus dnsmasq,
    rpcd_lsad and every Spotify process — eighteen in total.
  * The same call then reported success, because `pkill` exits 0 when it
    signals anything at all. ELI told the user twice that it had "closed the
    files via pkill" and had not logged them out.
  * `portable_app_control.close_app(force=True)` treated pkill's exit status 1
    ("nothing matched") as success too, so a complete no-op reported a
    force-close that never happened.

The guard's rule is that a kill is verified, not merely name-checked: the
pattern is dry-run through pgrep and every process it resolves to must be an
ordinary user application before a single signal is sent.
"""
import os
import shutil
from pathlib import Path

import pytest

from eli.system import process_guard as pg


# ── the exact pattern that ended the session ────────────────────────────────
@pytest.mark.parametrize("pattern", [
    "file", "files", "folder", "home", "app", "window", "session",
    "python", "python3", "bash", "system", "systemd", "daemon", "eli",
])
def test_generic_patterns_never_match_command_lines(pattern):
    """A generic word inside a command line identifies infrastructure, not an app."""
    plan = pg.check_kill_pattern(pattern, full_cmdline=True)
    assert plan.allowed is False, f"{pattern!r} would be killed by command line: {plan.reason}"
    assert plan.pids == []


def test_empty_and_tiny_targets_are_refused():
    for pattern in ("", "   ", "x", "ab"):
        plan = pg.check_kill_pattern(pattern, full_cmdline=True)
        assert plan.allowed is False, f"{pattern!r} was allowed"


# ── the processes that must never be signalled ─────────────────────────────
@pytest.mark.parametrize("name", [
    "dbus-daemon", "dbus-broker", "systemd", "systemd-logind", "init",
    "gnome-shell", "gnome-session-binary", "gdm3", "Xorg", "Xwayland",
    "mutter", "kwin_x11", "plasmashell", "sshd", "polkitd",
    "pipewire", "wireplumber", "pulseaudio", "gvfsd", "gvfsd-metadata",
    "gsd-housekeeping", "xdg-desktop-portal", "kworker/0:1",
    "winlogon.exe", "explorer.exe", "lsass.exe",
])
def test_session_critical_processes_are_protected(name):
    assert pg.is_protected_process(name) is True, f"{name} is not protected"


def test_unidentifiable_process_is_treated_as_protected():
    """Failing to name a process must fail towards refusing, not towards killing."""
    assert pg.is_protected_process("") is True
    assert pg.is_protected_process(None) is True


def test_ordinary_applications_are_not_blanket_protected():
    for name in ("firefox", "spotify", "nautilus", "code", "gimp", "vlc"):
        assert pg.is_protected_process(name) is False, f"{name} wrongly protected"


# ── a refused plan must send no signal at all ──────────────────────────────
def test_refused_kill_signals_nothing():
    res = pg.safe_pkill("file", full_cmdline=True)
    assert res["ok"] is False
    assert res["killed"] == 0
    assert res["pids"] == []
    assert res["plan"]["allowed"] is False
    assert res["reason"]


@pytest.mark.skipif(os.name != "posix", reason="signals are POSIX")
def test_an_app_is_asked_to_quit_from_its_top_process_first(monkeypatch):
    """Spotify: nine processes signalled at once left a crash dump. The top process is asked first
    and its helpers are signalled only if they are still there after it had time to close them."""
    plan = pg.KillPlan(allowed=True, reason="ok", pattern="spotify", full_cmdline=True)
    plan.pids = [100, 101, 102]
    monkeypatch.setattr(pg, "check_kill_pattern", lambda *a, **k: plan)
    monkeypatch.setattr(pg, "_parent_pid", lambda pid: {100: 1, 101: 100, 102: 100}[pid])
    signalled = []
    monkeypatch.setattr(pg.os, "kill", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(pg, "pid_alive", lambda pid: pid == 102 and signalled == [100])  # 101 closed with its parent
    res = pg.safe_pkill("spotify", full_cmdline=True, timeout=0.3)
    assert signalled[0] == 100 and signalled.count(101) == 0
    assert res["ok"] and res["pids"] == [100, 101, 102]


@pytest.mark.skipif(os.name != "posix", reason="signals are POSIX")
def test_an_app_is_closed_only_once_its_processes_are_gone(monkeypatch):
    """A signal sent was reported as the app closed, while it was still running."""
    plan = pg.KillPlan(allowed=True, reason="ok", pattern="player", full_cmdline=False)
    plan.pids = [200]
    monkeypatch.setattr(pg, "check_kill_pattern", lambda *a, **k: plan)
    monkeypatch.setattr(pg, "_parent_pid", lambda pid: 1)
    monkeypatch.setattr(pg.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(pg, "pid_alive", lambda pid: True)          # it does not go
    res = pg.safe_pkill("player", timeout=0.3)
    from eli.runtime import failure_taxonomy as ft
    assert res["ok"] is False and res["still_running"] == [200] and res["cause"] == ft.TIMEOUT
    monkeypatch.setattr(pg, "pid_alive", lambda pid: False)         # it goes
    assert pg.safe_pkill("player", timeout=0.3)["ok"] is True


def test_a_refused_or_empty_kill_says_its_cause():
    from eli.runtime import failure_taxonomy as ft
    assert pg.safe_pkill("file", full_cmdline=True)["cause"] in (ft.USER_INPUT, ft.PERMISSION, ft.MISSING)
    assert pg.safe_pkill("no-such-program-anywhere-xyz")["cause"] in (ft.MISSING, ft.DEPENDENCY)


@pytest.mark.skipif(os.name != "posix", reason="pgrep/pkill path is POSIX-only")
@pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep not installed")
def test_live_dry_run_blocks_patterns_resolving_to_protected_processes():
    """The verification, not the word list, is what has to hold.

    'dbus' is not in the generic list, so this case can only be refused by
    the dry-run actually resolving it to protected processes.
    """
    assert "dbus" not in pg.GENERIC_KILL_PATTERNS
    plan = pg.check_kill_pattern("dbus-daemon", full_cmdline=False)
    if not plan.pids and not plan.blocked:
        pytest.skip("no dbus-daemon running on this machine")
    assert plan.allowed is False, f"dbus-daemon kill was allowed: {plan.reason}"
    assert plan.blocked, "dbus-daemon should have been reported as blocked"


def test_fanout_cap_is_enforced_in_policy():
    assert pg.MAX_KILL_FANOUT <= 20, "a single app is never dozens of processes"


# ── structural lock: the raw kill loop must not come back ──────────────────
def _close_app_branch() -> str:
    # v3 decomposes the executor: CLOSE_APP is a handler in an effector module.
    src = Path("eli/execution/effectors/window_control.py").read_text(encoding="utf-8")
    start = src.index("def _h_close_app")
    end = src.index("def _h_tile_windows", start)
    return src[start:end]


def test_executor_close_app_has_no_unguarded_kill():
    branch = _close_app_branch()
    assert "safe_pkill" in branch, "CLOSE_APP no longer routes through the guard"
    for forbidden in ('["pkill", "-f", name]', '["pkill", name]', '["killall", name]'):
        assert forbidden not in branch, f"unguarded kill restored: {forbidden}"


def test_close_app_without_a_target_asks_rather_than_guessing():
    from eli.execution import executor_enhanced as ex
    res = ex.execute("CLOSE_APP", {})
    assert isinstance(res, dict)
    assert res.get("ok") is False
    assert "specify" in str(res.get("content", "")).lower()


# ── the guard must protect every platform, not just the one it was written on ──
@pytest.mark.parametrize("name", [
    # macOS: killing any of these ends the session or the window server
    "WindowServer", "loginwindow", "SystemUIServer", "Dock", "Finder",
    "coreaudiod", "securityd", "opendirectoryd", "launchd", "configd",
    "kernel_task", "diskarbitrationd",
])
def test_macos_session_processes_are_protected(name):
    assert pg.is_protected_process(name) is True, f"{name} unprotected on macOS"


def test_macos_full_paths_resolve_to_the_protected_name():
    """`ps -o comm=` returns a full path on macOS, not a bare name."""
    full = ("/System/Library/PrivateFrameworks/SkyLight.framework/"
            "Resources/WindowServer")
    assert pg.is_protected_process(full) is True


@pytest.mark.parametrize("name", [
    "winlogon.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
    "explorer.exe", "dwm.exe", "smss.exe", "svchost.exe", "fontdrvhost.exe",
    "audiodg.exe", "spoolsv.exe",
])
def test_windows_session_processes_are_protected(name):
    assert pg.is_protected_process(name) is True, f"{name} unprotected on Windows"


def test_windows_has_a_verified_kill_path():
    """Windows had no force-close path at all: pkill is POSIX-only, so
    'kill <app>' silently did nothing there."""
    assert hasattr(pg, "_check_kill_pattern_windows")
    assert hasattr(pg, "_windows_processes")
    src = Path("eli/system/process_guard.py").read_text(encoding="utf-8")
    # The same rules must apply, not a weaker set.
    win = src[src.index("def _check_kill_pattern_windows"):src.index("def check_kill_pattern")]
    for rule in ("is_protected_process", "_own_pids", "MAX_KILL_FANOUT"):
        assert rule in win, f"Windows path skips {rule}"


def test_windows_kill_is_not_forced():
    """/F would kill an app mid-write; the POSIX path sends SIGTERM, so the
    Windows path must be equally polite."""
    src = Path("eli/system/process_guard.py").read_text(encoding="utf-8")
    block = src[src.index('if os.name == "nt":', src.index("def safe_pkill")):]
    assert '"/F"' not in block and "'/F'" not in block, "Windows kill uses /F"


def test_a_close_is_done_only_when_the_process_is_gone(monkeypatch):
    """safe_pkill said ok when the signal was sent; an app that catches SIGTERM (a save prompt)
    stayed up and was reported closed."""
    import subprocess
    import sys

    stubborn = subprocess.Popen([sys.executable, "-c",
                                 "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"])
    try:
        import time
        time.sleep(0.5)          # its handler is in place
        monkeypatch.setattr(pg, "check_kill_pattern", lambda pattern, full_cmdline=False: pg.KillPlan(
            allowed=True, reason="test", pattern=pattern, full_cmdline=full_cmdline, pids=[stubborn.pid]))
        res = pg.safe_pkill("stubborn-test-app", timeout=1.0)
        assert res["ok"] is False and res["still_running"] == [stubborn.pid] and "still running" in res["reason"]
    finally:
        stubborn.kill()
        stubborn.wait()



@pytest.mark.parametrize("force", [False, True])
def test_the_close_effector_signals_an_app_only_when_told_to_force_it(monkeypatch, force):
    """The effector reached after close_app signalled the app on a plain "close X": a signal skips
    its "save your changes?". Without force it says the app needs force, and signals nothing."""
    from eli.execution.effectors import window_control as wc
    signalled = []
    monkeypatch.setattr(wc.shutil, "which", lambda c, *a, **k: None)          # no window tool here
    monkeypatch.setattr(pg, "check_kill_pattern", lambda pattern, full_cmdline=False: pg.KillPlan(
        allowed=True, reason="1 matching process(es), none protected", pattern=pattern,
        full_cmdline=full_cmdline, pids=[4242]))
    monkeypatch.setattr(pg, "safe_pkill", lambda pattern, full_cmdline=False, **k: signalled.append(pattern)
                        or {"ok": True, "killed": 1, "plan": {}})
    r = wc._h_close_app("CLOSE_APP", {"name": "a drawing app", "force": force})
    if force:
        assert r["ok"] is True and signalled == ["a drawing app"]
    else:
        assert signalled == [], "a plain close signalled the app"
        assert r["ok"] is False and r["needs_force"] and r["handled"] and r["action"] == "CLOSE_APP"
        assert "force close a drawing app" in r["content"]


def test_a_window_asked_to_close_is_reported_closed_only_once_the_app_is_gone(monkeypatch):
    """`wmctrl -c` exiting 0 was reported "Closed": it only asks the window, and the app may be asking
    whether to save. Closed once its processes are gone; otherwise said as it is."""
    from eli.execution.effectors import window_control as wc
    import eli.system.portable_app_control as pac
    from eli.utils import platform_compat
    monkeypatch.setattr(platform_compat, "LINUX", True)
    monkeypatch.setattr(wc.shutil, "which", lambda c, *a, **k: "/usr/bin/wmctrl" if c == "wmctrl" else None)
    monkeypatch.setattr(wc, "_run_ok", lambda argv, *a, **k: True)
    monkeypatch.setattr(pac, "wait_bound", lambda kind, key: 0.0)
    monkeypatch.setattr(pac, "_record_timing", lambda *a, **k: None)
    running = [True]
    monkeypatch.setattr(pac, "_app_running", lambda needles, loose=True: running[0])
    still = wc._h_close_app("CLOSE_APP", {"name": "a drawing app"})
    assert still["ok"] is False and still["handled"] and "still open" in still["content"]
    running[0] = False
    gone = wc._h_close_app("CLOSE_APP", {"name": "a drawing app"})
    assert gone["ok"] is True and gone["action"] == "CLOSE_APP"


def test_a_site_is_closed_as_its_tab_never_the_browser(monkeypatch):
    """"close <site>" closed the browser's whole window (wmctrl -c on the window whose title showed it)
    or ended the browser: every other tab went with it. The tab is brought to the front, checked, and
    closed with the browser's own key; one that cannot be brought to the front is said so."""
    import eli.system.portable_app_control as pac
    from eli.execution.effectors import window_control as wc
    from eli.execution.executor_enhanced import execute
    keys, signalled, front = [], [], {"shows": False}
    monkeypatch.setattr(pac, "_system", lambda: "linux")
    monkeypatch.setattr(pac, "focus_app", lambda name: {"ok": True})
    monkeypatch.setattr(pac, "active_window_matches", lambda name: front["shows"])
    monkeypatch.setattr(pac, "_xdotool_key", lambda k: keys.append(k) or front.update(shows=False) or True)
    monkeypatch.setattr(pac, "wait_bound", lambda kind, key: 0.0)
    monkeypatch.setattr(pac, "_record_timing", lambda *a, **k: None)
    monkeypatch.setattr(pac, "close_app", lambda *a, **k: signalled.append(("close_app", a)) or {"ok": True})
    monkeypatch.setattr(pg, "safe_pkill", lambda *a, **k: signalled.append(("pkill", a)) or {"ok": True})
    monkeypatch.setattr(wc, "_run_ok", lambda argv, *a, **k: signalled.append(("run", argv)) or True)
    args = {"name": "a-video-site.com", "target": "a-video-site.com", "site": "a-video-site.com", "tab": True}
    r = execute("CLOSE_APP", dict(args))
    assert r["ok"] is False and r["handled"] and keys == [] and "front" in r["content"]
    front["shows"] = True
    r = execute("CLOSE_APP", dict(args))
    assert r["ok"] is True and keys == ["ctrl+w"] and r["tab"] is True
    front["shows"] = True
    keys.clear()
    r = wc._h_close_app("CLOSE_APP", dict(args))
    assert r["ok"] is True and keys == ["ctrl+w"]
    assert signalled == [], "the browser's window or process was reached"
