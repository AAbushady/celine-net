"""Images with ComfyUI: one batch per call (comfyui/batches.py), new images and edits.

The agent's call starts the batch in the app's task group and returns at once with one slot
per image; the batch fills the slots in place as each image is made. It is no job: only a
batch that had to wait for the GPU wakes it, with one notice from the app once it is done.
`wait` holds its turn for the end, or until the batch still waits for the GPU after
GPU_TRY seconds. A batch's result is text: the preset, the size, the seed and each
image's short id. It sees a picture when it looks at it (`look`, by short ids or a whole
batch): the call's display names the pictures and the context sends them with its result
(turns/context.py calls_wire), so a look stores no copy and holds no image past its
clearing. An edit and a look take an image it made (an attachment of its own message, in
any conversation), one the user attached to this conversation, or its portrait
(batches.named_image); never one from the Messages archive or Documents.
"""
from __future__ import annotations

import types
import typing
from collections.abc import Callable
from typing import Annotated, Literal, Required, TypedDict

from .. import attachments, vision
from ..clock import zone
from ..core.ids import iso, span, when as when_
from ..core.statuses import MadeLife
from ..comfyui import batches as batches_mod, life, model_files, presets as presets_mod
from ..comfyui import proposals
from . import ToolContext, ToolResult, guide, parts, tool, described

SHORT = batches_mod.SHORT
# the last line of a call with wait=true that returns a batch still waiting for the GPU
GPU_LATER = ("The batch waits for the GPU and is made when the GPU is free, then a message "
             "from the app says what was made.")


def short(aid: str) -> str:
    return aid[:SHORT]


class Item(TypedDict, total=False):
    prompt: Required[Annotated[str, "What to draw, written for the preset (the guide "
                                    "shows how). With source: what to change."]]
    source: Annotated[str, "An image to edit: its short id, like f5ef648c, from a "
                           "result or list_images, or portrait:<name> for someone's "
                           "portrait. Empty: a new image."]
    source2: Annotated[str, "A second image for the edit, as source: another person, "
                            "an outfit, a place. Only a preset that takes several images "
                            "uses it (the guide says which)."]
    source3: Annotated[str, "A third image, as source2."]
    source4: Annotated[str, "A fourth image, as source2."]
    preset: Annotated[str, "A preset id. Left out: the first preset for a new image or an "
                           "edit, or the one a model or LoRA you give belongs to."]
    negative: Annotated[str, "What to keep out of the image. Only a preset that reads "
                             "a negative prompt uses it."]
    width: Annotated[int, "In pixels. Empty: the preset's size, or for an edit the "
                          "image's shape."]
    height: Annotated[int, "In pixels. Empty: as for width."]
    seed: Annotated[int, "The same seed and prompt give the same image. Empty: a new "
                         "one."]
    model: Annotated[str, "A model id from list_presets. Empty: the preset's own."]
    loras: Annotated[list[str], "LoRA ids from list_presets, each optionally with a "
                                "strength after a colon, like \"style/my_style_v1:0.8\"."]
    variant: Annotated[str, "A variant of the preset, for one that has variants (the "
                            "guide lists them per preset, like direct or expand). Never "
                            "a preset id: that goes in preset. Empty: its default."]


def item_line(i: dict, cfg, lives: bool = False) -> str:
    """One slot of a batch (as batches.view gives it), as it reads it; `lives`: with when
    what it made is cleared, or that it is kept (life.words)."""
    n = i["index"] + 1
    warned = "".join(f" Note: {w}" for w in i.get("warnings") or [])
    if i["state"] == "failed":
        return f"{n}. failed: {i['error']}{warned}"
    if i["state"] == "stopped":
        return f"{n}. stopped before it was made.{warned}"
    if i["state"] != "done":
        label, size = _label_size(i)
        line = f"{n}. {label}, {size}, seed {i['seed']}: {i.get('note') or i['state']}."
        if i.get("mode"):
            line += f" Variant {i['mode']}."
        if i.get("model"):
            line += f" Model {i['model']}."
        if i.get("loras"):
            line += f" LoRAs {', '.join(i['loras'])}."
        return line + warned
    return f"{n}. {made_line(i, cfg, lives)}{warned}"


def _label_size(i: dict) -> tuple[str, str]:
    """A slot's preset label and its size, "832x1216"."""
    p = presets_mod.of("images").get(i.get("preset", ""))
    att = i.get("attachment") or {}
    return (p.label if p else i.get("preset") or "",
            f"{att.get('width', i.get('width'))}x{att.get('height', i.get('height'))}")


def made_line(i: dict, cfg, lives: bool = False) -> str:
    """A made slot after its number: its image's short id, preset, size and seed, how it
    was made, and with `lives` when it is cleared or that it is kept (life.words)."""
    label, size = _label_size(i)
    att = i.get("attachment") or {}
    line = f"image {short(att['id'])}: {label}, {size}, seed {i['seed']}"
    if i.get("seconds") is not None:
        line += f", {i['seconds']:.0f} s"
    line += "."
    if i.get("source"):
        line += f" Edited from {_sources(i)}."
    if i.get("model"):
        line += f" Model {i['model']}."
    if i.get("loras"):
        line += f" LoRAs {', '.join(i['loras'])}."
    if i.get("triggers"):
        line += f" Trigger words added: {', '.join(i['triggers'])}."
    if i.get("mode"):
        line += f" Variant {i['mode']}."
    if lives and (lived := life.words(cfg, att.get("life"))):
        line += f" {lived}"
    return line


def look_line(n: int, got: dict, cfg) -> str:
    """Image `got` (batches.named_image's row) as a look reads it, `n` the place of its
    picture: what ties the picture to its id, as list_images and item_line say it."""
    if got["id"] == "portrait":
        return f"{n}. your portrait."
    if made := got.get("made"):
        lived = life.view(got["life"], got["life_at"]) if got.get("life") else None
        return f"{n}. " + made_line(
            {**made, "attachment": {**(made.get("attachment") or {}), "life": lived}}, cfg,
            lives=True)
    size = f", {got['width']}x{got['height']}" if got.get("width") else ""
    who = f"{cfg.user} attached" if got["role"] == "user" else "you made"
    return f"{n}. image {short(got['id'])}: {who} {got['filename']}{size}."


def _src(aid: str) -> str:
    return aid if aid.startswith("portrait:") else short(aid)


def _sources(i: dict) -> str:
    """An edit's images: "a", "a and b", "a, b and c"."""
    ids = [_src(i[k].get("id") or "") for k in presets_mod.SOURCES if i.get(k)]
    return ", ".join(ids[:-1]) + " and " + ids[-1] if len(ids) > 1 else ids[0]


def settled_head(b: dict, waited: float | None) -> str:
    """The first line of a batch read back: its state, and how long it waited for the
    GPU when it did (the notice after such a batch)."""
    items = b["items"]
    made = sum(i["state"] == "done" for i in items)
    did = f" waited {span(waited)} for the GPU and" if waited is not None else ""
    return f"Batch {short(b['id'])}{did} is {b['state']}: {made} of {len(items)} made."


# what the started line says of reporting back (images and music)
REPORTS = ("The batch reports back only if it has to wait for the GPU: then a message from "
           "the app says what was made once it is done.")
# the `wait` parameter of a generate tool (images and music)
WAIT = ("True: your turn waits until the batch is done or the user writes, as long as its "
        "time allows; a batch that still waits for the GPU after "
        f"{batches_mod.GPU_TRY:.0f} s returns as it is and is made when the GPU is free. "
        "Default false: the call returns at once.")


def batch_text(b: dict, cfg, *, started: bool, chat: bool = True,
               waited: float | None = None, lives: bool = False) -> str:
    """A batch as it reads it; started in a chat (`chat`), the user sees it there.
    `waited`: the seconds it waited for the GPU (settled_head); `lives`: item_line's."""
    items = batches_mod.view(b)["items"]
    if started:
        sees = f" {cfg.user} sees each one in this conversation when it is done." if chat \
            else ""
        head = (f"Batch {short(b['id'])} started: {len(items)} image"
                f"{'s' if len(items) != 1 else ''}, made one after the other.{sees} "
                f"{REPORTS} image_generator__read shows the batch.")
    else:
        head = settled_head(b, waited)
    lines = [head] + [item_line(i, cfg, lives) for i in items]
    return "\n".join(lines)


# ---- what images and music share: the role's presets and a batch's tools ----------------

def role_presets(cfg, role: str) -> list[presets_mod.Preset]:
    """The presets of a generation role ("images", "music"), in its order."""
    s = getattr(cfg, "settings", None)
    found = presets_mod.of(role)
    ids = dict.fromkeys(s.split(r)[1] for r in s.chain(presets_mod.ROLES[role])) \
        if s is not None else {}
    return [found[i] for i in ids if i in found]


# while the media runner is down (both switches off, or between its stop and start)
OFF = {"images": "Images are off.", "music": "Music is off."}


async def batch_generate(app, ctx: ToolContext, role: str, items, count: int, wait: bool,
                         text: Callable[..., str], **kw) -> ToolResult:
    """`text(batch, started, waited=None)`: the batch as it reads it (batch_text)."""
    if (batches := app.batches) is None:
        return ToolResult(content=OFF[role], is_error=True)
    try:
        b = await batches.create(role, items, count=count,
                                     conversation_id=ctx.conversation_id,
                                     tool_call_id=ctx.tool_call_id, **kw)
    except batches_mod.TooMany as e:
        return ToolResult(content=str(e), is_error=True)
    b = await batches.begin(b, wait=wait, time_left=ctx.time_left, wrote=ctx.user_wrote,
                            report=lambda done, waited: text(done, False, waited))
    said = text(b, b["state"] == "running" and not wait)
    if wait and b["state"] == "running" and batches.waits_for_gpu(b["id"]):
        said += "\n" + GPU_LATER
    # the call's display is the batch's own: it fills it in place (batches._publish)
    return ToolResult(content=said, is_error=b["state"] != "running" and not any(
        i["state"] == "done" for i in b["items"]))


async def batch_read(ctx: ToolContext, role: str, batch: str,
                     text: Callable[..., str]) -> ToolResult:
    try:
        b = await batches_mod.find(ctx.store, role, batch)
    except presets_mod.ImageError as e:
        return ToolResult(content=str(e), is_error=True)
    return ToolResult(content=text(b, False, lives=True), display=batches_mod.view(b))


async def batch_stop(app, ctx: ToolContext, role: str, batch: str,
                     text: Callable[[dict, bool], str]) -> ToolResult:
    if (batches := app.batches) is None:
        return ToolResult(content=OFF[role], is_error=True)
    try:
        b = await batches_mod.find(ctx.store, role, batch)
    except presets_mod.ImageError as e:
        return ToolResult(content=str(e), is_error=True)
    if not await batches.stop(b["id"], told=True):
        return ToolResult(content=f"Batch {short(b['id'])} is not running: it is "
                                  f"{b['state']}.")
    b = await batches_mod.get(ctx.store, b["id"])
    return ToolResult(content=text(b, False), display=batches_mod.view(b))


async def batch_keep(cfg, ctx: ToolContext, role: str, refs: list[str],
                     let_go: bool) -> ToolResult:
    """Keeps what batches of `role` made, or lets it go (life.keep): one line per id."""
    unit = life.UNIT[role].capitalize()
    found = [(ref, await life.find(ctx.store, role, ref)) for ref in refs or []]
    ids = [got["id"] for _, got in found if isinstance(got, dict)]
    rows = {r["id"]: r for r in await life.keep(cfg, ctx.store, ids, not let_go)} \
        if ids else {}
    lines, done = [], 0
    for _, got in found:
        if isinstance(got, str):
            lines.append(got)
            continue
        r = rows[got["id"]]
        s = short(r["id"])
        if r["life"] == MadeLife.CLEARED:
            lines.append(attachments.cleared_line(cfg, role, r["id"], r["life_at"],
                                           "" if let_go else " and cannot be kept"))
            continue
        done += 1
        if not let_go:
            lines.append(f"{unit} {s} is kept." if r["changed"] else
                         f"{unit} {s} was kept already.")
            continue
        due = life.clears_at(cfg, life.view(r["life"], r["life_at"]))
        lines.append((f"{unit} {s} is let go." if r["changed"] else f"{unit} {s} is "
                      f"temporary.") + (f" It is cleared on {when_(iso(due), zone(cfg))}."
                                        if due else ""))
    if not lines:
        return ToolResult(content=f"Name the {unit.lower()}s by their short ids.",
                          is_error=True)
    return ToolResult(content="\n".join(lines), is_error=done == 0, display={"count": done})


# ---- what images and music share: their presets, read, looked up and proposed -----------

async def preset_read(cfg, ctx: ToolContext, kind: str, preset: str,
                      part: int | None) -> ToolResult:
    """A preset of `kind` as the editor has it (comfyui/proposals.py read)."""
    p = presets_mod.of(kind).get((preset or "").strip())
    if p is None:
        ids = ", ".join(presets_mod.of(kind)) or "none"
        return ToolResult(content=f"There is no {presets_mod.NOUN[kind]} preset {preset!r}. "
                                  f"The presets are: {ids}.", is_error=True)
    out, _, _ = parts.read(proposals.read_text(p), part, ctx,
                           head=[proposals.read_head(p, cfg.user)],
                           call=f"{proposals.SKILL[kind]}__read_preset(preset=\"{p.id}\"")
    return ToolResult(content=out, display={"preset": p.id})


async def node_look_up(app, kind: str, node: str, search: str) -> ToolResult:
    """One node of ComfyUI, or the nodes whose names hold some words."""
    if bool(node.strip()) == bool(search.strip()):
        return ToolResult(content="Give node (a class name) or search (words), one of them.",
                          is_error=True)
    if app is None:
        return ToolResult(content=OFF[kind], is_error=True)
    text, bad = await (proposals.node(app, kind, node.strip()) if node.strip() else
                       proposals.nodes(app, kind, search.strip()))
    return ToolResult(content=text, is_error=bad)


async def preset_propose(app, ctx: ToolContext, kind: str, label: str, workflow, values,
                         replaces: str, why: str) -> ToolResult:
    if app is None:
        return ToolResult(content=OFF[kind], is_error=True)
    text, bad, display = await proposals.propose(app, ctx, kind, label=label,
                                                 workflow=workflow, values=values,
                                                 replaces=replaces, why=why)
    return ToolResult(content=text, is_error=bad, display=display or None)


PRESET_PART = ("Which part of a long preset, from 1; the head says how many. Default 1.")
NODE = "A node's class name, like \"KSampler\": its inputs and outputs."
NODE_SEARCH = ("Words: the node classes whose name, display name or category hold them all, "
               "like \"lora loader\".")
LABEL = "The preset's name, as Settings shows it."
WORKFLOW = ("The workflow in ComfyUI's API format, {id: {class_type, inputs}}; an input the "
            "app fills holds its placeholder as its whole value, like \"{{prompt}}\", "
            "\"{{seed}}\" or \"{{width:1024}}\" with its default. read_preset shows a whole "
            "one.")
VALUES = ("The editor's values, by the keys read_preset shows (family, good_at, prompting, "
          "example, default.<parameter>, variants, size.step, catalog.loras and the rest). "
          "Empty: none.")
REPLACES = "The id of a preset you proposed before, to replace it. Empty: a new preset."
PROPOSE_WHY = "Why, in one sentence, shown to {{username}} with the preset."


class Generator:
    """What the image and the music generator share: the presets of their `role` and
    whether one is set."""
    role: str       # the presets' role: images, music

    app = None      # the running app (core/services.py), from skills.build

    def __init__(self, cfg):
        self.cfg = cfg

    def _presets(self) -> list[presets_mod.Preset]:
        return role_presets(self.cfg, self.role)

    @property
    def status(self) -> str:
        return "up" if self._presets() else "unset"

    @property
    def error(self) -> str | None:
        if self.status == "up":
            return None
        return (f"No preset is set. {self.cfg.user} chooses presets in Settings, Images & "
                f"Music.")


class ImageGenerator(Generator):
    name = "image_generator"
    guide = guide("image_generator")
    description = described()
    role = "images"

    def __init__(self, cfg):
        super().__init__(cfg)
        if ids := tuple(p.id for p in self._presets()):
            self.generate = types.MethodType(_with_presets(ids), self)

    def _text(self, b: dict, started: bool, chat: bool = True,
              waited: float | None = None, lives: bool = False) -> str:
        return batch_text(b, self.cfg, started=started, chat=chat, waited=waited,
                          lives=lives)

    @property
    def docs(self) -> dict[str, str]:
        """The presets of the role, from their files, shown after the guide."""
        found = self._presets()
        out = ["The presets, in order. Without `preset`, an item takes the first one for "
               "its task (a new image, or an edit with `source`)."]
        for task in ("generate", "edit"):
            of_task = [p for p in found if p.task == task]
            out += [presets_mod.docs(p, n == 0) for n, p in enumerate(of_task)]
        return {"presets": "\n\n".join(out)}

    @tool(read_only=False, destructive=False, open_world=True, timeout=batches_mod.WAIT_MAX)
    async def generate(
        self,
        ctx: ToolContext,
        items: Annotated[list[Item], "The images, in order: each a new image from its "
                                     "prompt, or with source an edit."],
        count: Annotated[int, "How many images of each item, each with its own seed. "
                              "Default 1."] = 1,
        wait: Annotated[bool, WAIT] = False,
    ) -> ToolResult:
        """Make images with ComfyUI: new ones from a prompt, or edits of an image (an
        item with `source`). Load the guide first: it has each preset's prompt rules.
        The call returns at once with the batch id and one slot per image. The images
        are made one after the other and appear in this conversation as each is done;
        {{username}} sees them there. You see one when you look at it with
        image_generator__look; describe details only of pictures you looked at. A batch
        reports back only when it had to wait for the GPU: one message from the app once
        it is done.
        image_generator__read shows a batch, image_generator__stop stops one.
        What you make is cleared after the number of days set in Settings unless
        image_generator__keep keeps it.
        Your prompt reaches the model as you wrote it, a LoRA's trigger word in front.
        An item that does not fit its preset (an unknown variant, a size past the
        limits, a LoRA id that is none) fails with the reason in its line. Which preset
        fits which look, and how each takes a prompt, is in the guide. The size is
        width and height. Draw only what was asked for: no props or story nobody named.

        Example: items=[{"prompt": "..."}], count=2. Returns: the batch id and each
        slot (its image's short id once made); with wait=true, the batch when it is
        done, or as it is when it still waits for the GPU after a short try."""
        from .places import place_of
        chat = await ctx.store.read(lambda c: place_of(c, ctx.conversation_id)) == "chat"
        return await batch_generate(self.app, ctx, "images", items, count, wait,
                                    lambda b, s, waited=None: self._text(b, s, chat, waited),
                                    clip=True)

    @tool()
    async def read(
        self,
        ctx: ToolContext,
        batch: Annotated[str, "The batch id from generate, like 3f2a9c1e."],
    ) -> ToolResult:
        """A batch's state: each slot, waiting, being made, made (with its image's
        short id and when it is cleared, or that it is kept), failed (with why) or
        stopped. Returns: one line per image."""
        return await batch_read(ctx, "images", batch, self._text)

    @tool(read_only=False, destructive=False)
    async def stop(
        self,
        ctx: ToolContext,
        batch: Annotated[str, "The batch id from generate, like 3f2a9c1e."],
    ) -> ToolResult:
        """Stop a batch: the image being made is interrupted, the ones after it are not
        made. What was made stays. Returns: the batch."""
        return await batch_stop(self.app, ctx, "images", batch, self._text)

    @tool(read_only=False, destructive=False)
    async def keep(
        self,
        ctx: ToolContext,
        images: Annotated[list[str], "The images' short ids, like f5ef648c, from a result "
                                     "or list_images."],
        let_go: Annotated[bool, "True: let them go, so they are cleared after their days "
                                "again. Default false: keep them."] = False,
    ) -> ToolResult:
        """Keep images you made, so they are never cleared; what is not kept is cleared
        after the number of days set in Settings. Returns: one line per image."""
        return await batch_keep(self.cfg, ctx, "images", images, let_go)

    @tool()
    async def list_presets(
        self,
        ctx: ToolContext,
        kind: Annotated[Literal["preset", "lora", "model"], "preset: the presets. lora "
                        "or model: what one preset can use, from the ComfyUI server "
                        "itself."] = "preset",
        preset: Annotated[str | None, "For lora and model: a preset id. Empty: the "
                                      "first preset for new images that has them."] = None,
        category: Annotated[str | None, "LoRAs: character, style, realism, nsfw, "
                                        "anatomy, concept, tool. Models: base, finetune, "
                                        "edit. Empty: all."] = None,
        search: Annotated[str | None, "Words that must all appear in the id, name, tags "
                                      "or triggers."] = None,
    ) -> ToolResult:
        """The presets, or the models and LoRAs one preset can use: id, category, name,
        what it does, trigger words, default strength. Read it before you give an item
        a model or LoRAs. Returns: one line each, grouped by category."""
        from .. import image_catalog
        from ..settings.registry import provider_headers
        found = self._presets()
        if kind == "preset":
            return ToolResult(content="\n".join(
                f"- `{p.id}` {p.label} ({p.family}), "
                f"{'edits' if p.task == 'edit' else 'new images'}: {p.good_at}"
                for p in found) or "No preset is set.", display={"count": len(found)})
        with_catalog = [p for p in found if p.models or p.loras]
        want = preset or ""
        p = next((x for x in with_catalog if want in (x.id, x.label)), None) \
            if want else next((x for x in with_catalog if x.task == "generate"),
                              with_catalog[0] if with_catalog else None)
        if p is None:
            names = ", ".join(x.id for x in with_catalog) or "none"
            return ToolResult(content=f"No preset with models and LoRAs matches. Those "
                                      f"presets are: {names}.", is_error=True)
        s = self.cfg.settings
        pid = next(s.split(r)[0] for r in s.chain(presets_mod.ROLES["images"])
                   if s.split(r)[1] == p.id)
        try:
            cat = await image_catalog.load(
                s.providers[pid]["base_url"], provider_headers(s, pid),
                transport=self.app.llm.router.transport if self.app else None)
        except presets_mod.ImageError as e:
            return ToolResult(content=str(e), is_error=True)
        except Exception as e:
            return ToolResult(content=f"ComfyUI did not list its models "
                                      f"({type(e).__name__}).", is_error=True)
        prefixes = p.models if kind == "model" else p.loras
        rows = cat.list(kind, prefixes, category=category, search=search)
        if not rows:
            return ToolResult(content=f"No {kind} of {p.label} matches.")
        lines, last = [], None
        for r in rows[:40]:
            if r.category != last:
                last = r.category
                lines.append(f"\n{last}:")
            lines.append(f"- {r.line()}")
        text = f"{kind}s of {p.label} (`{p.id}`):" + "\n".join(lines)
        if len(rows) > 40:
            text += f"\n\n{len(rows) - 40} more. Narrow with category or search."
        return ToolResult(content=text.strip(), display={"count": len(rows)})

    @tool()
    async def list_images(self, ctx: ToolContext) -> ToolResult:
        """The images in this conversation, newest first: the ones you made, each with
        the prompt, the preset, the size and the seed it was made from and whether it is
        kept or cleared, and the ones {{username}} attached. Use it to see what an earlier
        image was asked for before you edit it or make a variation. Returns: one block
        per image with its short id, the source of an edit."""
        rows = await attachments.images(ctx.store, ctx.conversation_id)
        if not rows:
            return ToolResult(content="There are no images in this conversation.")
        blocks = []
        for r in rows[:30]:
            when = when_(r["created_at"], zone(self.cfg))
            made = r["made"]
            if made:
                att = made.get("attachment") or {}
                head = (f"{short(r['id'])}  {when}  you made it with {made.get('preset')}, "
                        f"{att.get('width')}x{att.get('height')}, seed {made.get('seed')}")
                if made.get("source"):
                    head += f", edited from {_sources(made)}"
                if made.get("model"):
                    head += f", model {made['model']}"
                if made.get("loras"):
                    head += f", LoRAs {', '.join(made['loras'])}"
                if r["life"] == MadeLife.KEPT:
                    head += ", kept"
                elif r["life"] == MadeLife.CLEARED:
                    head += f", cleared on {when_(r['life_at'], zone(self.cfg))}"
                lines = [head, f"  prompt: {made.get('prompt') or '(not recorded)'}"]
                if made.get("negative"):
                    lines.append(f"  negative: {made['negative']}")
                blocks.append("\n".join(lines))
            elif r["role"] == "user":
                blocks.append(f"{short(r['id'])}  {when}  {self.cfg.user} attached "
                              f"{r['filename']}")
            else:
                blocks.append(f"{short(r['id'])}  {when}  you made {r['filename']} (what "
                              f"it was asked for is not recorded)")
        more = len(rows) - 30
        text = "\n\n".join(blocks)
        if more > 0:
            text += f"\n\n{more} older images are not listed."
        return ToolResult(content=text, display={"count": len(rows)})

    @tool()
    async def look(
        self,
        ctx: ToolContext,
        images: Annotated[list[str] | None, "The images, each as an edit's source takes "
                                            "it: a short id like f5ef648c from a result or "
                                            "list_images (an image you made, in any "
                                            "conversation, or one {{username}} attached to "
                                            "this conversation), or portrait:<name>. As "
                                            "many as you need."] = None,
        batch: Annotated[str, "A batch id from generate, like 3f2a9c1e: every image it "
                              "made, in order, before the images."] = "",
        question: Annotated[str | None, "What you want to know about them, when your model "
                                        "cannot see images and another describes "
                                        "them."] = None,
    ) -> ToolResult:
        """Look at images: ones you made, ones {{username}} attached to this conversation,
        or your portrait, as many as you need in one call. Look before you say what a
        picture you made shows. Example: image_generator__look(images=["f5ef648c",
        "0d1e2f3a"]), or image_generator__look(batch="3f2a9c1e"). Returns: one line per
        image in order (its short id, preset, size and seed, and when it is cleared or
        that it is kept) and the pictures in that order, or their descriptions when your
        model cannot see images."""
        # a list written as one string ("f5ef648c, 0d1e2f3a") reads as its ids
        refs = images.replace(",", " ").split() if isinstance(images, str) else \
            [str(r).strip() for r in images or () if str(r).strip()]
        lines: list[str] = []
        if batch.strip():
            try:
                b = await batches_mod.find(ctx.store, "images", batch)
            except presets_mod.ImageError as e:
                lines.append(str(e))
            else:
                lines.append(settled_head(b, None))
                refs = [i["attachment"]["id"] for i in b["items"]
                        if i["state"] == "done" and i.get("attachment")] + refs
        elif not refs:
            return ToolResult(content="Give images, by their short ids, or a batch id.",
                              is_error=True)
        sees = await attachments.turn_sees_images(self.cfg, ctx)
        llm = self.app.llm if self.app is not None else None
        tried: set[str] = set()
        shown: list[dict] = []          # each image looked at, in order: {id, ..., media_type}
        for ref in refs:
            if ref.lower() in tried:
                continue
            tried.add(ref.lower())
            try:
                got = await batches_mod.named_image(self.cfg, ctx.store, ref,
                                                    ctx.conversation_id, True)
            except presets_mod.ImageError as e:
                lines.append(str(e))
                continue
            if any(s["id"] == got["id"] for s in shown):
                continue
            path = attachments.blob_path(self.cfg, got["sha256"])
            if not path.exists():
                lines.append(f"Image {short(got['id'])}: the file is no longer stored.")
                continue
            shown.append({k: got[k] for k in ("id", "filename", "sha256", "media_type")})
            line = look_line(len(shown), got, self.cfg)
            if not sees:
                line += "\n" + await vision.describe(llm, path.read_bytes(),
                                                     ctx.conversation_id, ctx.turn_id,
                                                     question)
            lines.append(line)
        if not shown:
            return ToolResult(content="\n".join(lines), is_error=True)
        # the pictures go to the model with the result, as the display names them; the
        # look keeps no copy (module docstring)
        return ToolResult(content="\n".join(lines),
                          display={"count": len(shown), **({"see": shown} if sees else {})})

    # ---- model files: found on a hub, asked of the user (comfyui/model_files.py) --------

    @tool(open_world=True)
    async def search_models(
        self,
        ctx: ToolContext,
        hub: Annotated[Literal["civitai", "huggingface"], "civitai: Civitai's catalog. "
                       "huggingface: Hugging Face's .safetensors files."],
        kind: Annotated[Literal["lora", "model"], "lora: LoRAs. model: diffusion models, "
                        "for a preset's model field."],
        query: Annotated[str, "Words to search for. Empty: the hub's listing in the order "
                              "of sort."] = "",
        preset: Annotated[str, "The image preset the file is for, by its id: checked first "
                               "that it takes files of this kind. Empty: none."] = "",
        base: Annotated[str, "The base model as the hub names it, like \"Qwen\" or "
                             "\"Flux.1 D\". Empty: any."] = "",
        sort: Annotated[Literal["newest", "most_downloaded", "highest_rated"] | None,
                        "The order: newest, most_downloaded or highest_rated. Empty: the "
                        "hub's own."] = None,
        period: Annotated[Literal["day", "week", "month", "year", "all"] | None,
                          "With sort: the time it counts over, day, week, month, year or "
                          "all. Empty: all."] = None,
        tag: Annotated[str, "One tag of the hub, like \"anime\" or \"style\". Empty: "
                            "any."] = "",
        after: Annotated[str, "The next page: the after value the last line of a page "
                              "gives."] = "",
    ) -> ToolResult:
        """Search a model hub, or list it with no words, for LoRAs or diffusion models
        the comfy helper can download. One line per file: title, type, base model, size,
        downloads, whether the hub marks it mature, trigger words, and the file value
        that request_model takes as it is. The hub's titles and words were written by its
        users. Example: image_generator__search_models(hub="civitai", kind="lora",
        query="ink wash", base="Qwen"); a listing: image_generator__search_models(
        hub="civitai", kind="lora", sort="most_downloaded", period="week").
        Returns: a page of files, and the call for the next page when there is one."""
        if self.app is None:
            return ToolResult(content=OFF["images"], is_error=True)
        text, bad, display = await model_files.search(
            self.app, hub=hub, kind=kind, query=query, preset=preset, base=base,
            sort=sort or "", period=period or "", tag=tag, after=after)
        return ToolResult(content=text, is_error=bad, display=display or None)

    @tool(open_world=True)
    async def read_model(
        self,
        ctx: ToolContext,
        model: Annotated[str, "The model: a file value search_models printed, a Civitai "
                              "page address (with or without modelVersionId), or "
                              "hf:<owner>/<name>."],
        part: Annotated[int | None, "Which part of a long read, from 1; the head says how "
                                    "many. Default 1."] = None,
    ) -> ToolResult:
        """Read what a hub says of one model: its facts, each version with each file and
        the file value request_model takes (or why the comfy helper does not take a
        file), its description and its example prompts with their settings, numbered
        for look_examples. The description and prompts were written by its users. A
        long read comes in parts. Example: image_generator__read_model(
        model="civitai:1234567"). Returns: the model as text."""
        if self.app is None:
            return ToolResult(content=OFF["images"], is_error=True)
        head, text, bad, display = await model_files.read(self.app, model)
        if bad:
            return ToolResult(content=text, is_error=True)
        out, _, _ = parts.read(text, part, ctx, head=[head],
                               call=f"image_generator__read_model(model=\"{model.strip()}\"")
        return ToolResult(content=out, display=display)

    @tool(open_world=True)
    async def look_examples(
        self,
        ctx: ToolContext,
        model: Annotated[str, "The model, as read_model takes it."],
        examples: Annotated[list[int] | None, "Which examples, by their numbers in "
                                              "read_model, at most 4. Empty: the first 4 "
                                              "that have a picture."] = None,
    ) -> ToolResult:
        """Look at a model's example pictures, at most 4 a call, each with its prompt
        and settings as its maker gave them. Example: image_generator__look_examples(
        model="civitai:1234567", examples=[1, 3]). Returns: the pictures, or their
        descriptions when your model cannot see images."""
        if self.app is None:
            return ToolResult(content=OFF["images"], is_error=True)
        sees = await attachments.turn_sees_images(self.cfg, ctx)
        text, bad, display, ids = await model_files.pictures(self.app, ctx, model, examples,
                                                             sees)
        return ToolResult(content=text, is_error=bad, display=display or None,
                          images=ids if sees and ids else None)

    @tool(read_only=False, destructive=False, open_world=True)
    async def request_model(
        self,
        ctx: ToolContext,
        preset: Annotated[str, "The image preset the file is for, by its id from "
                               "list_presets."],
        kind: Annotated[Literal["lora", "model"], "lora: a LoRA, given later in loras. "
                        "model: a diffusion model, given later in model."],
        file: Annotated[str, "Which file: the file value search_models or read_model "
                             "printed (civitai:<version>, civitai:<version>/<file>, "
                             "hf:<owner>/<name>/<path>), a Civitai page address with "
                             "modelVersionId, or a Hugging Face file's address."],
        why: Annotated[str, "Why, in one sentence, shown to {{username}} with the "
                            "request."],
    ) -> ToolResult:
        """Ask {{username}} to download a LoRA or a diffusion model for a preset, from
        Civitai or Hugging Face, through the comfy helper. Checked at once: the preset
        takes such files, the helper finds the file and takes it, and it is not there
        yet. Then it waits for {{username}}'s approval; the call returns at once and your
        turn goes on. A message from the app says when the file is ready, with the id to
        give in loras or model and its trigger words, or why it did not come.
        Example: image_generator__request_model(preset="qwen2.1-smart", kind="lora",
        file="civitai:1234567", why="An ink wash style for the poster {{username}} asked
        for."). Example: image_generator__request_model(preset="krea2-turbo",
        kind="model",
        file="hf:Comfy-Org/Krea-2/diffusion_models/krea2_raw_fp8_scaled.safetensors",
        why="The full Krea 2 model for a slower, finer picture.").
        Returns: what was asked for, or why nothing was."""
        files = getattr(self.app, "model_files", None)
        if files is None:
            return ToolResult(content=OFF["images"], is_error=True)
        text, bad, display = await files.request(ctx, preset=preset, kind=kind, file=file,
                                                 why=why)
        return ToolResult(content=text, is_error=bad, display=display)

    # ---- presets of its own (comfyui/proposals.py) ---------------------------------------

    @tool()
    async def read_preset(
        self,
        ctx: ToolContext,
        preset: Annotated[str, "A preset id, as list_presets shows it."],
        part: Annotated[int | None, PRESET_PART] = None,
    ) -> ToolResult:
        """Read an image preset as Settings' editor has it: its values, what each field
        is, and its workflow with its placeholders, the way propose_preset takes them.
        Example: image_generator__read_preset(preset="krea2-turbo"). Returns: JSON, a long
        one in parts."""
        return await preset_read(self.cfg, ctx, "images", preset, part)

    @tool(open_world=True)
    async def look_up_node(
        self,
        ctx: ToolContext,
        node: Annotated[str, NODE] = "",
        search: Annotated[str, NODE_SEARCH] = "",
    ) -> ToolResult:
        """Look up a node of ComfyUI, for a workflow you write: one class's inputs with
        their types and choices and its outputs, or the classes that match some words.
        Example: image_generator__look_up_node(search="lora loader"). Returns: the node,
        or one line per class."""
        return await node_look_up(self.app, "images", node, search)

    @tool(read_only=False, destructive=False, open_world=True)
    async def propose_preset(
        self,
        ctx: ToolContext,
        label: Annotated[str, LABEL],
        workflow: Annotated[dict, WORKFLOW],
        why: Annotated[str, PROPOSE_WHY],
        values: Annotated[dict | None, VALUES] = None,
        replaces: Annotated[str, REPLACES] = "",
    ) -> ToolResult:
        """Propose an image preset of your own: a ComfyUI workflow with the app's
        placeholders and its texts. Checked at once as Settings checks an uploaded
        workflow and an edit; then it waits until {{username}} approves it, and is saved
        as one of {{username}}'s presets, on, at the end of the Image generation list.
        With replaces, it replaces a preset you proposed before; a preset that came with
        the app or one {{username}} made is never replaced. Example:
        image_generator__propose_preset(label="Ink wash", workflow={...}, values={
        "good_at": "Ink wash paintings."}, why="..."). Returns: the preset's id, or every
        problem, or why nothing was saved."""
        return await preset_propose(self.app, ctx, "images", label, workflow, values,
                                    replaces, why)


def _with_presets(ids: tuple[str, ...]):
    """`generate` as a function of its own whose items' `preset` is an enum of `ids`,
    the live presets: the schema it is offered names them, so an invented one is
    refused before the batch."""
    # note: a copy of the function per skill built, rebuilt with the skills on reload
    fn = ImageGenerator.generate
    hints = typing.get_type_hints(Item, include_extras=True)
    about = typing.get_args(hints["preset"])[1:]
    item = TypedDict("Item", {**hints, "preset": Annotated[Literal[ids], *about]},
                     total=False)
    out = types.FunctionType(fn.__code__, fn.__globals__, fn.__name__, fn.__defaults__,
                             fn.__closure__)
    out.__kwdefaults__, out.__doc__ = fn.__kwdefaults__, fn.__doc__
    out.__dict__.update(fn.__dict__)
    of_fn = typing.get_type_hints(fn, include_extras=True)
    out.__annotations__ = {**of_fn, "items": Annotated[list[item],
                                                       *typing.get_args(of_fn["items"])[1:]]}
    return out
