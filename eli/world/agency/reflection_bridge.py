from __future__ import annotations
from eli.world.agency.autonomy_engine import EliWorldAutonomyEngine
from eli.world.core.schemas import WorldEvent
from eli.utils.log import get_logger

log = get_logger(__name__)

PERSONA_PART_CHARS = 12000     # as much of the persona, and of its overlay, as the world reads


def load_persona_text() -> str:
    """ELI's persona as eli.cognition.persona reads it (the files ELI really runs on, cleaned): the
    base first, then what it has learned. '' when there is none to read."""
    try:
        from eli.cognition.persona import read_auto_persona, read_base_persona
        parts = [read_base_persona(), read_auto_persona()]
    except Exception:
        log.debug("reflection_bridge persona read skipped", exc_info=True)
        return ""
    return "\n\n".join(str(part)[:PERSONA_PART_CHARS] for part in parts if str(part or "").strip())


def send_reflection_to_world(summary: str, depth: float = 0.7, source: str = "reflection_bridge"):
    engine = EliWorldAutonomyEngine()
    event = WorldEvent(event_type="reflection", source=source, summary=summary, payload={"depth": depth})
    return engine.ingest_event(event, persona_text=load_persona_text())
