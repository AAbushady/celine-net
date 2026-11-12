"""Music with ComfyUI: one batch per call (comfyui/batches.py), the same way as images.

The agent's call starts the batch in the app's task group and returns at once with one slot
per track; the batch fills the slots in place as each track is made. It is no job: only a
batch that had to wait for the GPU wakes it, with one notice from the app once it is done.
`wait` holds its turn for the end, or until the batch still waits for the GPU after
GPU_TRY seconds.
"""
from __future__ import annotations

from typing import Annotated, Required, TypedDict

from ..comfyui import batches as batches_mod, life, presets as presets_mod
from . import ToolContext, ToolResult, guide, tool, described
from .images import (LABEL, NODE, NODE_SEARCH, PRESET_PART, PROPOSE_WHY, REPLACES, REPORTS,
                     VALUES, WAIT, WORKFLOW, Generator, batch_generate, batch_keep,
                     batch_read, batch_stop, node_look_up, preset_propose, preset_read, took,
                     settled_head)

SHORT = batches_mod.SHORT


class Item(TypedDict, total=False):
    style: Required[Annotated[str, "Genre, instruments, tempo, space and mood, comma "
                                   "separated (the guide shows how)."]]
    lyrics: Annotated[str, "With section tags like [Verse 1] and [Chorus]. Empty for "
                           "an instrumental."]
    title: Annotated[str, "A short name for the track."]
    instrumental: Annotated[bool, "True (the default): no vocals. False: a song with "
                                  "the lyrics."]
    duration: Annotated[int, "The most the track may last, in seconds. Empty: the "
                             "preset's default."]


def item_line(i: dict, cfg, lives: bool = False) -> str:
    """One slot of a batch (as batches.view gives it), as it reads it; `lives`: as
    images.item_line."""
    n = i["index"] + 1
    if i["state"] == "failed":
        return f"{n}. failed: {i['error']}"
    if i["state"] == "stopped":
        return f"{n}. stopped before it was made."
    what = "instrumental" if i["instrumental"] else "song"
    if i["state"] != "done":
        return f"{n}. {i['title']} ({what}), seed {i['seed']}: {i.get('note') or i['state']}."
    lived = life.words(cfg, i["attachment"].get("life")) if lives else ""
    return (f"{n}. track {i['attachment']['id'][:SHORT]}: {i['title']} ({what}), seed "
            f"{i['seed']}, {took(i)}." + (f" {lived}" if lived else ""))


def batch_text(b: dict, cfg, *, started: bool, chat: bool = True,
               waited: float | None = None, lives: bool = False) -> str:
    """A batch as it reads it; started in a chat (`chat`), the user hears it there.
    `waited`: the seconds it waited for the GPU (images.settled_head)."""
    items = batches_mod.view(b)["items"]
    if started:
        hears = f" {cfg.user} hears each one in this conversation when it is done." if chat \
            else ""
        head = (f"Batch {b['id'][:SHORT]} started: {len(items)} track"
                f"{'s' if len(items) != 1 else ''}, made one after the other.{hears} "
                f"{REPORTS} music_generator__read shows the batch.")
    else:
        head = settled_head(b, waited)
    return "\n".join([head] + [item_line(i, cfg, lives) for i in items])


class MusicGenerator(Generator):
    name = "music_generator"
    guide = guide("music_generator")
    description = described()
    role = "music"

    def _text(self, b: dict, started: bool, chat: bool = True,
              waited: float | None = None, lives: bool = False) -> str:
        return batch_text(b, self.cfg, started=started, chat=chat, waited=waited,
                          lives=lives)

    @property
    def docs(self) -> dict[str, str]:
        """The first preset of the role, from its file, shown after the guide."""
        found = self._presets()
        return {"presets": presets_mod.docs(found[0], True)} if found else {}

    @tool(read_only=False, destructive=False, open_world=True, timeout=batches_mod.WAIT_MAX)
    async def generate(
        self,
        ctx: ToolContext,
        items: Annotated[list[Item], "The tracks, in order."],
        count: Annotated[int, "How many tracks of each item, each with its own seed. "
                              "Default 1."] = 1,
        wait: Annotated[bool, WAIT] = False,
    ) -> ToolResult:
        """Make music with ComfyUI: instrumental tracks, or songs with lyrics. Load the
        guide first: it has the style rules. The call returns at once with the batch id
        and one slot per track. The tracks are made one after the other and appear in
        this conversation as each is done; in a chat {{username}}
        hears them there. When the batch is done, one message from the app says what
        was made (none when wait=true already returned it done). music_generator__read
        shows a batch,
        music_generator__stop stops one. What you make is cleared after the number of
        days set in Settings unless music_generator__keep keeps it. Returns: the batch
        id and each slot; with wait=true, the batch when it is done, or as it is when it
        still waits for the GPU after a short try."""
        from .places import place_of
        chat = await ctx.store.read(lambda c: place_of(c, ctx.conversation_id)) == "chat"
        return await batch_generate(self.app, ctx, "music", items, count, wait,
                                    lambda b, s, waited=None: self._text(b, s, chat, waited))

    @tool()
    async def read(
        self,
        ctx: ToolContext,
        batch: Annotated[str, "The batch id from generate, like 3f2a9c1e."],
    ) -> ToolResult:
        """A batch's state: each slot, waiting, being made, made (with its track's
        short id and when it is cleared, or that it is kept), failed (with why) or
        stopped. Returns: one line per track."""
        return await batch_read(ctx, "music", batch, self._text)

    @tool(read_only=False, destructive=False)
    async def stop(
        self,
        ctx: ToolContext,
        batch: Annotated[str, "The batch id from generate, like 3f2a9c1e."],
    ) -> ToolResult:
        """Stop a batch: the track being made is interrupted, the ones after it are not
        made. What was made stays. Returns: the batch."""
        return await batch_stop(self.app, ctx, "music", batch, self._text)

    @tool(read_only=False, destructive=False)
    async def keep(
        self,
        ctx: ToolContext,
        tracks: Annotated[list[str], "The tracks' short ids, like f5ef648c, from a "
                                     "batch's lines."],
        let_go: Annotated[bool, "True: let them go, so they are cleared after their days "
                                "again. Default false: keep them."] = False,
    ) -> ToolResult:
        """Keep tracks you made, so they are never cleared; what is not kept is cleared
        after the number of days set in Settings. Returns: one line per track."""
        return await batch_keep(self.cfg, ctx, "music", tracks, let_go)

    # ---- presets of its own (comfyui/proposals.py) ---------------------------------------

    @tool()
    async def read_preset(
        self,
        ctx: ToolContext,
        preset: Annotated[str, "A music preset id, like yue2."],
        part: Annotated[int | None, PRESET_PART] = None,
    ) -> ToolResult:
        """Read a music preset as Settings' editor has it: its values, what each field
        is, and its workflow with its placeholders, the way propose_preset takes them.
        Example: music_generator__read_preset(preset="yue2"). Returns: JSON, a long one
        in parts."""
        return await preset_read(self.cfg, ctx, "music", preset, part)

    @tool(open_world=True)
    async def look_up_node(
        self,
        ctx: ToolContext,
        node: Annotated[str, NODE] = "",
        search: Annotated[str, NODE_SEARCH] = "",
    ) -> ToolResult:
        """Look up a node of ComfyUI, for a workflow you write: one class's inputs with
        their types and choices and its outputs, or the classes that match some words.
        Example: music_generator__look_up_node(node="SaveAudio"). Returns: the node, or
        one line per class."""
        return await node_look_up(self.app, "music", node, search)

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
        """Propose a music preset of your own: a ComfyUI workflow with the app's
        placeholders ({{style}}, {{lyrics}}, {{seed}}, {{duration:120}},
        {{instrumental:true}}) and its texts. Checked at once as Settings checks an
        uploaded workflow and an edit; then it waits until {{username}} approves it, and
        is saved as one of {{username}}'s presets, on, at the end of the Music generation
        list. With replaces, it replaces a preset you proposed before; a preset that came
        with the app or one {{username}} made is never replaced. Returns: the preset's
        id, or every problem, or why nothing was saved."""
        return await preset_propose(self.app, ctx, "music", label, workflow, values,
                                    replaces, why)
