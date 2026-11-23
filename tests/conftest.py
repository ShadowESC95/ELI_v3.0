import sys, os, pytest
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["ELI_TEST_MODE"] = "1"
os.environ["ELI_FORCE_CPU"] = "1"
_MACHINE_CUDA_DEVICES = os.environ.get("CUDA_VISIBLE_DEVICES")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")  # the real GPU libraries load; the card stays the app's, as on CI

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Each run starts as a fresh install: what an earlier run left in the throwaway sandbox (its vector
# index, the world's sessions, the databases) is gone before anything below writes or reads it. An
# index copied in once was searched by every later run, and a VR session one run recorded turned up
# in the next run's period log.
# A run still going in this checkout holds the sandbox (a shared lock, kept for the session): then the
# sandbox is left as it is, rather than pulled from under that run's open databases. The lock is in the
# system's temporary folder, one per checkout: beside the sandbox it was a file the run left in the
# checkout outside its sandbox.
_SANDBOX_ROOT = ROOT / "artifacts" / "_pytest"


def _sandbox_lock() -> Path:
    import hashlib
    import tempfile
    key = hashlib.sha1(str(ROOT.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"eli-pytest-sandbox-{key}.lock"


def _fresh_sandbox() -> None:
    import shutil
    if _SANDBOX_ROOT.parent.parent != ROOT:
        return
    _SANDBOX_ROOT.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:                 # Windows: a file another run has open is not deleted anyway
        shutil.rmtree(_SANDBOX_ROOT, ignore_errors=True)
        return
    global _SANDBOX_HOLD
    _SANDBOX_HOLD = open(_sandbox_lock(), "a+")
    try:
        fcntl.flock(_SANDBOX_HOLD, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fcntl.flock(_SANDBOX_HOLD, fcntl.LOCK_SH)         # another run is using it
        return
    shutil.rmtree(_SANDBOX_ROOT, ignore_errors=True)
    fcntl.flock(_SANDBOX_HOLD, fcntl.LOCK_SH)             # this run's, until it ends


_SANDBOX_HOLD = None
_fresh_sandbox()
_SANDBOX_STARTED = __import__("time").time()     # this run's start, for the check that nothing older is in it

# Tests never reach this machine's desktop, as on a CI runner: no session bus (media players,
# notifications) and no display for an app a test launches. A test run once started the real
# Spotify and played a song.
for _desktop_var in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
    os.environ.pop(_desktop_var, None)
os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(ROOT / "artifacts" / "_pytest" / "no-session-bus")
# Nor is a file opened on it: with no display xdg-open still started LibreOffice on a summary and it
# stayed running after the tests. These launchers do nothing here; a test of one stubs its own.
_no_launchers = ROOT / "artifacts" / "_pytest" / "no-launchers"
_no_launchers.mkdir(parents=True, exist_ok=True)
for _launcher in ("xdg-open", "gio", "gtk-launch", "open"):
    (_no_launchers / _launcher).write_text("#!/bin/sh\nexit 0\n")
    (_no_launchers / _launcher).chmod(0o755)
os.environ["PATH"] = str(_no_launchers) + os.pathsep + os.environ.get("PATH", "")
# Nor its sound or mixer: the real audio libraries load in tests, so the sound servers are out of
# reach and ALSA knows only a null device (as on a CI runner, PortAudio then does not start). Nor the internet: a request that is not stubbed fails at once.
(ROOT / "artifacts" / "_pytest").mkdir(parents=True, exist_ok=True)
(ROOT / "artifacts" / "_pytest" / "asound.conf").write_text("pcm.!default { type null }\n")
os.environ["ALSA_CONFIG_PATH"] = str(ROOT / "artifacts" / "_pytest" / "asound.conf")
os.environ["PULSE_SERVER"] = "unix:" + str(ROOT / "artifacts" / "_pytest" / "no-sound-server")
os.environ["PIPEWIRE_REMOTE"] = str(ROOT / "artifacts" / "_pytest" / "no-sound-server")
os.environ["JACK_NO_START_SERVER"] = "1"
for _proxy in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[_proxy] = "http://127.0.0.1:9"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# Every place ELI keeps state is a throwaway IN-PROJECT dir for the whole session (in-project, so
# runtime_settings does not strip it as a stale path). data_dir() holds the conversations,
# incidents, runtime state, per-user files and databases; config_dir() the small settings files
# (pomodoro, voice clones); learning_dir() the training runs. Pointing these roots at the sandbox
# covers everything resolved through them, so no test can write into this checkout's own state.
# The config dir starts each run as a fresh install has it: the tracked files and nothing else.
# Which variable puts which store there is tests/_state_env.py's (a module run without this conftest
# uses the same list). Starting an engine regenerates the capabilities table and the manifest into
# the data folder, the sandbox here; only the claims bootstrap (tests/claims/conftest.py) brings the
# checkout's shipped manifest up to date.
SANDBOX = ROOT / "artifacts" / "_pytest"
from tests._state_env import outside as _stores_outside, state_env as _state_env  # noqa: E402
os.environ.update(_state_env(SANDBOX))


def _fresh_config() -> None:
    import shutil
    import subprocess
    target = SANDBOX / "config"
    shutil.rmtree(target, ignore_errors=True)
    try:
        tracked = subprocess.run(["git", "ls-files", "-z", "config"], cwd=ROOT, capture_output=True,
                                 check=True).stdout.decode("utf-8").split("\0")
    except (OSError, subprocess.CalledProcessError):
        tracked = []                    # not a git checkout: start with none, as an empty install would
    for name in filter(None, tracked):
        dest = target / Path(name).relative_to("config")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    target.mkdir(parents=True, exist_ok=True)


_fresh_config()
from tests._state_env import seed_identity as _seed_identity  # noqa: E402
_seed_identity(SANDBOX / "config")      # the run's own owner, never this machine's ~/.eli_user_id
os.environ.setdefault("ELI_CPU_PREFLIGHT", "0")  # no smoke subprocess in tests
# Every store is in the sandbox (state_env above): the user and memory DB (user_db_path() honours
# ELI_USER_DB/ELI_MEMORY_DB first: tests once wrote rows into the real user.sqlite3), the agent DB, the
# persona overlay and notebook (a run once replaced the real persona.auto.txt), the world (ELI_WORLD_DIR,
# read at each use), the audit chain and its HMAC key, the agenda and the settings.
_PYTEST_DB = Path(os.environ["ELI_USER_DB"])
_PYTEST_DB.parent.mkdir(parents=True, exist_ok=True)
# The document index stores whatever a turn reads and embeds it on a worker thread. Off by
# default here; the tests for it build their own on a tmp path.
os.environ.setdefault("ELI_DOCUMENT_INDEX", "0")
# Each run starts with no settings file, as a fresh install and CI do: tests read the developer's own
# settings (Net toggle, Full Control, model paths) and some wrote to them; with Full Control on, a
# test's "offline" connect went out to the network.
_PYTEST_SETTINGS = Path(os.environ["ELI_SETTINGS_FILE"])
_PYTEST_SETTINGS.unlink(missing_ok=True)

# Hard isolation guard: fail LOUDLY at collection if any store still resolves outside the sandbox
# (tests/_state_env.stores: the DBs, the persona overlay and persona.py's own constant for it, the
# notebook, the vector index, the world's state file, the settings). This makes "no test can
# change memory" an enforced invariant, not just configuration a future refactor could break.
def _assert_db_isolated() -> None:
    escaped = _stores_outside(ROOT / "artifacts" / "_pytest")
    if escaped:
        raise RuntimeError(
            "TEST ISOLATION BREACH: " + "; ".join(f"{name} resolves to {path}" for name, path in escaped.items())
            + f", outside the throwaway {ROOT / 'artifacts' / '_pytest'}. A test could change this machine's "
            "own records. Check tests/_state_env.py.")


_assert_db_isolated()

@pytest.fixture(autouse=True)
def _tests_never_launch_apps(monkeypatch, request):
    """A test that reached the real launcher started Spotify on the developer's machine and played
    a song, and an OPEN_APP test started Firefox; with no display they still spawn the process.
    Tests that need a launch stub the launcher they are about; a test of the launcher itself, which
    runs a harmless program, says so with the launches_apps marker."""
    if request.node.get_closest_marker("launches_apps"):
        return
    monkeypatch.setattr("eli.execution.media_runtime.open_spotify",
                        lambda: "Could not open Spotify: tests never launch it")
    monkeypatch.setattr("eli.execution.effectors.system_helpers.open_app_with_timeout",
                        lambda argv, timeout=30: {"ok": False, "error": "tests never launch apps", "argv": argv})
    monkeypatch.setattr("eli.system.portable_app_control._popen", lambda args: False)


@pytest.fixture(autouse=True)
def _no_repair_offer_from_an_earlier_test():
    """Each test starts with no install offer waiting and no failure on record. The launcher stub
    above makes OPEN_APP fail, and where the app is not installed (a CI runner) that failure left an
    offer waiting for five minutes: a later test's "yes", "no" or "get the news at 7am" was read as
    the answer to it, so those tests passed on a machine that has the apps and failed on the runner."""
    gr = sys.modules.get("eli.runtime.grounded_remediation")
    if gr is not None:
        gr.clear_pending()
        gr._LAST_FAILURE = None
        # Clearing it resolved the data folder before the test chose its own; forget that.
        from tests._state_env import _forget_resolved_paths
        _forget_resolved_paths()


@pytest.fixture
def workspace():
    """A throwaway folder inside the project, for tests of code that only writes inside it (FIX_FILE,
    the patch-set engine and self-repair refuse a path outside it): in this run's sandbox, which is
    in the checkout. It was the checkout's own artifacts/, written by every such test."""
    import shutil
    import tempfile
    SANDBOX.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="workspace_", dir=str(SANDBOX)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_user.sqlite3"
    yield db_path
    if db_path.exists():
        db_path.unlink()

@pytest.fixture
def memory_instance(temp_db):
    from eli.memory import Memory
    return Memory(db_path=temp_db)

@pytest.fixture
def quiet_gpu_sources(monkeypatch):
    """A machine whose every GPU source (nvidia-smi and the NVIDIA kernel driver, the Linux device
    scan, rocm-smi, DXGI, macOS's registry and system_profiler, llama.cpp's device list) reports
    nothing, with detection's cache cleared. A test then gives one source something to say."""
    import eli.core.hardware_profile as hp
    monkeypatch.setattr(hp, "nvidia_smi_path", lambda: None)
    monkeypatch.setattr(hp, "_nvidia_driver_loaded", lambda: False)
    monkeypatch.setattr(hp, "_linux_drm_gpus", lambda: [])
    monkeypatch.setattr(hp, "_windows_dxgi_adapters", lambda software=False: None)
    monkeypatch.setattr(hp, "_macos_gpus", lambda: [])
    monkeypatch.setattr(hp, "_macos_accelerators", lambda: None)
    monkeypatch.setattr(hp, "_llama_cpp_cards", lambda: None)
    monkeypatch.setattr(hp, "_lspci_name_for_pci_addr", lambda addr: "")
    monkeypatch.setattr(hp.shutil, "which", lambda name: None)
    monkeypatch.setattr(hp, "_DETECT_HW_CACHE", None)
    return hp

@pytest.fixture(scope="session")
def small_chat_model():
    """ELI_MODEL_PATH if set, else the smallest GGUF in models/ that carries a chat template (a chat
    model; projectors and embedders carry none, and a file's name is no guide). None when there is none."""
    import glob
    env = os.environ.get("ELI_MODEL_PATH")
    if env and os.path.exists(env):
        return env
    from eli.cognition.model_load_diagnostics import gguf_metadata
    found = sorted((os.path.getsize(g), g) for g in glob.glob(str(ROOT / "models" / "**" / "*.gguf"), recursive=True)
                   if (gguf_metadata(g) or {}).get("tokenizer.chat_template"))
    return found[0][1] if found else None


@pytest.fixture
def machine_gpu(monkeypatch):
    """This machine's own cards, for a test about them (the installed GPU pack): the run hides them."""
    if _MACHINE_CUDA_DEVICES is None:
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    else:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", _MACHINE_CUDA_DEVICES)


@pytest.fixture
def gpu_offload_runtime(monkeypatch):
    """A llama.cpp that can offload to a GPU but gives no device list to plan with, so sizing takes the
    fit path. Real llama.cpp answers for the machine running the tests (none on CI, the developer's
    card here), so a sizing test names the runtime it is about."""
    import eli.core.hardware_profile as hp
    from eli.core import load_plan
    monkeypatch.setattr(hp, "_llama_gpu_offload_available", lambda: True)
    monkeypatch.setattr(load_plan, "devices", lambda: [])
    return hp

# FIX: Force persistence gate to allow all memory writes in tests
@pytest.fixture(autouse=True)
def allow_all_persistence():
    with patch("eli.runtime.persistence_gate.should_store_memory_text", return_value=True), \
         patch("eli.runtime.persistence_gate.should_store_conversation_turn", return_value=True):
        yield

# Force persistence gate to allow all memory writes during tests
@pytest.fixture(autouse=True, scope="function")
def force_persistence_gate():
    with patch("eli.memory.memory._eli_should_store_memory_text", None), \
         patch("eli.memory.memory._eli_should_store_conversation_turn", None):
        yield


# Production code writes ELI_* variables straight into os.environ (save_settings pins ELI_MODEL_PATH, for
# one), which monkeypatch cannot see, so they outlived the test that caused them and skewed later ones.
@pytest.fixture(autouse=True, scope="function")
def restore_eli_environment():
    before = {k: v for k, v in os.environ.items() if k.startswith("ELI_")}
    yield
    for k in [k for k in os.environ if k.startswith("ELI_") and k not in before]:
        os.environ.pop(k, None)
    for k, v in before.items():
        if os.environ.get(k) != v:
            os.environ[k] = v
    # Path resolvers are lru_cached; a test that pointed them at a temp tree must not decide for the next one.
    try:
        from eli.core import paths as _paths
        for _fn in vars(_paths).values():
            if hasattr(_fn, "cache_clear"):
                _fn.cache_clear()
    except Exception:
        pass


# gguf_inference keeps the loaded model in module globals; a test that runs the real
# load_model() leaves a Mock there for the whole session. Reset it after every test.
@pytest.fixture(autouse=True, scope="function")
def reset_gguf_inference_globals():
    yield
    try:
        import eli.cognition.gguf_inference as _ggi
        _ggi._llm = None
        _ggi._live_runtime_override = None
        _ggi._load_failed = False
        _ggi._last_error = None
        if "_live_runtime_params" in vars(_ggi):
            _ggi._live_runtime_params = None
    except Exception:
        pass


# Whatever escapes the sandbox anyway (a path built by hand, a new store, a test that rewrites a
# document) is caught: the whole checkout is listed before the session and compared after it, and
# every file created, changed or deleted there, and every folder made there, is named, with the test
# that did it. It fails the run in CI and with ELI_TEST_STRICT_ISOLATION=1; otherwise it is only
# reported, since a copy of ELI running from this checkout (ELI's own RUN_TESTS) writes its state
# while the tests run. Only ELI's state folders (artifacts/, config/, models/ and the like) were
# watched, so a test that wrote the capability manifest at the checkout's root, or rewrote a
# blueprint, passed unseen. Not watched: the run's own sandbox, git's folder, the interpreter's
# bytecode, a Python environment kept in the checkout (a developer's .venv), and what the run was
# asked to write (--junitxml, --basetemp). The eval's own throwaway folder
# (tools/eval/eli_driver.new_sandbox) is a sandbox like this run's: what it holds is still watched.
_THROWAWAY_FOLDERS = (ROOT / "artifacts" / "_eval_sandbox",)
FOLDER = "folder"


def _own_state() -> dict:
    """Every file (its modification time and size) and folder in this checkout, but what is not watched."""
    seen = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        here = Path(dirpath)
        kept = []
        for name in dirnames:
            path = here / name
            if name in (".git", "__pycache__") or path == SANDBOX or (path / "pyvenv.cfg").is_file():
                continue
            kept.append(name)
            if path not in _THROWAWAY_FOLDERS:
                seen[path] = FOLDER
        dirnames[:] = kept
        for name in filenames:
            if name == ".git":                          # a worktree's pointer to its repository
                continue
            try:
                info = (here / name).lstat()
            except OSError:
                continue
            seen[here / name] = (info.st_mtime_ns, info.st_size)
    return seen


def _written_since(before: dict, asked=()) -> list:
    """What in this checkout was written, made or deleted since `before` (_own_state), each a path
    relative to the checkout, with the test that did it where the audit hook saw it. `asked`: paths
    the run was asked to write, with what is under them."""
    asked = tuple(Path(a).resolve() for a in ((asked,) if isinstance(asked, (str, Path)) else asked))
    now = _own_state()
    out = []
    for path in sorted(set(before) | set(now)):
        was, is_ = before.get(path), now.get(path)
        if was == is_:
            continue
        resolved = path.resolve()
        if any(resolved == a or a in resolved.parents for a in asked):
            continue
        rel = str(path.relative_to(ROOT)) + ("/" if FOLDER in (was, is_) else "")
        by = _WRITTEN_BY.get(os.path.realpath(path)) or _WRITTEN_BY.get(os.path.abspath(path))
        what = "deleted" if is_ is None else ("made" if FOLDER in (was, is_) else "written")
        out.append(f"{rel} ({what} by {by})" if by else (f"{rel} (deleted)" if is_ is None else rel))
    return out


# Which test first wrote, made, moved or deleted each path in this checkout outside the sandbox (an
# audit hook), so the report names it.
_WRITTEN_BY: dict = {}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_WATCHED = str(ROOT.resolve())


def _note_written(event, args) -> None:
    if event == "open":
        if len(args) < 3 or args[0] is None or isinstance(args[0], int):
            return
        mode, flags = args[1], args[2]
        if isinstance(mode, str):
            if not any(c in mode for c in "wax+"):
                return
        elif not (isinstance(flags, int) and flags & _WRITE_FLAGS):
            return
        targets = args[:1]
    elif event in ("os.mkdir", "os.remove", "os.rmdir", "shutil.rmtree"):
        targets = args[:1]
    elif event == "os.rename":                         # os.replace too
        targets = args[:2]
    else:
        return
    for target in targets:
        if target is None or isinstance(target, int):
            continue
        try:
            path = os.path.realpath(os.fsdecode(target))
        except (TypeError, ValueError):
            continue
        if not (path == _WATCHED or path.startswith(_WATCHED + os.sep)):
            continue
        sandbox = str(SANDBOX.resolve())
        if path == sandbox or path.startswith(sandbox + os.sep) or (os.sep + "__pycache__") in path:
            continue
        _WRITTEN_BY.setdefault(path, os.environ.get("PYTEST_CURRENT_TEST", "collection").split(" ")[0])


sys.addaudithook(_note_written)
_STATE_BEFORE = _own_state()


@pytest.fixture(autouse=True, scope="function")
def measurements_stay_on_this_machine(monkeypatch, tmp_path):
    """What ELI measured on this machine (GPU headroom, whisper's footprint) is never read or written
    by a test: a report test read this machine's 108 MB for Qwen3.6 once a real llama.cpp was loaded.
    Nor what one test measured for the next: a generation's decode speed is kept process-wide
    (model_tier), and a slow one recorded by an earlier test cut a later self-consistency test's
    samples from 3 to 2 in the full run, while the test passed on its own."""
    from eli.core import load_plan, model_tier
    from eli.perception import local_whisper_stt
    monkeypatch.setattr(load_plan, "_headroom_file", lambda: tmp_path / "gpu_headroom.json")
    monkeypatch.setattr(load_plan, "_watch", {})
    monkeypatch.setattr(local_whisper_stt, "_footprint_file", lambda: tmp_path / "whisper_gpu.json")
    monkeypatch.setattr(model_tier, "_speed_ema", 0.0)


# ── What the run wrote outside its sandbox ───────────────────────────────────
# (The results document RUN_TESTS, the scheduled audit and the test review read is written by
# tools/run_test_report.py, not here.)
def pytest_sessionfinish(session, exitstatus):
    asked = [p for p in (session.config.getoption("xmlpath", None),             # --junitxml
                         session.config.getoption("basetemp", None)) if p]
    escaped = _written_since(_STATE_BEFORE, asked)
    if escaped:
        print(f"\n[conftest] TEST ISOLATION BREACH: {len(escaped)} files or folders in this checkout were "
              "written, made or deleted during the run:", *escaped, sep="\n  ")
        if os.environ.get("CI") or os.environ.get("ELI_TEST_STRICT_ISOLATION"):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
