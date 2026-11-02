"""A typed one-word message is not a misheard fragment.

Typing "thanks" got "I only caught 'thanks' — could you say that again?": the guard for speech
fragments ("ply", "find your mo") ran on typed text too. The window marks what was typed.
"""
import contextvars
import pathlib

from eli.execution.router_enhanced import route
from eli.kernel.request_context import input_channel_var

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _route_as(channel, text):
    def run():
        input_channel_var.set(channel)
        return route(text)
    return contextvars.copy_context().run(run)


def test_typed_thanks_is_conversation():
    r = _route_as("typed", "thanks")
    assert r["action"] == "CHAT" and r["args"]["message"] == "thanks"


def test_speech_fragments_are_still_asked_about():
    assert route("thanks")["action"] == "NOOP"
