"""The routes that belong to no one feature, as the start-up sees them (core/parts.py)."""
from __future__ import annotations

from ..core.parts import Part
from . import (
    attachments, notebook, skills, context, kb, connectors,
    identity, llm, llm_proxy, settings, forms, speech,
    agent, commands, documents, bulk, presets, appearance,
    texts, prompts, approvals, waits, ws,
)

part = Part(name="api", routes=tuple(m.router for m in (
    attachments, notebook, skills, context, kb, connectors,
    identity, llm, llm_proxy, settings, forms, speech,
    agent, commands, documents, bulk, presets, appearance,
    texts, prompts, approvals, waits, ws,
)))
