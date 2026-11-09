from eli.execution import media_runtime as mr


class _RunResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _PopenResult:
    pass


def test_noop_returns_message_without_original_executor():
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return "ORIGINAL"

    execute_action = mr.install_media_executor(original)

    assert execute_action("NOOP", {"message": "Say what to play."}) == "Say what to play."
    assert calls == []


def test_youtube_play_delegates_to_original_executor():
    calls = []

    def original(action, args, *a, **kw):
        calls.append((action, args))
        return {"ok": True, "action": action, "content": "ORIGINAL", "response": "ORIGINAL"}

    execute_action = mr.install_media_executor(original)
    result = execute_action(
        "PLAY_MEDIA",
        {"target": "youtube", "query": "play youtube dr dre the watcher"},
    )

    assert calls == [
        ("PLAY_MEDIA", {"target": "youtube", "query": "play youtube dr dre the watcher"}),
    ]
    assert result["response"] == "ORIGINAL"


def test_pause_youtube_delegates_to_original_executor():
    calls = []

    def original(action, args, *a, **kw):
        calls.append((action, args))
        return {"ok": True, "action": action, "content": "⏸ Paused — YouTube",
                "response": "⏸ Paused — YouTube"}

    execute_action = mr.install_media_executor(original)
    result = execute_action("PAUSE_MEDIA", {"target": "youtube"})

    assert calls == [("PAUSE_MEDIA", {"target": "youtube"})]
    assert "Paused" in result["content"]


def test_spotify_play_delegates_to_original_executor():
    calls = []

    def original(action, args, *a, **kw):
        calls.append((action, args))
        return {"ok": True, "action": action, "content": "Playing on Spotify",
                "response": "Playing on Spotify"}

    execute_action = mr.install_media_executor(original)
    result = execute_action(
        "PLAY_MEDIA",
        {"target": "spotify", "query": "dmx"},
    )

    assert calls == [("PLAY_MEDIA", {"target": "spotify", "query": "dmx"})]
    assert result["response"] == "Playing on Spotify"


# ── "open spotify" says it opened once Spotify can take commands ─────────────────────────────────
from eli.execution.media_runtime import open_spotify as _real_open_spotify  # noqa: E402 (before the test stub)


def _clock(monkeypatch, tmp_path):
    from eli.integrations.media import cross_platform as cp
    monkeypatch.setattr(cp, "_launch_record", lambda: tmp_path / "launch_times.json")
    now = {"t": 0.0}
    monkeypatch.setattr(cp.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(cp.time, "sleep", lambda s: now.update(t=now["t"] + s))
    return now


def test_open_spotify_waits_until_spotify_is_up_and_learns_how_long_it_took(monkeypatch, tmp_path):
    from eli.integrations.media import cross_platform as cp
    now = _clock(monkeypatch, tmp_path)
    import eli.execution.media_runtime as mr2
    monkeypatch.setattr(mr2, "_launch_spotify", lambda: "Opened app: spotify")
    monkeypatch.setattr(cp, "spotify_ready", lambda: now["t"] >= 3.0)
    assert _real_open_spotify() == "Opened app: spotify"
    assert now["t"] >= 3.0
    assert cp.launch_deadline("spotify") == cp.LAUNCH_MARGIN * 3.0


def test_open_spotify_that_never_comes_up_is_not_called_open(monkeypatch, tmp_path):
    from eli.integrations.media import cross_platform as cp
    _clock(monkeypatch, tmp_path)
    import eli.execution.media_runtime as mr2
    monkeypatch.setattr(mr2, "_launch_spotify", lambda: "Opened app: spotify")
    monkeypatch.setattr(cp, "spotify_ready", lambda: False)
    monkeypatch.setattr(cp, "launch_deadline", lambda app="spotify": 5.0)
    said = _real_open_spotify()
    assert said.startswith("Started spotify, but it has not come up")
