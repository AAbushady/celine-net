"""Video with ComfyUI: one batch per call (comfyui/batches.py), the same way as images and
music.

The agent's call starts the batch in the app's task group and returns at once with one slot
per clip; the batch fills the slots in place as each clip is made, and a message from the
app says when it is done. A clip may start from a picture (`source`, named as an image
edit's source is). It cannot watch a clip: `look` shows it the stills its preset saves
with each clip (the first frame, two in between and the last), the way image_generator's
look shows a picture, with no copy kept.
"""
from __future__ import annotations

from typing import Annotated, Required, TypedDict

from .. import attachments, vision
from ..comfyui import batches as batches_mod, life, presets as presets_mod
from . import ToolContext, ToolResult, guide, tool, described
from .images import (LABEL, NODE, NODE_SEARCH, PRESET_PART, PROPOSE_WHY, REPLACES, REPORTS,
                     VALUES, WAIT, WORKFLOW, Generator, batch_generate, batch_keep,
                     batch_read, batch_stop, node_look_up, preset_propose, preset_read,
                     settled_head, took)

SHORT = batches_mod.SHORT


def _label_size(i: dict) -> tuple[str, str]:
    """A slot's preset label and its size, "1280x704"."""
    p = presets_mod.of("video").get(i.get("preset", ""))
    att = i.get("attachment") or {}
    return (p.label if p else i.get("preset") or "",
            f"{att.get('width', i.get('width'))}x{att.get('height', i.get('height'))}")


class Item(TypedDict, total=False):
    prompt: Required[Annotated[str, "What happens in the clip: the subject, the motion, the "
                                    "place and the light, the camera (the guide shows how)."]]
    source: Annotated[str, "A picture the clip starts from, named as an image edit's source "
                           "is: an image's short id, portrait for your portrait, or the file "
                           "name of one {{username}} attached here. Empty: from the prompt "
                           "alone."]
    seconds: Annotated[float, "How long the clip lasts. Empty: the preset's default."]
    negative: Annotated[str, "What to keep out of it, when the preset reads a negative "
                             "prompt."]
    width: Annotated[int, "In pixels. Empty: the preset's, or the picture's shape."]
    height: Annotated[int, "In pixels."]
    preset: Annotated[str, "A video preset id. Empty: the first of the Video generation "
                           "list."]


def item_line(i: dict, cfg, lives: bool = False) -> str:
    """One slot of a batch (as batches.view gives it), as it reads it; `lives`: when it is
    cleared or that it is kept."""
    n = i["index"] + 1
    if i["state"] == "failed":
        return f"{n}. failed: {i['error']}"
    if i["state"] == "stopped":
        return f"{n}. stopped before it was made."
    label, size = _label_size(i)
    length = i.get("length") or 0
    secs = f"{length / i['fps']:.1f} s" if length and i.get("fps") else "its preset's length"
    src = f" From picture {i['source']['id'][:SHORT]}." if i.get("source") else ""
    if i["state"] != "done":
        return (f"{n}. {label}, {size}, {secs}, seed {i['seed']}: "
                f"{i.get('note') or i['state']}.{src}")
    att = i["attachment"]
    lived = life.words(cfg, att.get("life")) if lives else ""
    frames = len(i.get("frames") or [])
    return (f"{n}. clip {att['id'][:SHORT]}: {label}, {size}, {secs}, seed {i['seed']}, "
            f"{took(i)}.{src} {frames} stills for video_generator__look."
            + (f" {lived}" if lived else ""))


def batch_text(b: dict, cfg, *, started: bool, chat: bool = True,
               waited: float | None = None, lives: bool = False) -> str:
    """A batch as it reads it; started in a chat (`chat`), the user sees it there.
    `waited`: the seconds it waited for the GPU (images.settled_head)."""
    items = batches_mod.view(b)["items"]
    if started:
        sees = f" {cfg.user} sees each one in this conversation when it is done." if chat \
            else ""
        head = (f"Batch {b['id'][:SHORT]} started: {len(items)} clip"
                f"{'s' if len(items) != 1 else ''}, made one after the other, minutes "
                f"each.{sees} {REPORTS} video_generator__read shows the batch.")
    else:
        head = settled_head(b, waited)
    return "\n".join([head] + [item_line(i, cfg, lives) for i in items])


class VideoGenerator(Generator):
    name = "video_generator"
    guide = guide("video_generator")
    description = described()
    role = "video"

    def _text(self, b: dict, started: bool, chat: bool = True,
              waited: float | None = None, lives: bool = False) -> str:
        return batch_text(b, self.cfg, started=started, chat=chat, waited=waited,
                          lives=lives)

    @property
    def docs(self) -> dict[str, str]:
        """The presets of the role, from their files, shown after the guide."""
        found = self._presets()
        return {"presets": "\n\n".join(presets_mod.docs(p, n == 0)
                                       for n, p in enumerate(found))} if found else {}

    @tool(read_only=False, destructive=False, open_world=True, timeout=batches_mod.WAIT_MAX)
    async def generate(
        self,
        ctx: ToolContext,
        items: Annotated[list[Item], "The clips, in order: each from its prompt, or with "
                                     "source from a picture."],
        count: Annotated[int, "How many clips of each item, each with its own seed. "
                              "Default 1."] = 1,
        wait: Annotated[bool, WAIT] = False,
    ) -> ToolResult:
        """Make short video clips with ComfyUI: from a prompt, or from a picture brought to
        life (`source`). Load the guide first: it has the prompt rules. The call returns at
        once with the batch id and one slot per clip; a clip takes minutes. The clips are
        made one after the other and appear in this conversation as each is done;
        {{username}} sees them there. When the batch is done, one message from the app
        says what was made (none when wait=true already returned it done). You see a clip
        through its stills with video_generator__look. video_generator__read shows a
        batch, video_generator__stop stops one. What you make is cleared after the number
        of days set in Settings unless video_generator__keep keeps it. Returns: the batch
        id and each slot."""
        from .places import place_of
        chat = await ctx.store.read(lambda c: place_of(c, ctx.conversation_id)) == "chat"
        return await batch_generate(self.app, ctx, "video", items, count, wait,
                                    lambda b, s, waited=None: self._text(b, s, chat, waited))

    @tool()
    async def read(
        self,
        ctx: ToolContext,
        batch: Annotated[str, "The batch id from generate, like 3f2a9c1e."],
    ) -> ToolResult:
        """A batch's state: each slot, waiting, being made, made (with its clip's short
        id and when it is cleared, or that it is kept), failed (with why) or stopped.
        Returns: one line per clip."""
        return await batch_read(ctx, "video", batch, self._text)

    @tool(read_only=False, destructive=False)
    async def stop(
        self,
        ctx: ToolContext,
        batch: Annotated[str, "The batch id from generate, like 3f2a9c1e."],
    ) -> ToolResult:
        """Stop a batch: the clip being made is interrupted, the ones after it are not
        made. What was made stays. Returns: the batch."""
        return await batch_stop(self.app, ctx, "video", batch, self._text)

    @tool(read_only=False, destructive=False)
    async def keep(
        self,
        ctx: ToolContext,
        clips: Annotated[list[str], "The clips' short ids, like f5ef648c, from a batch's "
                                    "lines."],
        let_go: Annotated[bool, "True: let them go, so they are cleared after their days "
                                "again. Default false: keep them."] = False,
    ) -> ToolResult:
        """Keep clips you made, so they are never cleared (their stills with them); what
        is not kept is cleared after the number of days set in Settings. Returns: one line
        per clip."""
        return await batch_keep(self.cfg, ctx, "video", clips, let_go)

    @tool()
    async def look(
        self,
        ctx: ToolContext,
        clips: Annotated[list[str] | None, "The clips' short ids, like f5ef648c, from a "
                                           "batch's lines."] = None,
        batch: Annotated[str, "A batch id from generate: every clip it made, in "
                              "order."] = "",
        question: Annotated[str | None, "What you want to know, when your model cannot see "
                                        "images and another describes the stills."] = None,
    ) -> ToolResult:
        """Look at clips you made through their stills: for each clip its first frame,
        two in between and its last, in order, the way a picture's look shows it. Look
        before you say what a clip shows. Example: video_generator__look(
        clips=["f5ef648c"]). Returns: one line per clip and its stills, or their
        descriptions when your model cannot see images."""
        refs = [str(r).strip() for r in clips or () if str(r).strip()]
        lines: list[str] = []
        batch = (batch or "").strip()
        if batch:
            try:
                b = await batches_mod.find(ctx.store, "video", batch)
            except presets_mod.ImageError as e:
                lines.append(str(e))
            else:
                lines.append(settled_head(b, None))
                refs = [i["attachment"]["id"] for i in b["items"]
                        if i["state"] == "done" and i.get("attachment")] + refs
        elif not refs:
            return ToolResult(content="Give clips, by their short ids, or a batch id.",
                              is_error=True)
        sees = await attachments.turn_sees_images(self.cfg, ctx)
        llm = self.app.llm if self.app is not None else None
        shown: list[dict] = []
        done: set[str] = set()
        for ref in refs:
            got = await life.find(ctx.store, "video", ref)
            if isinstance(got, str):
                lines.append(got)
                continue
            if got["id"] in done:
                continue
            done.add(got["id"])
            row = await attachments.get(ctx.store, got["id"]) or {}
            if row.get("life") == "cleared":
                lines.append(attachments.cleared_line(self.cfg, "video", row["id"],
                                                      row["life_at"], ""))
                continue
            item = await batches_mod.clip_item(ctx.store, got["id"])
            stills = [f for f in (item or {}).get("frames") or []
                      if attachments.blob_path(self.cfg, f["sha256"]).exists()]
            n = len(done)
            if not stills:
                lines.append(f"{n}. clip {got['id'][:SHORT]}: its stills are no longer "
                             f"stored.")
                continue
            label, size = _label_size(item or {})
            lines.append(f"{n}. clip {got['id'][:SHORT]}: {label}, {size}, "
                         f"{len(stills)} stills, first to last: "
                         f"{(item or {}).get('prompt') or ''}".rstrip())
            for k, f in enumerate(stills, 1):
                shown.append({x: f[x] for x in ("id", "filename", "sha256", "media_type")})
                if not sees:
                    data = attachments.blob_path(self.cfg, f["sha256"]).read_bytes()
                    lines.append(f"   still {k}: " + await vision.describe(
                        llm, data, ctx.conversation_id, ctx.turn_id, question))
        if not shown:
            return ToolResult(content="\n".join(lines), is_error=True)
        return ToolResult(content="\n".join(lines),
                          display={"count": len(shown), **({"see": shown} if sees else {})})

    # ---- presets of its own (comfyui/proposals.py) ---------------------------------------

    @tool()
    async def read_preset(
        self,
        ctx: ToolContext,
        preset: Annotated[str, "A video preset id, like wan2.2-5b."],
        part: Annotated[int | None, PRESET_PART] = None,
    ) -> ToolResult:
        """Read a video preset as Settings' editor has it: its values, what each field is,
        and its workflow with its placeholders, the way propose_preset takes them.
        Example: video_generator__read_preset(preset="wan2.2-5b"). Returns: JSON, a long
        one in parts."""
        return await preset_read(self.cfg, ctx, "video", preset, part)

    @tool(open_world=True)
    async def look_up_node(
        self,
        ctx: ToolContext,
        node: Annotated[str, NODE] = "",
        search: Annotated[str, NODE_SEARCH] = "",
    ) -> ToolResult:
        """Look up a node of ComfyUI, for a workflow you write: one class's inputs with
        their types and choices and its outputs, or the classes that match some words.
        Example: video_generator__look_up_node(node="SaveVideo"). Returns: the node, or
        one line per class."""
        return await node_look_up(self.app, "video", node, search)

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
        """Propose a video preset of your own: a ComfyUI workflow with the app's
        placeholders ({{prompt}}, {{negative}}, {{seed}}, {{width:1280}}, {{height:704}},
        {{length:121}}, {{fps:24}}, {{image}} for a start picture) and its texts. Checked at
        once as Settings checks an uploaded workflow and an edit; then it waits until
        {{username}} approves it, and is saved as one of {{username}}'s presets, on, at the
        end of the Video generation list. With replaces, it replaces a preset you proposed
        before; a preset that came with the app or one {{username}} made is never replaced.
        Returns: the preset's id, or every problem, or why nothing was saved."""
        return await preset_propose(self.app, ctx, "video", label, workflow, values,
                                    replaces, why)
