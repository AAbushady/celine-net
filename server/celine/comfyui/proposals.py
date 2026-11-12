"""The agent's own presets (read_preset, look_up_node and propose_preset of the image and
music generators):

- `read`: a preset as the page's editor has it, its fields and their values, and its
  workflow with its placeholders (presets.with_placeholders), as one JSON text, the way
  propose_preset takes them back.
- `node`, `nodes`: what ComfyUI says of its nodes (/object_info) through the ComfyUI of the
  kind's role, which the comfy helper answers from its saved copy while ComfyUI is down.
  One class: its inputs with their types and at most CHOICES choices, its outputs. Words:
  the classes whose name, display name or category hold them all, at most FOUND.
- `propose`: a preset of the agent's, checked at once by the page's own checks
  (presets.drafted: an upload's placeholders and the editor's values, then the preset as
  `load` reads it); with problems, every sentence and no card. Then it waits inside the
  turn for the user (approvals, kind preset: its name, task, nodes and parameters and what
  this ComfyUI misses; its workflow under Details) and, approved, is saved by the page's
  own save of an upload (adding.add): one of the user's own presets, marked `proposed`,
  at the end of its role. With `replaces` it is written over a preset the agent proposed
  before, which keeps its place; a shipped preset and one the user made are never
  replaced."""
from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

import httpx

from ..settings.registry import provider_headers
from . import presets as presets_mod, server as server_mod
from .presets import PresetError

CHOICES = 20                # choices of one input shown
FOUND = 40                  # classes one search lists
TTL = 300.0                 # seconds a server's whole /object_info is reused
NOBODY = "Nothing was proposed: nobody is here to approve it."
TASKS = {"generate": "New images", "edit": "Edits", "music": "Music", "video": "Video"}
SKILL = {"images": "image_generator", "music": "music_generator", "video": "video_generator"}
_infos: dict[str, tuple[float, dict]] = {}


# ---- read ------------------------------------------------------------------------------

def origin_words(p: presets_mod.Preset, user: str) -> str:
    if p.proposed:
        return "you proposed it, so propose_preset with replaces can replace it"
    return {"shipped": "it came with the app", "edited": f"it came with the app and {user} "
            f"changed it", "own": f"{user} made it"}[p.origin]


def read_head(p: presets_mod.Preset, user: str) -> str:
    return (f"{p.label} (`{p.id}`), {TASKS[p.task].lower()}: {origin_words(p, user)}. Its "
            f"label, values and workflow below are what propose_preset takes.")


def read_text(p: presets_mod.Preset) -> str:
    """A preset as the editor has it: its editor's values (but the name, which is
    `label`), what each field is, and its workflow with its placeholders."""
    values = {k: v for k, v in presets_mod.values(p).items() if k != "label"}
    fields = {f["key"]: f["label"] + (f": {f['help']}" if f.get("help") else "")
              for f in presets_mod.fields(p) if f["key"] != "label"}
    return json.dumps({"label": p.label, "task": p.task, "values": values, "fields": fields,
                       "workflow": presets_mod.with_placeholders(p)},
                      indent=1, ensure_ascii=False)


# ---- ComfyUI's nodes -----------------------------------------------------------------------

def role_provider(app, kind: str) -> str | None:
    """The ComfyUI of the kind's role: its first in the role's list, else the first
    ComfyUI. None when there is none."""
    s = app.cfg.settings
    comfy = [pid for pid, x in (s.providers if s is not None else {}).items()
             if x.get("kind") == "comfyui"]
    chain = [s.split(r)[0] for r in s.chain(presets_mod.ROLES[kind])] if s is not None else []
    return next((pid for pid in chain if pid in comfy), comfy[0] if comfy else None)


def _comfy(app, pid: str) -> server_mod.Comfy:
    s = app.cfg.settings
    return server_mod.Comfy(s.providers[pid]["base_url"], provider_headers(s, pid),
                            app.llm.router.transport)


async def info(app, kind: str, classes: set[str]) -> dict[str, Any]:
    """{class: its /object_info entry or None} from the kind's ComfyUI. ImageError with
    the sentence why not; httpx's error when it does not answer."""
    pid = role_provider(app, kind)
    if pid is None:
        raise presets_mod.ImageError("No ComfyUI is set: its address is in Settings, Images "
                                     "& Music.")
    comfy = _comfy(app, pid)
    try:
        return await comfy.object_info(classes)
    finally:
        await comfy.close()


async def _all(app, kind: str) -> dict[str, Any]:
    """Every node of the kind's ComfyUI (GET /object_info), reused for TTL seconds."""
    pid = role_provider(app, kind)
    if pid is None:
        raise presets_mod.ImageError("No ComfyUI is set: its address is in Settings, Images "
                                     "& Music.")
    url = app.cfg.settings.providers[pid]["base_url"].rstrip("/")
    hit = _infos.get(url)
    if hit is not None and time.monotonic() - hit[0] < TTL:
        return hit[1]
    comfy = _comfy(app, pid)
    try:
        r = await comfy.client.get(f"{url}/object_info")
        data = server_mod._ok(r).json()
    except ValueError:
        raise presets_mod.ImageError(f"ComfyUI answered /object_info with something that is "
                                     f"not JSON: is {url} a ComfyUI server?") from None
    finally:
        await comfy.close()
    if not isinstance(data, dict):
        raise presets_mod.ImageError("ComfyUI answered /object_info with something that is "
                                     "not a list of nodes.")
    _infos[url] = (time.monotonic(), data)
    return data


def _input(name: str, spec: Any) -> str:
    """One input: its type, or its choices (at most CHOICES), its default and range."""
    choices = presets_mod.options(spec)
    opts = spec[1] if isinstance(spec, list) and len(spec) > 1 and \
        isinstance(spec[1], dict) else {}
    if choices is not None:
        shown = ", ".join(str(c) for c in choices[:CHOICES])
        more = f" and {len(choices) - CHOICES} more" if len(choices) > CHOICES else ""
        out = f"- {name}: one of {shown}{more}" if choices else f"- {name}: no choices"
    else:
        typ = spec[0] if isinstance(spec, list) and spec else spec
        out = f"- {name}: {typ}"
    bits = []
    if "default" in opts and not isinstance(opts["default"], (list, dict)):
        bits.append(f"default {json.dumps(opts['default'], ensure_ascii=False)[:60]}")
    if "min" in opts and "max" in opts:
        bits.append(f"{opts['min']} to {opts['max']}")
    if opts.get("multiline"):
        bits.append("text of several lines")
    return out + (f" ({', '.join(bits)})" if bits else "")


def node_text(cls: str, entry: dict) -> str:
    """One node as ComfyUI describes it, small enough to read at once."""
    head = f"{cls}"
    if entry.get("display_name") and entry["display_name"] != cls:
        head += f" ({entry['display_name']})"
    if entry.get("category"):
        head += f", category {entry['category']}"
    lines = [head + "."]
    if entry.get("description"):
        lines.append(" ".join(str(entry["description"]).split())[:300])
    inputs = entry.get("input") or {}
    for group in ("required", "optional"):
        specs = inputs.get(group) or {}
        if specs:
            lines.append(f"Inputs, {group}:")
            lines += [_input(name, spec) for name, spec in specs.items()]
    outs = entry.get("output") or []
    names = entry.get("output_name") or []
    if outs:
        lines.append("Outputs, by their index in a link [node, index]: " + ", ".join(
            f"{i} {t}" + (f" ({names[i]})" if i < len(names) and names[i] != t else "")
            for i, t in enumerate(outs)) + ".")
    return "\n".join(lines)


async def node(app, kind: str, cls: str) -> tuple[str, bool]:
    """-> (one node's text, or why there is none; whether it is an error)."""
    try:
        got = (await info(app, kind, {cls})).get(cls)
    except presets_mod.ImageError as e:
        return str(e), True
    except httpx.HTTPStatusError as e:
        return str(e), True
    except httpx.HTTPError as e:
        return f"ComfyUI did not answer ({type(e).__name__}).", True
    if not isinstance(got, dict):
        return (f"ComfyUI has no node {cls}: a search by words finds a node's exact "
                f"name.", True)
    return node_text(cls, got), False


async def nodes(app, kind: str, words: str) -> tuple[str, bool]:
    """-> (the classes whose name, display name or category hold every word; whether it
    is an error)."""
    want = [w for w in words.lower().split() if w]
    try:
        every = await _all(app, kind)
    except presets_mod.ImageError as e:
        return str(e), True
    except httpx.HTTPError as e:
        return f"ComfyUI did not answer ({type(e).__name__}).", True
    hits = []
    for cls, entry in sorted(every.items()):
        entry = entry if isinstance(entry, dict) else {}
        hay = " ".join([cls, str(entry.get("display_name") or ""),
                        str(entry.get("category") or "")]).lower()
        if all(w in hay for w in want):
            hits.append((cls, entry))
    if not hits:
        return f"No node of ComfyUI matches {words!r}.", False
    lines = [f"- {cls}" + (f": {e['display_name']}" if e.get("display_name") not in
                           (None, "", cls) else "") +
             (f" ({e['category']})" if e.get("category") else "") for cls, e in hits[:FOUND]]
    if len(hits) > FOUND:
        lines.append(f"{len(hits) - FOUND} more: add words.")
    return "\n".join(lines), False


# ---- a proposal --------------------------------------------------------------------------

def _nodes_fact(graph: dict) -> str:
    counts = Counter(n["class_type"] for n in graph.values())
    return ", ".join(f"{c} x{n}" if n > 1 else c for c, n in sorted(counts.items()))


async def _missing(app, p: presets_mod.Preset) -> str:
    """What this ComfyUI misses for the preset, in the fact's words."""
    try:
        got = await info(app, p.kind, {n["class_type"] for n in p.workflow.values()})
    except presets_mod.ImageError as e:
        return f"Not checked: {e}"
    except httpx.HTTPStatusError as e:
        return f"Not checked: {e}"
    except httpx.HTTPError as e:
        return f"Not checked: ComfyUI did not answer ({type(e).__name__})."
    return p.missing(got) or "Nothing: it has every node and file."


async def propose(app, ctx, kind: str, *, label: str, workflow: Any,
                  values: dict | None, replaces: str, why: str) -> tuple[str, bool, dict]:
    """The agent's proposal (the module docstring). -> (the answer, whether it is an error,
    the call's display)."""
    from .adding import add
    user = app.cfg.user
    noun = presets_mod.NOUN[kind]
    if not (label or "").strip():
        return "label names the preset.", True, {}
    if isinstance(workflow, str):
        text = workflow
    elif isinstance(workflow, dict) and workflow:
        text = json.dumps(workflow, ensure_ascii=False)
    else:
        return ("workflow is the workflow in ComfyUI's API format with placeholders, as "
                "read_preset shows one."), True, {}
    if values is not None and not isinstance(values, dict):
        return "values is {key: value}, the keys read_preset shows.", True, {}
    values = {k: v for k, v in (values or {}).items() if k != "label"}
    old = None
    if (replaces or "").strip():
        old = presets_mod.of(kind).get(replaces.strip())
        if old is None:
            return f"There is no {noun} preset {replaces.strip()!r}.", True, {}
        if old.origin != "own":
            return (f"{old.label} came with the app, so it is never replaced: propose a new "
                    f"preset, without replaces."), True, {}
        if not old.proposed:
            return (f"{old.label} is one {user} made, so it is never replaced: propose a new "
                    f"preset, without replaces."), True, {}
    if len((why or "").strip()) < 10:
        return (f"Nothing was proposed: say why in `why`, one real sentence {user} can judge "
                f"(what the preset is for)."), True, {}
    try:
        pid, data, graph = presets_mod.drafted(kind, label, text, values,
                                               pid=old.id if old else None, proposed=True)
        p = presets_mod.checked(kind, pid, data, graph, "own")
    except PresetError as e:
        return f"The preset was not proposed: {' '.join(e.problems)}", True, {}
    approvals = getattr(app, "approvals", None)
    if approvals is None:
        return NOBODY, True, {}
    facts = [("Name", p.label), ("Task", TASKS[p.task]), ("Nodes", _nodes_fact(p.workflow)),
             ("Parameters", ", ".join(p.parameters)),
             ("Missing on this ComfyUI", await _missing(app, p))]
    if old is not None:
        facts.insert(1, ("Replaces", old.label))
    action = f"replace the preset {old.label}" if old else f"add the preset {p.label}"
    detail = ("Workflow", json.dumps(presets_mod.with_placeholders(p), indent=2,
                                     ensure_ascii=False))
    if no := await approvals.ask("preset", ctx.conversation_id, action, facts=facts,
                                 detail=detail, why=why, call=ctx.tool_call_id or None):
        return no, True, {"preset": p.id}
    try:
        saved = await add(app, kind, label, text, values, proposed=True,
                                   replaces=old.id if old else None)
    except PresetError as e:
        return f"{' '.join(e.problems)} Nothing was saved.", True, {"preset": p.id}
    role = presets_mod.ROLES[kind]
    from ..settings.registry import ROLES
    where = ROLES[role]["label"]
    if old is not None:
        said = f"Replaced the preset {old.label} (`{saved.id}`); it keeps its place in the {where} list."
    elif kind == "images":
        said = (f"Added the preset {saved.label} (`{saved.id}`) at the end of the {where} list: "
                f"image_generator__generate takes it as preset=\"{saved.id}\".")
    elif kind == "video":
        said = (f"Added the preset {saved.label} (`{saved.id}`) at the end of the {where} list: "
                f"video_generator__generate takes it as preset=\"{saved.id}\".")
    else:
        said = (f"Added the preset {saved.label} (`{saved.id}`) at the end of the {where} list; "
                f"music_generator__generate uses the first preset of that list, which "
                f"{user} orders in Settings, Media.")
    return said, False, {"preset": saved.id}
