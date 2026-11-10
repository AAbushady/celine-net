"""The page's types are written from the route models (celine/page_types.py), and the
committed file is what the models give now."""
from __future__ import annotations

from pathlib import Path

import pytest

from celine import page_types

FILE = Path(__file__).parent.parent / "web" / "src" / "lib" / "api.gen.ts"


def test_the_committed_types_are_what_the_models_give():
    committed = FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == page_types.render(), (
        "a route model changed: write the page's types again with "
        "`uv run python -m celine.page_types > web/src/lib/api.gen.ts`")


def test_the_sub_agent_answers_are_models_the_page_gets():
    found = page_types.schemas()
    assert {"SubAgent", "SubAgentList", "SubAgentTree", "Display", "FilterOption"} <= set(found)
    assert found["SubAgent"]["properties"]["state"] == {"$ref": "#/components/schemas/AgentState"}
    assert "additionalProperties" in found["SubAgent"], "a field the model does not name is refused"


def test_a_schema_is_written_as_typescript():
    got = page_types.typescript({
        "Row": {"type": "object", "description": "One row.", "required": ["id", "state", "tags"],
                "properties": {
                    "id": {"type": "string"},
                    "state": {"$ref": "#/components/schemas/State"},
                    "tags": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}],
                             "description": "why it stopped"},
                    "by-name": {"type": "object", "additionalProperties": {"type": "integer"}}}},
        "State": {"type": "string", "enum": ["on", "off"]},
    })
    assert got.endswith('''/** One row. */
export type Row = {
  id: string
  state: State
  tags: (string | null)[]
  /** why it stopped */
  note?: string | null
  "by-name"?: Record<string, number>
}

export type State = "on" | "off"
''')


def test_a_schema_it_cannot_write_is_an_error():
    with pytest.raises(ValueError, match="cannot write"):
        page_types.typescript({"Odd": {"type": "tuple-of-moons"}})
    with pytest.raises(ValueError, match="cannot write"):
        page_types.typescript({"Elsewhere": {"$ref": "other.json#/Thing"}})
