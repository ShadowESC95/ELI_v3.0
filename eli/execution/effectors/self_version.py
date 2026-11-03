"""VERSION_CHANGES: which ELI this is and what is known of its changes (eli/runtime/version_record.py).
A grounded answer to "notice any updates to your code?", "check the diff pre and post install",
"what version are you", in place of an invented status report."""
from __future__ import annotations

from eli.execution.effectors.registry import register as _register


def _h_version_changes(a, args):
    from eli.runtime import version_record
    msg = version_record.told()
    return {"ok": True, "action": a, "version": version_record.current_version(), "content": msg, "response": msg,
            "evidence_source": "versions_seen"}


_register("VERSION_CHANGES", _h_version_changes)
