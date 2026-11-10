#!/usr/bin/env python3
"""capabilities_doc.py — regenerate blueprints/capabilities_and_actions.md.

Companion to capability_updater.py: that refreshes capability_manifest.json; this
renders the human-readable reference table (every routable action + a
representative activation phrase + Core/Plugin source) from that manifest plus a
curated description/phrase map below.

Driven off the manifest (no heavy executor import). Any routable action that is
NOT yet in the curated map is still emitted (under "New / undocumented") with a
"(needs activation phrase)" marker and reported in `needs_phrase`, so the doc can
never silently drift or go stale as actions are added — it just flags what a human
should write a phrase for. Wire-in: capability_updater calls
generate_capabilities_doc() after writing the manifest.

Paths are derived relative to this file (repo-root portable; no hardcoded home).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import textwrap
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from eli.core.paths import canonical_root
    ELI_ROOT = canonical_root(Path(__file__).resolve().parents[3])
except Exception:
    ELI_ROOT = Path(__file__).resolve().parents[3]
OUT = ELI_ROOT / "blueprints" / "capabilities_and_actions.md"


def out_path() -> Path:
    """Where an engine start writes the table: ELI_CAPABILITIES_DOC, else this install's runtime state
    (paths.data_dir()). The tracked blueprint is written only when it is asked for (running this module,
    `out=OUT`): every engine start rewrote it, so a test or an eval run from a checkout did too."""
    named = os.environ.get("ELI_CAPABILITIES_DOC", "").strip()
    if named:
        return Path(named).expanduser()
    from eli.core.paths import data_dir
    return data_dir() / "runtime" / "capabilities_and_actions.md"


_DATE_LINE = re.compile(r"^(\*Auto-generated )\d{4}-\d{2}-\d{2}( from)", re.M)


def _same_but_for_the_date(path: Path, text: str) -> bool:
    """A new date over an unchanged table is not an update: written anyway, the tracked file was
    changed in git by every start (the manifest beside it follows the same rule)."""
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _DATE_LINE.sub(r"\1\2", old) == _DATE_LINE.sub(r"\1\2", text)

# action -> (category, description, [phrases]): the catalogue, kept by the action registry
# (eli/execution/effectors/registry.py, CATALOGUE). An action is described once: here, or where its
# effector registers it (register(..., about=...)). Each line below also says, in keywords, how the
# action's result is shown (shown="always": the reply in every reasoning mode, the reports, receipts
# and raw content a model must not retell; shown="quick": in Quick, and worded in the other modes),
# whether it is returned straight from the executor (direct), whether the model's guess at an intent
# may pick it (offered=False: a yes, a no or an internal step), whether only the user's own words
# may run it (explicit: it renames the user, changes ELI or erases data) and whether an MCP client
# gets it without the control opt-in (open: it only reads, nothing of the user's). Those lists were
# kept by hand in six places and are read from these declarations now.
from eli.execution.effectors import registry as _registry

_C: "OrderedDict[str, Tuple[str, str, List[str]]]" = _registry.CATALOGUE


def _add(cat: str, action: str, desc: str, *phrases: str, **facets) -> None:
    _registry.declare(action, about=(cat, desc, phrases), **facets)


def action_words(action: str, *, with_phrases: bool = True) -> str:
    """What the catalogue says one action is about, as text: its name, what it does and, with
    `with_phrases`, how it is asked for. The example phrases name particular things ("launch
    firefox"); leave them out to keep only what the action is about in general. "" for an action
    the catalogue does not describe."""
    entry = _C.get(str(action or "").upper())
    if not entry:
        return ""
    _cat, desc, phrases = entry
    said = list(phrases or []) if with_phrases else []
    return " ".join([str(action).upper().replace("_", " "), desc or "", *said]).lower()


def catalogue() -> List[dict]:
    """Structured command catalogue grouped by category — for the web UI / API.

    Built from the same `_C` table that renders the markdown reference, so the
    on-screen command list can never drift from the documented capability set.
    """
    by_cat: "OrderedDict[str, List[dict]]" = OrderedDict()
    for action, (cat, desc, phrases) in _C.items():
        by_cat.setdefault(cat, []).append(
            {"action": action, "description": desc, "phrases": list(phrases)})
    return [{"category": c, "actions": acts} for c, acts in by_cat.items()]


# ── Conversation & persona ──────────────────────────────────────────────────
_add("Conversation & persona", "CHAT", "Open conversation / fallback; persona-bound reply", "anything not matching a command", "“how are you”", "“tell me a story”", offered=False)
_add("Conversation & persona", "EXPLAIN_LAST_RESPONSE", "Explain why ELI gave its previous answer", "“why did you say that”", "“explain your last response”", shown="quick", direct=True)
_add("Conversation & persona", "CLEAR_CHAT_HISTORY", "Clear the current chat history", "“clear chat history”", "“start a new chat”", shown="quick", explicit=True)
_add("Conversation & persona", "PERSONA_LOCK_SET", "Pin a persona/role for the session", "“lock your persona to X”", shown="quick", explicit=True)
_add("Conversation & persona", "PERSONA_LOCK_CLEAR", "Release a pinned persona", "“clear persona lock”", shown="quick", explicit=True)
_add("Conversation & persona", "PERSONA_LOCK_STATUS", "Show the current persona lock", "“what persona are you locked to”", shown="quick", open=True)
_add("Conversation & persona", "PERSONA_REFRESH", "Refresh/reload ELI's persona from the latest profile", "“refresh your persona”", "“reload your persona”", shown="quick", explicit=True)
_add("Conversation & persona", "SET_COMMUNICATION_STYLE", "Persist how you want ELI to speak to you (empty or a reset phrase clears it)", "“speak concisely from now on”", shown="quick", explicit=True)
_add("Conversation & persona", "HELP", "List what ELI can do", "“help”", "“what commands do you have”", shown="quick", open=True)
_add("Conversation & persona", "LIST_CAPABILITIES", "Enumerate ELI's capabilities", "“what can you do”", "“list your capabilities”", shown="quick", direct=True, open=True)

# ── App & window control ────────────────────────────────────────────────────
_add("App & window control", "OPEN_APP", "Launch an application (installs it if missing)", "“open spotify”", "“launch firefox”", shown="quick", direct=True)
_add("App & window control", "CLOSE_APP", "Close an application", "“close spotify”", "“quit chrome”", shown="quick")
_add("App & window control", "FOCUS_APP", "Bring an app's window to the front", "“focus firefox”", "“switch to the browser”", shown="quick")
_add("App & window control", "OPEN_BROWSER", "Open the default web browser", "“open the browser”", shown="quick", direct=True)
_add("App & window control", "OPEN_URL", "Open a URL in the browser", "“open github.com”", "“go to youtube.com”", shown="quick", direct=True)
_add("App & window control", "OPEN_IDE", "Open the code IDE", "“open the IDE”", shown="quick", direct=True)
_add("App & window control", "OPEN_FILE_SYSTEM", "Open the file manager", "“open files”", "“open the file manager”", shown="quick", direct=True)
_add("App & window control", "OPEN_SYSTEM_SETTINGS", "Open OS settings", "“open system settings”", shown="quick", direct=True)
_add("App & window control", "OPEN_AUDIO_SETTINGS", "Open sound settings", "“open audio settings”", shown="quick", direct=True)
_add("App & window control", "OPEN_POWER_SETTINGS", "Open power settings", "“open power settings”", shown="quick", direct=True)
_add("App & window control", "OPEN_NETWORK_BROWSER", "Open network settings", "“open network settings”", shown="quick", direct=True)
_add("App & window control", "OPEN_COMMUNICATION_HUB", "Open the comms hub (mail/chat apps)", "“open communication hub”", shown="quick", direct=True)
_add("App & window control", "OPEN_MEDIA_HUB", "Open the media hub", "“open media hub”", shown="quick", direct=True)
_add("App & window control", "TILE_WINDOWS", "Tile windows in a grid", "“tile windows”", "“2x2 grid”", "“4x2”", shown="quick", direct=True)
_add("App & window control", "MAXIMISE_WINDOW", "Maximise a named app's window (args: name), or the focused one when none is named", "“maximise firefox”", "“maximise the window”", shown="quick")
_add("App & window control", "MINIMISE_ALL", "Minimise all / show desktop", "“minimise everything”", "“show desktop”", shown="quick")
_add("App & window control", "MINIMISE_WINDOW", "Minimise the focused window", "“minimise the window”", "“minimise this”", shown="quick")
_add("App & window control", "MINIMIZE_WINDOW", "Minimise the focused window (US spelling)", "“minimize this window”", shown="quick")
_add("App & window control", "MINIMISE_APP", "Minimise a specific app's window", "“minimise spotify”", shown="quick")
_add("App & window control", "MINIMIZE_APP", "Minimise a specific app's window (US spelling)", "“minimize chrome”", shown="quick")
_add("App & window control", "HIDE_APP", "Hide an application's window", "“hide spotify”", "“hide the window”")
_add("App & window control", "NEXT_WINDOW", "Switch to the next window", "“next window”", shown="quick")
_add("App & window control", "PREVIOUS_WINDOW", "Switch to the previous window", "“previous window”", shown="quick")
_add("App & window control", "RESTORE_WINDOWS", "Restore minimised windows", "“restore windows”", shown="quick")
_add("App & window control", "SWITCH_WORKSPACE", "Switch virtual desktop/workspace", "“switch to workspace 2”", shown="quick")
_add("App & window control", "SMART_HOME", "Control smart-home devices", "“turn off the lights”", shown="quick")
_add("App & window control", "CONFIRM_PENDING_REMEDIATION", "Approve a pending fix/install offer", "“yes” (to an “install X?” offer)", shown="quick", direct=True, offered=False)
_add("App & window control", "CANCEL_PENDING_REMEDIATION", "Decline a pending fix/install offer", "“no” (to an “install X?” offer)", shown="quick", direct=True, offered=False)

# ── Input control ───────────────────────────────────────────────────────────
_add("Input control", "VOLUME", "Adjust, mute or read the system volume", "“volume up”", "“mute”",
     "“set volume to 30”", "“what's the volume?”", shown="quick", direct=True)
_add("Input control", "KEYBOARD", "Send keystrokes / type text", "“press enter”", "“type hello world”", shown="quick", direct=True)
_add("Input control", "MOUSE_CONTROL", "Move/click the mouse", "“left click”", "“move the mouse up”", shown="quick", direct=True)

# ── Media ───────────────────────────────────────────────────────────────────
_add("Media", "PLAY_MEDIA", "Play a track on the named platform", "“play Juicy by Notorious B.I.G. on Spotify”", "“play lo-fi on YouTube”", shown="quick", direct=True)
_add("Media", "PAUSE_MEDIA", "Pause playback", "“pause”", "“pause the music”", shown="quick", direct=True)
_add("Media", "STOP_MEDIA", "Stop playback", "“stop the music”", shown="quick", direct=True)
_add("Media", "NOW_PLAYING", "Report the current track and where it's playing (Spotify or headless-mpv YouTube audio)", "“what's playing”", "“what song is this”", shown="quick", direct=True)
_add("Media", "NEXT_MEDIA", "Next track", "“next song”", "“skip”", shown="quick", direct=True)
_add("Media", "PREVIOUS_MEDIA", "Previous track", "“previous track”", "“go back a song”", shown="quick", direct=True)
_add("Media", "REPEAT_MEDIA", "Repeat current track", "“repeat this song”", shown="quick")
_add("Media", "SHUFFLE_MEDIA", "Toggle shuffle", "“shuffle”", shown="quick")
_add("Media", "MEDIA_CONTROL", "Generic transport control (MPRIS)", "“play/pause”", shown="quick", direct=True)
_add("Media", "SKIP_YOUTUBE_AD", "Skip a YouTube ad when skippable", "“skip the ad”", shown="quick")

# ── Vision & screen ─────────────────────────────────────────────────────────
_add("Vision & screen", "SCREEN_READ_ANALYZE", "Screenshot → vision model + OCR; answers questions about on-screen content", "“what's on my screen”", "“can you see this”", "“what season is on the screen”", shown="quick", direct=True)
_add("Vision & screen", "SCREEN_LOCATE", "Find on-screen UI text/element", "“find the submit button on screen”", shown="quick", direct=True)
_add("Vision & screen", "SCREENSHOT", "Capture a screenshot", "“take a screenshot”", shown="quick", direct=True)
_add("Vision & screen", "OCR_IMAGE", "Extract text from an image file", "“ocr photo.png”", "“read the text in image.jpg”", shown="quick")
_add("Vision & screen", "ANALYZE_IMAGE", "Describe/analyse an image file (VLM)", "“describe cat.jpg”", "“analyse this image”", shown="quick")
_add("Vision & screen", "ANALYZE_PDF", "Read/summarise a PDF", "“summarise report.pdf”", "“analyse this pdf”", shown="quick")
_add("Vision & screen", "ANALYZE_PDF_FOLDER", "Analyse all PDFs in a folder", "“analyse the pdfs in that directory”", shown="quick")
_add("Vision & screen", "ANALYZE_CSV", "Analyse a CSV/spreadsheet", "“analyse data.csv”", shown="quick")
_add("Vision & screen", "AMBIENT_VISION", "Toggle continuous screen-watching", "“watch my screen”", "“stop watching”", shown="quick")

# ── Gaze (eye-tracking) ─────────────────────────────────────────────────────
_add("Gaze (eye-tracking)", "GAZE_ENABLE", "Enable webcam gaze control", "“enable gaze control”", shown="quick", explicit=True)
_add("Gaze (eye-tracking)", "GAZE_DISABLE", "Disable gaze control", "“disable gaze control”", shown="quick", explicit=True)
_add("Gaze (eye-tracking)", "GAZE_CALIBRATE", "Calibrate gaze tracking", "“calibrate gaze”", shown="quick", explicit=True)
_add("Gaze (eye-tracking)", "GAZE_STATUS", "Show gaze tracker status", "“gaze status”", shown="quick")
_add("Gaze (eye-tracking)", "GAZE_CLICK", "Click where the eyes rest", "“open” / “left click” / “hit enter” (while gaze is on)", shown="quick", explicit=True)

# ── Voice ───────────────────────────────────────────────────────────────────
_add("Voice", "DICTATE", "Start/stop dictation into the active field", "“start dictation”", "“stop dictating”", shown="quick", direct=True)
_add("Voice", "TRANSCRIBE", "Transcribe an audio file", "“transcribe audio.wav”", shown="quick", direct=True)
_add("Voice", "SPEAK", "Speak text aloud (TTS)", "“say good morning”", shown="quick", direct=True)
_add("Voice", "LIST_VOICES", "List installed voices and what's downloadable (166 across 45 languages)", "“what voices do you have”", "“list voices”", "“which accents can I get”", shown="quick")
_add("Voice", "DOWNLOAD_VOICE", "Download a voice/accent from the Piper library and switch to it", "“download a British voice”", "“get me a Scottish accent”", "“install en_US-amy-medium”", shown="quick")
_add("Voice", "SET_VOICE", "Switch the active TTS voice/accent (distinct from persona tone)", "“use the alan voice”", "“switch your voice to amy”", "“use the robotic style voice”", "“use the natural voice”", shown="quick", explicit=True)
_add("Voice", "CREATE_VOICE", "Make a new ELI voice from a recording (wav/mp3/mp4 drop-in; XTTS-v2 clone)", "“create a voice called Nova from clip.mp4”", "“make an ELI voice from recording.wav”", shown="quick")
_add("Conversation & persona", "SET_TONE", "Shade ELI's expressed tone/emotion (words+voice+face) without changing its core personality", "“be comedic”", "“talk street”", "“sound more professional”", "“use a deadpan tone”", shown="quick", explicit=True)
_add("Conversation & persona", "CLEAR_TONE", "Drop a pinned tone — back to autonomous, situation-adaptive delivery", "“go back to normal”", "“be yourself”", "“drop the tone”", shown="quick", explicit=True)
_add("Voice", "LISTEN_FOR_COMMAND", "Start listening for a voice command", "“listen” / the wake word “computer”", shown="quick")

# ── Files & notes ───────────────────────────────────────────────────────────
_add("Files & notes", "CREATE_FILE", "Create a file", "“create a file notes.txt”", shown="quick")
_add("Files & notes", "CREATE_FOLDER", "Create a folder", "“make a folder called projects”", shown="quick", direct=True)
_add("Files & notes", "READ_FILE", "Read a file's contents", "“read notes.txt”", shown="quick", direct=True)
_add("Files & notes", "LIST_DIR", "List a directory", "“list the files in ~/Documents”", shown="quick", direct=True)
_add("Files & notes", "FILE_AUDIT", "Audit files for issues", "“audit my files”", shown="quick")
_add("Files & notes", "SUMMARIZE_FILE", "Summarise a document/file", "“summarise report.docx”", shown="quick")
_add("Files & notes", "CONVERT_DOCUMENT", "Convert between document formats", "“convert report.md to pdf”", shown="quick")
_add("Files & notes", "WRITE_NOTE", "Write a quick note", "“write a note saying buy milk”", shown="quick", direct=True)
_add("Files & notes", "NEW_NOTE", "Create a new note", "“new note”", shown="quick")
_add("Files & notes", "LIST_NOTES", "List saved notes", "“list my notes”", shown="quick")
_add("Files & notes", "SEARCH_NOTES", "Search notes", "“search notes for budget”", shown="quick")
_add("Files & notes", "GET_CLIPBOARD", "Read the clipboard", "“what's in my clipboard”", shown="quick", direct=True)
_add("Files & notes", "SET_CLIPBOARD", "Write to the clipboard", "“copy this to the clipboard”", shown="quick", direct=True)

# ── Generation (docs/code) ──────────────────────────────────────────────────
_add("Generation (docs/code)", "GENERATE_DOCUMENT", "Grounded document via the multi-stage pipeline (evidence → plan/outline → sections → review→revise)", "“generate a document about X”", "“write a report on Y”", shown="quick")
_add("Generation (docs/code)", "CREATE_DOCUMENT", "Multi-stage grounded document (plan→draft→review); single-pass fallback", "“create a document on X”", shown="quick")
_add("Generation (docs/code)", "DATA_FABRICATOR", "Generate synthetic/test data", "“fabricate a test dataset for X”", shown="quick")
_add("Generation (docs/code)", "GENERATE_SCRIPT", "Generate a runnable script via the coding agent", "“write a bash script to monitor the GPU”")
_add("Generation (docs/code)", "GENERATE_PROJECT", "Scaffold a multi-file project", "“generate a project that does X”", shown="quick")
_add("Generation (docs/code)", "CODE_SOLVE", "Solve a coding task (plan→DAG→verify→repair)", "“solve this: …”", "“implement function X”")
_add("Generation (docs/code)", "FIX_FILE", "Find and fix bugs in a file", "“fix the bugs in foo.py”")
_add("Generation (docs/code)", "EXAMINE_CODE", "Tiered error scan of named files/codebase", "“examine eli/memory/memory.py for errors”", "“review the codebase for bugs”", shown="always", direct=True)
_add("Generation (docs/code)", "CONFIRM_CODE_FIX", "Apply the offered code fixes (pending-confirm)", "“yes, fix it”", "“fix the logic ones too”", shown="always", direct=True, offered=False)
_add("Generation (docs/code)", "CANCEL_CODE_FIX", "Decline the offered code fixes", "“no, leave it”", shown="always", direct=True, offered=False)
_add("Generation (docs/code)", "SHOW_DIFF", "Show a code diff", "“show the diff”")
_add("Generation (docs/code)", "OPEN_IN_IDE", "Open a generated artifact in the IDE", "(post-action; auto after generate)", shown="quick", direct=True)

# ── System status & info ────────────────────────────────────────────────────
_add("System status & info", "CPU_USAGE", "Current CPU usage", "“cpu usage”", shown="quick", direct=True, open=True)
_add("System status & info", "RAM_USAGE", "Current memory usage", "“how much ram is free”", shown="quick", direct=True, open=True)
_add("System status & info", "GPU_STATUS", "GPU/VRAM status", "“gpu status”", shown="quick", direct=True, open=True)
_add("System status & info", "SYSTEM_STATS", "Combined system stats", "“system stats”", shown="quick", direct=True, open=True)
_add("System status & info", "HARDWARE_PROFILE", "Detected hardware profile", "“show hardware profile”", shown="quick")
_add("System status & info", "GET_STATUS", "General status", "“status”")
_add("System status & info", "TIME", "Current time", "“what time is it”", shown="quick", direct=True, open=True)
_add("System status & info", "GET_TIME", "Current time (alt)", "“tell me the time”", shown="quick", direct=True, open=True)
_add("System status & info", "DATE", "Current date", "“what's the date”", shown="quick", direct=True, open=True)
_add("System status & info", "GET_DATE", "Current date (alt)", "“what day is it”", shown="quick", direct=True, open=True)
_add("System status & info", "GET_WEATHER", "Weather for a place", "“weather in Paris”", shown="quick", direct=True)
_add("System status & info", "NEWS_FETCH", "Synthesised news digest of the latest stories and headlines; topic-scoped re-fetch of a story after a briefing when you go deeper", "“what's the news”", "“catch me up”", "“go deeper into Hubble”", "“more on the transformer story”", shown="always", direct=True)
_add("System status & info", "WEB_SEARCH", "Web search (Net-gated)", "“search the web for X”", direct=True)
_add("System status & info", "VERSION_CHANGES", "Which version ELI is, when it was first seen here and the versions before it; no invented diffs", "“notice any updates to your code?”", "“what version are you”", "“check the diff pre and post install”", shown="quick", direct=True, open=True)
_add("ELI's world", "IMPORT_MODEL", "Bring a 3D model file on this computer (.gltf/.glb) into ELI's rooms", "“import the model at ~/things/lamp.glb”", shown="quick", direct=True)
_add("ELI's world", "FETCH_MODEL", "Fetch a CC0 model of a thing from Poly Haven for ELI's rooms (Net-gated)", "“fetch a model of a pine tree”", "“get a rocking chair model”", shown="quick", direct=True)
_add("ELI's world", "PLAY_GAME", "Make up a small game to play in ELI VR (targets, catch, sort, order, hide) where you stand, from the room, ELI's memories and how you did last time; the score is journalled and remembered", "“let's play a game in VR”", "“set up a catch game”", "“challenge me”", shown="quick", direct=True)
_add("ELI's world", "STOP_GAME", "Stop the game ELI set", "“stop the game”", "“I give up”", shown="quick", direct=True)
_add("System status & info", "WEB_FETCH", "Read a linked web page and answer about it (Net-gated)", "“https://example.com/post what do you think?”", "“summarise this https://example.com/article”", direct=True)
_add("System status & info", "MESSAGE_TIME_QUERY", "Time-of-a-message query", "“when did I say that”", shown="quick")
_add("System status & info", "CHECK_CHRONAL_ALIGNMENT", "Playful date/time sanity check", "“check chronal alignment”", offered=False)

# ── Grounded introspection ──────────────────────────────────────────────────
_add("Grounded introspection", "RUNTIME_STATUS", "Live runtime: model, ctx, GPU layers, batch", "“what are you running on”", "“runtime status — everything”", shown="quick", direct=True, open=True)
_add("Grounded introspection", "RUNTIME_AUDIT", "Full runtime audit (what's broken/missing)", "“run a full runtime audit”", shown="quick", direct=True)
_add("Grounded introspection", "IMPORT_AUDIT", "Which imports are failing/missing", "“what imports are failing”", shown="quick", direct=True)
_add("Grounded introspection", "RESOLVE_RUNTIME_PATHS", "Resolved paths of critical files", "“show the resolved runtime paths”", shown="always", direct=True)
_add("Grounded introspection", "GUI_RUNTIME_AUDIT", "Audit GUI wiring with file-read proof", "“audit your gui file”", "“scan the gui runtime wiring and prove every hook”", shown="quick", direct=True)
_add("Grounded introspection", "DIAGNOSE_WRAPPERS", "Show the executor middleware/wrapper chain", "“show the executor wrapper chain”", shown="quick", offered=False)
_add("Grounded introspection", "COGNITION_STATUS", "Cognition runtime status", "“cognition status”", shown="quick", direct=True, open=True)
_add("Grounded introspection", "EXPLAIN_COGNITION_RUNTIME", "Explain the cognition runtime verbatim", "“explain your cognition runtime”", shown="quick", direct=True, open=True)
_add("Grounded introspection", "EXPLAIN_GGUF_DIAGNOSTICS", "Live GGUF load/inference compatibility report (runtime version, preflight, last load error)", "“gguf errors”", "“why won't nemotron load”", "“empty response from the model”", shown="quick", direct=True)
_add("Grounded introspection", "EXPLAIN_FAILURE_LOG", "Grounded executor failure log from agent.sqlite3 (not GGUF console lines)", "“what are the failures”", "“show the failure log”", "“what's tripping you up”", shown="quick", direct=True)
_add("Grounded introspection", "EXPLAIN_LAST_FAILURE", "Explain the most recent grounded or executor failure", "“what was the last failure”", "“why did that fail”", shown="quick", direct=True)
_add("Grounded introspection", "MEMORY_STATUS", "Memory subsystem status / counts", "“how does your memory work”", shown="quick", direct=True)
_add("Grounded introspection", "MEMORY_STATS", "Memory statistics", "“memory stats”", open=True)
_add("Grounded introspection", "EXPLAIN_MEMORY_RUNTIME", "Explain the memory runtime verbatim (4 DBs)", "“explain exactly how your memory works internally”", shown="quick", direct=True)
_add("Grounded introspection", "AWARENESS_STATUS", "What ELI is currently aware of", "“what are you aware of”", shown="quick")
_add("Grounded introspection", "FRONTIER_STATUS", "Full system wiring matrix", "“run a full system wiring matrix”", shown="quick")
_add("Grounded introspection", "ELI_IDENTITY_AUDIT", "Audit ELI's identity provenance", "“audit your identity”", shown="quick")
_add("Grounded introspection", "CODE_CHANGES", "Recent code changes", "“what code has changed”", shown="quick")
_add("Grounded introspection", "NAME_SOURCE_AUDIT", "Where ELI's knowledge of your name comes from", "“how do you know my name”", shown="quick", direct=True)
_add("Grounded introspection", "REASONING_MODE_STATUS", "Current reasoning mode", "“what reasoning mode are you in”", shown="quick", direct=True, open=True)
_add("Grounded introspection", "EXPLAIN_ALL_REASONING_MODES", "Describe all 5 reasoning modes", "“explain all your reasoning modes”", shown="quick", open=True)
# The documented phrase must be one that actually reaches SELF_REPORT. A bare
# "what have you been working on" no longer does, deliberately: it is small talk
# as often as it is a maintenance question, and answering conversation with a
# git/runtime evidence dump is the worse failure. Naming updates keeps it
# unambiguous — the claims suite fails if this phrase stops routing here.
_add("Grounded introspection", "SELF_REPORT", "What ELI has recently done/worked on (code, commits, runtime)", "“what updates have you made recently”", shown="quick", direct=True)

# ── Memory & profile ────────────────────────────────────────────────────────
_add("Memory & profile", "MEMORY_STORE", "Store a durable fact", "“remember that my sister's name is Anna”", shown="quick")
_add("Memory & profile", "MEMORY_RECALL", "Recall stored facts", "“what do you remember about my car”", shown="quick")
_add("Memory & profile", "MEMORY_FORGET", "Delete matching memories and everything derived from them, after confirmation", "“forget that my locker is 212”", shown="always", explicit=True)
_add("Memory & profile", "PERSONAL_MEMORY_SUMMARY", "Summary of what ELI knows about you", "“what do you know about me”", shown="quick", direct=True)
_add("Memory & profile", "PERSONAL_MEMORY_DEEP_EXPLAIN", "Deep, sourced profile explanation", "“explain everything you know about me and where it's stored”", shown="quick", direct=True)
_add("Memory & profile", "USER_IDENTITY_SUMMARY", "Who the user is", "“who am I”", shown="quick", direct=True)
_add("Memory & profile", "REFRESH_USER_INFO", "Re-extract the user profile", "“refresh my user info”", shown="quick")
_add("Memory & profile", "SET_USER_NAME", "Set/correct the user's name", "“my name is Alex”", "“call me Alex”", shown="quick", explicit=True)

# ── Self-maintenance ────────────────────────────────────────────────────────
_add("Self-maintenance", "SELF_ANALYZE", "Analyse own failures/metrics", "“analyse your failures”", shown="always", direct=True)
_add("Self-maintenance", "SELF_IMPROVE", "Run a self-improvement patch cycle", "“improve yourself”", shown="always", direct=True)
_add("Self-maintenance", "SELF_PATCH", "Propose fixes to ELI's own code, applied only on your yes", "“patch yourself”", shown="always", direct=True, explicit=True)
_add("Self-maintenance", "APPROVE_CANDIDATE", "Approve or decline a proposed fix to ELI's own code", "“approve candidate 3”",
     "“decline candidate 3”", shown="always", direct=True, offered=False)
_add("Self-maintenance", "SELF_REPAIR_PLAYBOOK", "Grounded repair decision guide (self help)", "“self help”", "“repair playbook”", "“maintenance playbook”", shown="quick", direct=True)
_add("Self-maintenance", "AUTOPILOT_DEBUG", "Turn a failure into a plan: root cause, affected files from git history, rollback, patch and validation", "“debug this error: ValueError x in eli/core/paths.py”", "“autopilot debug this traceback”", shown="quick")
_add("Self-maintenance", "CODEBASE_GRAPH", "Import graph of ELI's own source: what depends on what", "“show the codebase graph”", shown="quick")
_add("Self-maintenance", "TIMESTAMP_DIAG", "Run real clock and timestamp diagnostics instead of guessing", "“check the timestamps”", "“investigate the timestamp problem”", shown="quick")
_add("Voice", "DESIGN_VOICE", "Build a custom style voice from a base voice with pitch, speed and effects", "“design a voice called Nova, deep and slow”", "“make a custom voice named Nova”", shown="quick")
_add("Conversation & persona", "SET_AI_MODE", "Set the reasoning mode (Quick, Normal, Advanced, Research, Expert); reached through the intent resolver or the mode selector", "(no dedicated phrase)", explicit=True)
_add("Memory & profile", "USER_INFO_REPORT", "Full report of the living user profile; reached through the intent resolver", "(no dedicated phrase)")
_add("Vision & screen", "IMAGE_STATUS", "Status of the latest image job", "“what is the latest image status”")
_add("Generation (docs/code)", "DOC_GENERATE", "Alias of GENERATE_DOCUMENT", "“generate a document about tides”", shown="quick")
_add("Plugins", "PLUGIN_STATUS", "Status of one plugin; reached through the intent resolver (“list plugins” lists them all)", "(no dedicated phrase)", shown="quick")
_add("Plugins", "MCP_LIST", "List configured MCP servers", "“list mcp servers”", shown="quick")
_add("Plugins", "MCP_STATUS", "Status of MCP servers and their tools", "“mcp status”", shown="quick")
_add("Plugins", "MCP_TOOLS", "List the tools MCP servers expose", "“list mcp tools”", shown="quick")
_add("Plugins", "MCP_CALL", "Call a tool on an MCP server (the server runs outside ELI's network guard)", "“call the mcp tool read_file”", shown="quick", explicit=True)
_add("Plugins", "MCP_ADD", "Add an MCP server (installed switched off until you enable it)", "“add an mcp server”", shown="quick", explicit=True)
_add("Plugins", "MCP_REMOVE", "Remove an MCP server", "“remove the mcp server github”", shown="quick", explicit=True)
_add("Plugins", "MCP_DOCTOR", "Check MCP servers: launch preflight and handshake", "“mcp doctor”", "“check my mcp servers”", shown="quick")
_add("Self-maintenance", "SELF_IMPROVEMENT_LOG", "Show the self-improvement log", "“show your self-improvement log”", shown="always", direct=True)
_add("Self-maintenance", "SELF_UPGRADE", "git pull → deps → rebuild indexes", "“upgrade yourself”", shown="always", direct=True, explicit=True)
_add("Self-maintenance", "SELF_UPDATE", "Self-update (control-contract)", "“update yourself”", shown="always", direct=True, explicit=True)
_add("Self-maintenance", "SELF_TEST", "Run internal self-tests", "“run a self test”", shown="always", direct=True)
_add("Self-maintenance", "RUN_TESTS", "Run the pytest suite and summarise the results document", "“run the test suite”", "“generate a test report”")
_add("Self-maintenance", "GENERATE_TESTS", "ELI writes + sandbox-verifies behavioural tests for its own functions (Phase 4)", "“generate tests for your code”", "“grow your test coverage”", shown="quick")
_add("Self-maintenance", "LORA_STATUS", "Report LoRA fine-tune readiness (preflight: modules, base model, reviewed data)", "“lora status”", "“is lora ready”", shown="quick", open=True)
_add("Self-maintenance", "LORA_TRAIN", "Run the LoRA training pipeline DAG (preflight→build→train→eval); dry-run from chat, real training via the overnight task", "“train a lora”", "“fine-tune yourself”", shown="quick", explicit=True)
_add("Self-maintenance", "ORCHESTRATION_STATUS", "Explain the agent DAG orchestrator: execution layers, dependencies, critical path, and last run", "“orchestration status”", "“show your agent dag”", shown="quick", open=True)
_add("Self-maintenance", "TEST_REVIEW", "Run the suite, back up the report + write an errors file, summarise results, and offer result-driven fix options", "“test review”", "“run the tests and tell me what to fix”", shown="quick")
_add("Voice", "WAKE_SET", "Set your own wake word (any phrase); ELI persists it and trains the detector on it", "“change the wake word to athena”", "“set my wake word to atlas”", shown="quick", explicit=True)
_add("Voice", "WAKE_TRAIN", "Train the local, self-supervised wake-word model (Piper-synth + noise/music augmentation; robust over background music)", "“train the wake word”", shown="quick", explicit=True)
_add("Voice", "WAKE_ENROLL", "Record your voice saying the wake word and retrain (personalisation, over music)", "“enroll my wake word”", shown="quick", explicit=True)
_add("Voice", "TRAIN_VOICE", "Learn your voice + tone — pitch/energy/rate, happy/angry/excited, and question-vs-statement; ELI then adapts its delivery to how you sound", "“train my voice”", "“learn how I speak”", shown="quick", explicit=True)
_add("Voice", "STT_DIAGNOSTICS", "Mic/STT/TTS diagnostics (Whisper, wake word, ducking, Piper)", "“run voice diagnostics”", "“voice diagnostics”", "“check the microphone”", "“stt diagnostics”", shown="quick")
_add("Conversation & persona", "MULTI_COMMAND", "Run several chained commands from one utterance, in order", "“close steam and set an alarm for 7am”", "“open spotify then play X”", shown="always")

# ── Tasks, time & planning ──────────────────────────────────────────────────
_add("Tasks, time & planning", "SCHEDULE_TASK", "Schedule overnight/timed work (code/research/etc.)", "“research X overnight”", "“build Y at 2am”", shown="quick")
_add("Tasks, time & planning", "SET_ALARM", "Set an alarm", "“set an alarm for 7am”", shown="always", direct=True)
_add("Tasks, time & planning", "SET_TIMER", "Set a timer", "“set a timer for 10 minutes”", shown="always", direct=True)
_add("Tasks, time & planning", "ADD_EVENT", "Add a calendar event", "“add an event tomorrow at 3pm”", shown="always")
_add("Tasks, time & planning", "LIST_EVENTS", "List events", "“list my events”", shown="always")
_add("Tasks, time & planning", "REMOVE_EVENT", "Remove a calendar event or reminder", "“cancel the dentist appointment”", shown="always", explicit=True)
_add("Tasks, time & planning", "POMODORO_START", "Start a pomodoro", "“start a pomodoro”", shown="quick")
_add("Tasks, time & planning", "POMODORO_STOP", "Stop the pomodoro", "“stop the pomodoro”", shown="quick")
_add("Tasks, time & planning", "POMODORO_STATUS", "Pomodoro status", "“pomodoro status”", shown="quick")
_add("Tasks, time & planning", "BACKGROUND_JOBS", "List background/scheduled jobs", "“show background jobs”", shown="quick")
_add("Tasks, time & planning", "CHECK_JOB", "Check a specific job", "“check job 5”", shown="quick")
_add("Tasks, time & planning", "HABIT_STATUS", "Show learned habits", "“show my habits”", shown="quick", direct=True)
_add("Tasks, time & planning", "CONFIRM_HABIT", "Approve a proposed habit", "“yes” (to a habit offer)", shown="always", direct=True, offered=False)
_add("Tasks, time & planning", "DECLINE_HABIT", "Decline a proposed habit", "“no” (to a habit offer)", shown="always", direct=True, offered=False)
_add("Tasks, time & planning", "EXECUTE_GOAL", "Execute a stored goal", "“execute goal X”")
_add("Tasks, time & planning", "RESUME_TASK", "Recover a saved task record and state its real staleness", "“continue the ORBIT project”", "“resume the migration task”", shown="quick")

# ── Proactive ───────────────────────────────────────────────────────────────
_add("Proactive", "PROACTIVE_START", "Start the proactive daemon", "“start proactive mode”", shown="quick", explicit=True)
_add("Proactive", "PROACTIVE_STOP", "Stop the proactive daemon", "“stop proactive mode”", shown="quick", explicit=True)
_add("Proactive", "PROACTIVE_STATUS", "Proactive daemon status", "“proactive status”", shown="quick", direct=True)
_add("Proactive", "GET_PROPOSALS", "ELI's self-generated proposals/goals from observed signals", "“do you have any proposals for me”", "“what's on your agenda”", shown="quick")
_add("Proactive", "MORNING_REPORT", "Daily briefing (news/weather/agenda)", "“morning report”", "“give me my briefing”", shown="always", direct=True)

# ── Plugins ─────────────────────────────────────────────────────────────────
_add("Plugins", "PLUGIN_LIST", "List installed plugins", "“list plugins”", shown="quick", open=True)
_add("Plugins", "PLUGIN_SEARCH", "Search for plugins", "“search plugins for weather”", shown="quick")
_add("Plugins", "PLUGIN_INSTALL", "Install a plugin", "“install the X plugin”", shown="quick", explicit=True)
_add("Plugins", "PLUGIN_UNINSTALL", "Uninstall a plugin", "“uninstall the X plugin”", shown="quick", explicit=True)
_add("Plugins", "PLUGIN_ENABLE", "Enable a plugin", "“enable the X plugin”", shown="quick", explicit=True)
_add("Plugins", "PLUGIN_DISABLE", "Disable a plugin", "“disable the X plugin”", shown="quick", explicit=True)

# ── Shell & advanced ────────────────────────────────────────────────────────
_add("Shell & advanced", "RUN_CMD", "Run a shell command (gated/confirmed)", "“run the command: ls -la”", explicit=True)
_add("Shell & advanced", "SHELL_EXEC", "Execute shell (gated/confirmed)", "“execute: df -h”", shown="quick", explicit=True)
_add("Shell & advanced", "SEQUENCE", "Run a multi-step action sequence", "“do X then Y then Z”", shown="always")
_add("Shell & advanced", "ROUTING_FAULT_EXPLAIN", "Explain why an input failed to route", "“why did that fail to route”", shown="quick", direct=True)
_add("Shell & advanced", "NOOP", "Internal no-op (ignored fragment guard)", "(internal — fragmentary input is dropped)", offered=False)


_CAT_ORDER = list(OrderedDict((c, None) for (c, _, _) in _C.values()))


def _load_manifest(path=None) -> Tuple[List[dict], set, set]:
    if path is None:
        from eli.tools.registry.capability_updater import current_manifest_path
        path = current_manifest_path()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    caps = data.get("capabilities", [])
    routable = {c["action"] for c in caps
                if c.get("routable") or c.get("in_supported_list")}
    plugin = {c["action"] for c in caps if c.get("plugin")}
    return caps, routable, plugin


def generate_capabilities_doc(manifest=None, to=None) -> Dict:
    """Render the capabilities table from the manifest (`manifest`, the one just written, else the
    install's current one) + curated map, to `to` (the blueprint is OUT) or out_path(). Returns
    {ok, total, documented, routable, needs_phrase, path}."""
    caps, routable, plugin = _load_manifest(manifest)
    all_actions = {c["action"] for c in caps}

    # New routable actions not yet curated → emitted with a placeholder so the doc
    # stays complete; reported so a human can add a phrase.
    needs_phrase = sorted(a for a in routable if a not in _C)

    cats: "OrderedDict[str, list]" = OrderedDict((c, []) for c in _CAT_ORDER)
    for action, (cat, desc, phrases) in _C.items():
        cats.setdefault(cat, []).append((action, desc, phrases))
    if needs_phrase:
        cats["New / undocumented (add a phrase above)"] = [
            (a, "—", ["(needs activation phrase)"]) for a in needs_phrase]

    out: List[str] = []
    out.append("# ELI — Capabilities & Actions (with activation phrases)\n")
    out.append(f"*Auto-generated {datetime.date.today().isoformat()} from "
               f"`capability_manifest.json` ({len(all_actions)} capabilities; "
               f"{len(routable)} routable or executor-backed). Regenerated by "
               f"`eli/tools/registry/capabilities_doc.py` alongside the manifest.*\n")
    out.append(textwrap.dedent("""\
        **How to read this:** every row is a real action ELI can route to and
        execute. Activation phrases are **representative** — the router is
        paraphrase- and typo-tolerant; a phrase shows the *shape* that fires the
        action, not the only accepted wording. `Plugin` = backed by a plugin (rest
        are core executor+router). Voice users prefix with the wake word
        (“computer, …”). Generation actions (documents/scripts/projects) run the
        evidence-planner first (real code/web/memory analysis) before synthesising.
        """))

    for cat, rows in cats.items():
        if not rows:
            continue
        out.append(f"\n## {cat}\n")
        out.append("| Action | What it does | Example activation phrase(s) | Source |")
        out.append("|---|---|---|---|")
        for action, desc, phrases in rows:
            src = "Plugin" if action in plugin else "Core"
            out.append(f"| `{action}` | {desc} | {' · '.join(phrases)} | {src} |")

    aliases = sorted(all_actions - routable - set(_C))
    if aliases:
        out.append("\n## Aliases & internal actions (not directly user-triggered)\n")
        out.append("Synonyms normalised to a routable action, post-actions, or "
                   "internal pipeline steps:\n")
        out.append(", ".join(f"`{a}`" for a in aliases) + "\n")

    out.append("\n## Coverage\n")
    out.append(f"- Routable actions documented: **{len(routable & set(_C))}/{len(routable)}**")
    if needs_phrase:
        out.append(f"- ⚠️ New/undocumented (need a curated phrase): {', '.join(needs_phrase)}")
    out.append("")

    text, path = "\n".join(out), Path(to) if to else out_path()
    if not _same_but_for_the_date(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "total": len(all_actions),
        "routable": len(routable),
        "documented": len(routable & set(_C)),
        "needs_phrase": needs_phrase,
        "path": str(path),
    }


if __name__ == "__main__":
    r = generate_capabilities_doc(to=OUT)          # the tracked blueprint, asked for by name
    print(f"Wrote {r['path']}")
    print(f"  documented {r['documented']}/{r['routable']} routable")
    if r["needs_phrase"]:
        print(f"  needs phrase: {', '.join(r['needs_phrase'])}")
