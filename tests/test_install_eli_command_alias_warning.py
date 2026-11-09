"""Live bug (2026-10-02): a stale `alias eli=...` in ~/.bashrc pointed at an old
portable snapshot. install_eli_command.sh's own `command -v` check never caught
it — aliases don't exist outside an interactive shell, so a script-context
`command -v` can't see them. The user's install LOOKED successful every time,
while the alias silently won in every real terminal.

Fixed: the installer now greps common shell rc files directly for a
conflicting `alias <name>=` and warns.
"""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_eli_command.sh"


def _run_installer(tmp_path, rc_content=None):
    home = tmp_path / "home"
    home.mkdir()
    if rc_content is not None:
        (home / ".bashrc").write_text(rc_content, encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["ELI_BIN_DIR"] = str(home / ".local" / "bin")
    return subprocess.run(
        ["bash", str(SCRIPT), "--force"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30,
    )


def test_warns_when_an_rc_alias_points_elsewhere(tmp_path):
    cp = _run_installer(tmp_path, rc_content="alias eli-v3='/some/old/stale/path/eli.sh'\n")
    assert "defines an 'alias eli-v3=' NOT pointing at" in cp.stderr
    assert "/some/old/stale/path/eli.sh" in cp.stderr


def test_no_warning_when_rc_has_no_alias(tmp_path):
    cp = _run_installer(tmp_path, rc_content="# nothing relevant here\n")
    assert "alias eli-v3=" not in cp.stderr


def test_no_warning_when_alias_already_points_at_the_fresh_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".local" / "bin" / "eli-v3"  # this version's own command name
    (home / ".bashrc").write_text(f"alias eli-v3='{target}'\n", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["ELI_BIN_DIR"] = str(home / ".local" / "bin")
    cp = subprocess.run(
        ["bash", str(SCRIPT), "--force"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    assert "alias eli-v3=" not in cp.stderr


# ── which names this version takes ─────────────────────────────────────────────
FIX = ROOT / "scripts" / "fix_eli_shell_env.sh"


def _home(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), ELI_BIN_DIR=str(home / ".local" / "bin"))
    return home, env


def test_the_shell_fix_installs_this_versions_commands_and_never_takes_another_products(tmp_path):
    """Run by install.sh --yes, it force-installed the generic `eli` over whichever ELI product
    owned it, and deleted every `export ELI_PROJECT_ROOT=` line, another version's included."""
    home, env = _home(tmp_path)
    bin_dir = home / ".local" / "bin"
    others = "#!/bin/sh\n# another ELI product's command\nexec /opt/another-eli/run \"$@\"\n"
    (bin_dir / "eli").write_text(others, encoding="utf-8")
    (home / ".bashrc").write_text("export ELI_PROJECT_ROOT=/opt/another-eli\n"
                                  f"export ELI_PROJECT_ROOT={ROOT}\n", encoding="utf-8")
    cp = subprocess.run(["bash", str(FIX), "--yes"], cwd=str(ROOT), env=env,
                        capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert (bin_dir / "eli").read_text(encoding="utf-8") == others
    for name in ("eli-v3", "eli3"):
        assert f'APP_ROOT="{ROOT}"' in (bin_dir / name).read_text(encoding="utf-8")
    rc = (home / ".bashrc").read_text(encoding="utf-8")
    assert "export ELI_PROJECT_ROOT=/opt/another-eli" in rc
    assert f"export ELI_PROJECT_ROOT={ROOT}\n" not in rc


def test_a_command_that_is_not_eli_v3s_is_replaced_only_with_force(tmp_path):
    home, env = _home(tmp_path)
    target = home / ".local" / "bin" / "eli-v3"
    target.write_text("#!/bin/sh\necho a different program\n", encoding="utf-8")
    run = lambda *a: subprocess.run(["bash", str(SCRIPT), *a], cwd=str(ROOT), env=env,  # noqa: E731
                                    capture_output=True, text=True, timeout=30)
    assert run().returncode == 1 and "a different program" in target.read_text(encoding="utf-8")
    assert run("--force").returncode == 0
    # ELI v3's own command, written before, is brought up to date without --force
    assert run().returncode == 0 and f'APP_ROOT="{ROOT}"' in target.read_text(encoding="utf-8")

