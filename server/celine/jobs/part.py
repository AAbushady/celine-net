"""Background jobs as the start-up sees them (core/parts.py)."""
from __future__ import annotations

from ..core.parts import Part
from . import api

part = Part(name="jobs", routes=(api.router,))
