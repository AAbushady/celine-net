"""The skills as the start-up sees them (core/parts.py): the agent's mood is appraised
after a turn."""
from __future__ import annotations

from ..core.parts import Part
from . import mind

part = Part(name="skills", wire=mind.start)
