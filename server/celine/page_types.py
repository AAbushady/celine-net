"""The page's types for what the routes take and answer, written from the routes' models.

    uv run python -m celine.page_types > web/src/lib/api.gen.ts

The file is committed, so a change to a model shows as a diff of the page's contract, and
tests/test_page_types.py fails while the committed file is not what the models give. Every
model the routes name is written (the answers of core/dto.py and of each feature's api.py,
and the request bodies), each by its name. A schema this cannot write is an error, never a
guess."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

HEAD = """/* Written from the server's route models (server/celine/page_types.py). Do not edit:
   change the model and write it again with
   `uv run python -m celine.page_types > web/src/lib/api.gen.ts`. */
"""
SKIP = {"HTTPValidationError", "ValidationError"}       # FastAPI's own 422 shapes: the app answers its own
REF = "#/components/schemas/"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def schemas() -> dict[str, dict]:
    """The models every route names, by name: the app's OpenAPI components, built on a
    scratch data folder (nothing is started, read or written but that empty folder)."""
    from . import __main__ as main, boot
    with tempfile.TemporaryDirectory() as tmp:
        before = os.environ.get("CELINE_DATA_DIR")
        os.environ["CELINE_DATA_DIR"] = tmp
        try:
            app = main.build_app(boot.load(), dev=False)
            found = app.openapi().get("components", {}).get("schemas", {})
        finally:
            if before is None:
                del os.environ["CELINE_DATA_DIR"]
            else:
                os.environ["CELINE_DATA_DIR"] = before
    return {name: s for name, s in found.items() if name not in SKIP}


def _key(name: str) -> str:
    return name if NAME.match(name) else json.dumps(name)


def _type(s: dict, depth: int = 0) -> str:
    """One JSON schema as a TypeScript type."""
    if "$ref" in s:
        if not s["$ref"].startswith(REF):
            raise ValueError(f"a reference this cannot write: {s['$ref']}")
        return s["$ref"][len(REF):]
    if "const" in s:
        return json.dumps(s["const"])
    if "enum" in s:
        return " | ".join(json.dumps(v) for v in s["enum"])
    for many in ("anyOf", "oneOf"):
        if many in s:
            return " | ".join(dict.fromkeys(_wrapped(x, depth) for x in s[many]))
    if "allOf" in s:
        return " & ".join(_wrapped(x, depth) for x in s["allOf"])
    kind = s.get("type")
    if isinstance(kind, list):
        return " | ".join(_type({**s, "type": k}, depth) for k in kind)
    if kind == "object" or "properties" in s:
        return _object(s, depth)
    if kind == "array":
        if "prefixItems" in s:
            return "[" + ", ".join(_type(x, depth) for x in s["prefixItems"]) + "]"
        return _wrapped(s.get("items", {}), depth) + "[]"
    if kind == "string":
        return "string"
    if kind in ("integer", "number"):
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind is None:
        return "unknown"
    raise ValueError(f"a schema this cannot write: {s}")


def _wrapped(s: dict, depth: int) -> str:
    """A type that can stand inside a union or before `[]`."""
    t = _type(s, depth)
    return f"({t})" if " | " in t or " & " in t else t


def _object(s: dict, depth: int) -> str:
    props = s.get("properties") or {}
    extra = s.get("additionalProperties")
    if not props:
        return f"Record<string, {_type(extra, depth) if isinstance(extra, dict) else 'unknown'}>"
    required = set(s.get("required") or [])
    pad = "  " * (depth + 1)
    lines = []
    for name, p in props.items():
        if p.get("description"):
            lines.append(f"{pad}/** {_comment(p['description'])} */")
        lines.append(f"{pad}{_key(name)}{'' if name in required else '?'}: {_type(p, depth + 1)}")
    return "{\n" + "\n".join(lines) + "\n" + "  " * depth + "}"


def _comment(text: str) -> str:
    return " ".join(text.split()).replace("*/", "* /")


def typescript(found: dict[str, dict]) -> str:
    """The models as one TypeScript module, each an exported type, by name."""
    out = [HEAD]
    for name in sorted(found):
        if not NAME.match(name):
            raise ValueError(f"a model name this cannot write: {name}")
        s = found[name]
        if s.get("description"):
            out.append(f"/** {_comment(s['description'])} */")
        out.append(f"export type {name} = {_type(s)}\n")
    return "\n".join(out)


def render() -> str:
    return typescript(schemas())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(render())
