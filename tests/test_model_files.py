"""Model files the agent finds and asks for (comfyui/model_files.py, hubs.py), and its own
presets (comfyui/proposals.py), against a stand-in comfy helper behind the router's
transport: a request refused at once with no card, the card's facts for Hugging Face and
Civitai, approve to ready with its notice and the trigger words, approve to failed,
denied, a restart in the middle, a ComfyUI that is not on demand; a search on both hubs
with its pages and its preset check, a listing with no words, a read in parts with a file
the helper does not take, the example pictures to a model that sees and one that does
not, the outside mark and the class; the preset tools (read, a node looked up while
ComfyUI is down, a proposal with problems, one approved and made by a batch, its
replacement, a shipped one and the user's refused); the page's routes, the Civitai key's
among them (to the helper, nothing of it kept), and the helper's two refusals about the key
by their code. Fake servers only, a fixed clock for the batches, waits on conditions."""
from __future__ import annotations

import itertools
import json
import logging

import anyio
import httpx
import pytest
from chat_fixtures import until
from settings_fixtures import client, scratch
from test_comfyui import OD_PROVIDERS, OD_ROLES, UPLOAD, Clock, Fake, Helper, _call, _notices, png

from celine import approvals as ap, attachments, vision
from celine.backends.llm import LLM
from celine.backends.router import Router
from celine.boot import load
from celine.comfyui import api as comfy_api, batches as batches_mod, hubs, model_files
from celine.comfyui import presets, proposals, server
from celine.conversations import store as conversations
from celine.core.bus import EventBus
from celine.core.services import Services
from celine.guard import facts as guard_facts, sources
from celine.guard.owner import _SEAL, Owner
from celine.skills import ToolResult, build as build_registry
from celine.skills.images import ImageGenerator
from celine.skills.music import MusicGenerator

pytestmark = pytest.mark.anyio
OWNER = Owner("test", _SEAL)
MB, GB = 1024 ** 2, 1024 ** 3
HF = {"hub": "huggingface", "repo": "MicksHF/Anime", "path": "fusal.safetensors"}
REV = "abc123def4567890"
HF_VALUE = f"hf:MicksHF/Anime@{REV}/fusal.safetensors"
WHY = "An anime style for the poster sam asked for."
KEY = ("Civitai gives this file only to a signed-in account, and the helper has no Civitai "
       "key. The owner sets one with `comfyd.py set-civitai-key`.")
KEY_REFUSED = ("Civitai refused the helper's key for this file. The owner sets another with "
               "`comfyd.py set-civitai-key`.")
CIVITAI_KEY = "civitai-made-up-key-5e1d"
CHECK_REFUSED = "Civitai refused this key; nothing was saved."
LOOKS = {
    "hf": {"source": {**HF, "revision": REV}, "name": "fusal.safetensors", "size": 420 * MB,
           "sha256": "a" * 64, "license": "apache-2.0", "about": None},
    222: {"source": {"hub": "civitai", "model": 111, "version": 222, "file": 333,
                     "page": "https://civitai.red/models/111?modelVersionId=222"},
          "name": "Ink Wash v2.safetensors", "size": 200 * MB, "sha256": "b" * 64,
          "license": "Use it and credit the maker.",
          "about": {"title": "Ink wash, v2", "type": "LORA", "base": "Qwen",
                    "words": ["inkwash style", "sumi-e"], "mature": True}},
    444: {"source": {"hub": "civitai", "model": 112, "version": 444, "file": 445},
          "name": "big.safetensors", "size": 6 * GB, "sha256": "c" * 64, "license": None,
          "about": {"title": "Big", "type": "Checkpoint", "base": "Qwen", "words": [],
                    "mature": False}},
    555: {"source": {"hub": "civitai", "model": 113, "version": 555, "file": 556},
          "name": "vae.safetensors", "size": 300 * MB, "sha256": "d" * 64, "license": None,
          "about": {"title": "A VAE", "type": "VAE", "base": "Qwen", "words": [],
                    "mature": False}},
}
RESULT = {"source": {"hub": "civitai", "version": 222}, "name": "Ink Wash v2.safetensors",
          "size": 200 * MB, "title": "Ink wash", "type": "LORA", "base": "Qwen",
          "words": ["inkwash style"], "mature": False, "downloads": 12345,
          "updated": "2026-11-01", "address": "https://civitai.red/models/111"}
MATURE = {**RESULT, "source": {"hub": "civitai", "version": 777, "file": 9},
          "title": "Night", "mature": True, "downloads": None, "words": []}
HF_RESULT = {**RESULT, "source": {**HF, "revision": REV}, "name": "fusal.safetensors",
             "title": "MicksHF/Anime", "type": "LoRA", "words": [], "downloads": 40}
READ = {"title": "Ink wash", "type": "LORA", "base": "Qwen", "creator": "someone",
        "address": "https://civitai.red/models/111", "tags": ["style", "ink"],
        "license": "Use it and credit the maker.", "mature": False, "downloads": 12345,
        "updated": "2026-11-01", "description": "Soft ink on rice paper.",
        "versions": [{"name": "v2", "base": "Qwen", "words": ["inkwash style"],
                      "published": "2026-10-01", "files": [
                          {"source": {"hub": "civitai", "version": 222, "file": 333},
                           "name": "Ink Wash v2.safetensors", "size": 200 * MB,
                           "takes": True},
                          {"source": {"hub": "civitai", "version": 222, "file": 334},
                           "name": "Ink Wash v2.pt", "size": 200 * MB, "takes": False,
                           "why": "Only a .safetensors file is taken."}]}],
        "examples": [{"base": "SD 1.5" if n == 2 else "Qwen" if n == 1 else None,
                      "prompt": f"ink wash, a heron {n}", "negative": "blurry",
                      "settings": {"steps": 8, "cfg": 1.0},
                      "picture": {"id": f"p{n}", "width": 832, "height": 1216,
                                  "mature": n == 3}} for n in range(1, 7)]}


class Stand(Helper):
    """The comfy helper at helper:8791 with its model files (plan 7.2 to 7.7) beside its
    leases. `script`: what each look at a job says, in order, the last one for good."""

    def __init__(self, fake: Fake):
        super().__init__(fake)
        self.script = ["fetching", "listing", "ready"]
        self.fail_why = "The checksum did not match"
        self.fail_code: str | None = None   # a failed job's code (the Civitai key's)
        self.files = [{"path": "loras/qwen2.1/old.safetensors", "size": 5 * MB,
                       "requested": None}]
        self.free = 500 * GB
        self.jobs: dict[str, dict] = {}
        self.posts: list[dict] = []
        self.looks: list[dict] = []
        self.searches: list[dict] = []
        self.comfy_down = False
        self.read = READ
        self.civitai: str | None = None     # the Civitai key it keeps
        self.refuse = {"p2": "Example 2 is a video, which is not taken."}
        self.unknown = {"Nope"}         # node classes ComfyUI has not
        self.nodes = {"KSampler": {"input": {"required": {
            "seed": ["INT", {"default": 0, "min": 0, "max": 99}],
            "sampler_name": [[f"s{n}" for n in range(30)], {}]}},
            "output": ["LATENT"], "output_name": ["LATENT"], "display_name": "KSampler",
            "category": "sampling"},
            "LoraLoaderModelOnly": {"input": {"required": {}}, "output": ["MODEL"],
                                    "display_name": "LoraLoaderModelOnly",
                                    "category": "loaders"}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "helper":
            return self.fake(request)
        path, method = request.url.path, request.method
        if path.startswith("/models"):
            self.log.append(f"{method} {path}")
            if self.down:
                raise httpx.ConnectError("refused", request=request)
            body = json.loads(request.content) if request.content else {}
            return self._models(path, method, body)
        if path == "/keys/civitai":
            self.log.append(f"{method} {path}")
            if method == "PUT":
                key = json.loads(request.content)["key"]
                if " " in key:
                    return httpx.Response(400, json={"error": "That is not an API key (it has "
                                                              "spaces); nothing changed."})
                if key.startswith("wrong"):         # Civitai refuses it
                    return httpx.Response(403, json={"error": CHECK_REFUSED,
                                                     "code": "civitai_key_refused"})
                self.civitai = key
                return httpx.Response(200, json={"set": True, "checked": True})
            if method == "DELETE":
                self.civitai = None
            return httpx.Response(200, json={"set": self.civitai is not None})
        if path == "/object_info":
            return httpx.Response(200, json=self.nodes)
        if path.startswith("/object_info/") and path.rsplit("/", 1)[1] in self.nodes:
            cls = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={cls: self.nodes[cls]})
        if path.startswith("/object_info/") and path.rsplit("/", 1)[1] in self.unknown:
            return httpx.Response(200, json={})
        if self.comfy_down and not path.startswith(("/leases", "/object_info")):
            return httpx.Response(503, json={"error": "ComfyUI is not running."})
        return super().__call__(request)

    def _models(self, path: str, method: str, body: dict) -> httpx.Response:
        if path == "/models/look":
            src = body["source"]
            self.looks.append(src)
            if src.get("hub") == "huggingface":
                if (src.get("repo"), src.get("path")) == (HF["repo"], HF["path"]):
                    return httpx.Response(200, json=LOOKS["hf"])
                return httpx.Response(404, json={"error": "There is no such file."})
            if src.get("version") == 666:
                return httpx.Response(403, json={"error": KEY, "code": "civitai_key_missing"})
            if src.get("version") == 667:
                return httpx.Response(403, json={"error": KEY_REFUSED,
                                                 "code": "civitai_key_refused"})
            if src.get("version") in LOOKS:
                return httpx.Response(200, json=LOOKS[src["version"]])
            return httpx.Response(404, json={"error": f"Civitai has no version "
                                                      f"{src.get('version')}."})
        if path == "/models" and method == "POST":
            self.posts.append(body)
            jid = f"J{len(self.posts)}"
            size = next(x["size"] for x in LOOKS.values() if x["sha256"] == body["sha256"])
            self.jobs[jid] = {"id": jid, "state": "fetching", "why": None, "done": 0,
                              "size": size, "saved_as": body["saved_as"], "body": body}
            return httpx.Response(200, json={k: v for k, v in self.jobs[jid].items()
                                             if k != "body"})
        if path.startswith("/models/jobs/"):
            job = self.jobs.get(path.rsplit("/", 1)[1])
            if job is None:
                return httpx.Response(404, json={"error": "There is no such job."})
            state = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            job.update(state=state, done=job["size"] // 2 if state == "fetching"
                       else job["size"], why=self.fail_why if state == "failed" else None,
                       code=self.fail_code if state == "failed" else None)
            if state == "ready" and all(f["path"] != job["saved_as"] for f in self.files):
                b = job["body"]
                look = next(x for x in LOOKS.values() if x["sha256"] == b["sha256"])
                self.files.append({"path": job["saved_as"], "size": job["size"],
                                   "requested": {"source": b["source"],
                                                 "about": look["about"],
                                                 "holder": b["holder"],
                                                 "at": 1794391200.0}})
            return httpx.Response(200, json={k: v for k, v in job.items() if k != "body"})
        if path == "/models" and method == "GET":
            return httpx.Response(200, json={"files": self.files, "free": self.free,
                                             "limit": 100 * GB})
        if path.startswith("/models/files/") and method == "DELETE":
            rel = path.removeprefix("/models/files/")
            f = next((x for x in self.files if x["path"] == rel), None)
            if f is None:
                return httpx.Response(404, json={"error": "There is no such file."})
            if f["requested"] is None:
                return httpx.Response(409, json={"error": "This file came with the set; it "
                                                          "is not removed this way."})
            self.files.remove(f)
            return httpx.Response(200, json={"removed": True})
        if path == "/models/search":
            self.searches.append(body)
            if body["hub"] == "huggingface":
                return httpx.Response(200, json={"results": [HF_RESULT], "next": None})
            if body.get("after") == "p2":
                return httpx.Response(200, json={"results": [MATURE], "next": None})
            return httpx.Response(200, json={"results": [RESULT, MATURE], "next": "p2"})
        if path == "/models/read":
            return httpx.Response(200, json=self.read)
        if path == "/models/picture":
            if body["id"] in self.refuse:
                return httpx.Response(422, json={"error": self.refuse[body["id"]]})
            return httpx.Response(200, content=png(64, 96),
                                  headers={"content-type": "image/png"})
        return httpx.Response(404, json={"error": "No such route."})


@pytest.fixture
async def hub(tmp_path, monkeypatch, store):
    """The app with its ComfyUI behind the stand-in helper, its key, an intercom, the
    approvals and the model files' service."""
    from celine.intercom import Intercom
    from celine.settings.secrets import set_key
    scratch(tmp_path, monkeypatch, providers=OD_PROVIDERS, roles=OD_ROLES)
    monkeypatch.setattr(model_files, "FOLLOW", 0.01)
    for name in ("POLL", "LEASE_POLL", "LEASE_RENEW"):
        monkeypatch.setattr(server, name, 0.01)
    monkeypatch.setattr(batches_mod, "clock", Clock())
    proposals._infos.clear()
    cfg = load()
    cfg.user = "sam"
    set_key(cfg.settings.secrets_root, "od", "k3y")
    fake = Fake()
    helper = Stand(fake)
    llm = LLM(cfg, router=Router(cfg, transport=httpx.MockTransport(helper)))
    async with anyio.create_task_group() as tg:
        services = Services(cfg=cfg, store=store, llm=llm, bus=EventBus())
        services.intercom = Intercom(store)
        services.approvals = ap.Approvals(cfg, store=store)
        services.batches = batches_mod.Batches(services, tg)
        services.model_files = model_files.ModelFiles(services, tg)
        services.registry = build_registry(services)
        services.helper, services.fake, services.tg = helper, fake, tg
        yield services
        tg.cancel_scope.cancel()


def _images(app) -> ImageGenerator:
    skill = ImageGenerator(app.cfg)
    skill.app = app
    return skill


_CALLS = itertools.count(1)


async def _ctx(app, name="image_generator__request_model"):
    """A call of `name` in a new conversation, its id one of its own."""
    ctx, _ = await _call(app.store, await conversations.create(app.store),
                         f"c{next(_CALLS)}", name)
    return ctx


async def _card(app) -> dict:
    await until(lambda: app.approvals.waiting)
    return app.approvals.view()[0]


def _answer(app, ok: bool) -> None:
    ask = next(iter(app.approvals.waiting.values()))
    assert app.approvals.decide(ask.id, ok, OWNER)


async def _row(app) -> dict:
    return await app.store.read(lambda c: [dict(r) for r in c.execute(
        "SELECT * FROM model_requests ORDER BY created_at")][-1])


async def _notice_for(app, cid: str) -> str:
    async def landed() -> bool:
        return any(to == f"agent:{cid}" for to, _ in await _notices(app.store))
    await until(landed)
    [text] = [t for to, t in await _notices(app.store) if to == f"agent:{cid}"]
    return text


# ---- a request, refused at once ---------------------------------------------------------

@pytest.mark.parametrize("args, said", [
    ({"preset": "anima-turbo"}, "Anima turbo takes no LoRAs."),
    ({"preset": "nope"}, "There is no image preset 'nope': list_presets lists them."),
    ({"file": "hf:MicksHF/Anime"}, "hf:MicksHF/Anime names a repo and no file: add the file's "
                                   "path in it, like hf:MicksHF/Anime/name.safetensors."),
    ({"file": "hf:MicksHF/Anime/other.safetensors"}, "There is no such file."),
    ({"file": "https://civitai.red/models/111"},
     "This address names no version: give the address with modelVersionId in it, from the "
     "version's own page, or civitai:<version id>."),
    ({"file": "https://example.com/models/111?modelVersionId=222"},
     "example.com is neither Civitai nor Hugging Face: give a file value, an address on "
     "civitai.red, civitai.com or huggingface.co, or owner/name/path for Hugging Face."),
    ({"file": "civitai:666"}, "This file needs a Civitai key, which sam sets in Settings, "
                              "Media."),
    ({"file": "civitai:667"}, "Civitai refused the key set in Settings, Media for "
                              "this file."),
    ({"file": "civitai:555"}, "Civitai lists this file as VAE: only a LoRA or a checkpoint "
                              "is taken."),
    ({"file": "civitai:444"}, "Civitai lists this file as a Checkpoint, so it goes where a "
                              "preset takes models: ask with kind=\"model\"."),
    ({"file": "https://civitai.com/models/999?modelVersionId=222"},
     "Version 222 belongs to model 111, not to model 999 of the address."),
    ({"file": "civitai:222", "why": "style"},
     "Nothing was asked: say why in `why`, one real sentence sam can judge (what the file "
     "is for)."),
])
async def test_a_request_the_tool_or_the_look_refuses_makes_no_card(hub, args, said):
    a = {"preset": "qwen2.1-smart", "kind": "lora", "file": "civitai:222", "why": WHY, **args}
    r = await _images(hub).request_model(await _ctx(hub), **a)
    assert (r.is_error, r.content) == (True, said)
    assert hub.approvals.waiting == {} and hub.helper.posts == []
    assert await hub.store.read(lambda c: c.execute(
        "SELECT count(*) FROM model_requests").fetchone()[0]) == 0


async def test_a_file_that_is_there_or_asked_for_already_makes_no_second_card(hub):
    hub.helper.files.append({"path": "loras/qwen2.1/fusal.safetensors", "size": 1,
                             "requested": None})
    r = await _images(hub).request_model(await _ctx(hub), preset="qwen2.1-smart",
                                         kind="lora", file=HF_VALUE, why=WHY)
    assert r.content == ("loras/qwen2.1/fusal.safetensors is there already: give "
                         "qwen2.1/fusal in loras with the preset qwen2.1-smart.")
    hub.helper.files.pop()
    first = await _images(hub).request_model(await _ctx(hub), preset="qwen2.1-smart",
                                             kind="lora", file=HF_VALUE, why=WHY)
    assert not first.is_error
    again = await _images(hub).request_model(await _ctx(hub), preset="qwen2.1-smart",
                                             kind="lora", file=HF_VALUE, why=WHY)
    assert again.content == "qwen2.1/fusal was asked for already and waits for sam."
    assert len(hub.approvals.waiting) == 1


async def test_a_comfyui_that_is_not_on_demand_takes_no_model_files(tmp_path, monkeypatch,
                                                                    store):
    from test_comfyui import PROVIDERS, ROLES
    scratch(tmp_path, monkeypatch, providers=PROVIDERS, roles=ROLES)
    cfg = load()
    llm = LLM(cfg, router=Router(cfg, transport=httpx.MockTransport(Fake())))
    async with anyio.create_task_group() as tg:
        app = Services(cfg=cfg, store=store, llm=llm, bus=EventBus())
        app.approvals = ap.Approvals(cfg, store=store)
        app.model_files = model_files.ModelFiles(app, tg)
        r = await _images(app).request_model(await _ctx(app), preset="krea2-turbo",
                                             kind="lora", file="civitai:222", why=WHY)
        assert r.content == ("ComfyUI takes no model files from the app: only a ComfyUI that "
                             "starts on demand, behind the comfy helper, does.")
        r = await _images(app).search_models(await _ctx(app), hub="civitai", kind="lora")
        assert r.is_error and "takes no model files" in r.content
        tg.cancel_scope.cancel()


# ---- a request, asked and followed -------------------------------------------------------

async def test_a_hugging_face_file_is_asked_and_its_card_holds_the_looks_facts(hub):
    ctx = await _ctx(hub)
    r = await _images(hub).request_model(ctx, preset="qwen2.1-smart", kind="lora",
                                         file=HF_VALUE, why=WHY)
    assert r.content == ("Asked sam for the LoRA qwen2.1/fusal for Qwen 2.1: "
                         "fusal.safetensors, 420 MB, from huggingface.co. It waits for sam's "
                         "answer; a message from the app says when qwen2.1/fusal is ready "
                         "to use, or why not.")
    assert r.display["model_file"]["state"] == "asked"
    assert hub.helper.looks == [{**HF, "revision": REV}], "the value goes to the look as it is"
    card = await _card(hub)
    assert card["kind"] == "model_file" and card["tool_call_id"] == ctx.tool_call_id
    assert card["title"] == f"{hub.cfg.name} asks to download the LoRA qwen2.1/fusal for Qwen 2.1."
    assert card["facts"] == [
        {"label": "File", "value": "fusal.safetensors"},
        {"label": "Source", "value": "huggingface.co/MicksHF/Anime at abc123def456",
         "href": f"https://huggingface.co/MicksHF/Anime/blob/{REV}/fusal.safetensors"},
        {"label": "Size", "value": "420 MB"}, {"label": "Licence", "value": "apache-2.0"},
        {"label": "Saved as", "value": "loras/qwen2.1/fusal.safetensors"},
        {"label": "Preset", "value": "Qwen 2.1"},
        {"label": "Free space after", "value": hubs.size_words(500 * GB - 420 * MB)},
        {"label": "Reason", "value": WHY}]
    assert card["help"].startswith("A model file comes from outside your network")


async def test_approved_it_downloads_and_the_agent_hears_the_id_and_trigger_words(hub):
    ctx = await _ctx(hub)
    url = "https://civitai.red/models/111?modelVersionId=222"
    r = await _images(hub).request_model(ctx, preset="qwen2.1-smart", kind="lora", file=url,
                                         why=WHY)
    assert not r.is_error, r.content
    card = await _card(hub)
    facts = {f["label"]: f for f in card["facts"]}
    assert [f["label"] for f in card["facts"]] == [
        "File", "Site", "Title", "Type", "Base model", "Trigger words", "Mature", "Page",
        "Size", "Licence", "Saved as", "Preset", "Free space after", "Reason"]
    assert facts["Site"]["value"] == "civitai.red" and facts["Mature"]["value"] == "Yes"
    assert facts["Trigger words"]["value"] == "inkwash style, sumi-e"
    assert facts["Page"] == {"label": "Page", "value": url, "href": url}
    assert facts["Saved as"]["value"] == "loras/qwen2.1/Ink_Wash_v2.safetensors"
    _answer(hub, True)
    text = await _notice_for(hub, ctx.conversation_id)
    assert text == ("The LoRA qwen2.1/Ink_Wash_v2 for Qwen 2.1 is ready: give "
                    "qwen2.1/Ink_Wash_v2 in loras, as list_presets now lists it, and write "
                    "its trigger words inkwash style, sumi-e into the prompt.")
    [post] = hub.helper.posts
    row = await _row(hub)
    assert post == {"source": LOOKS[222]["source"], "sha256": "b" * 64,
                    "saved_as": "loras/qwen2.1/Ink_Wash_v2.safetensors", "holder": row["id"]}
    assert (row["state"], row["done"], row["error"]) == ("ready", 200 * MB, None)
    shown = await hub.store.read(lambda c: json.loads(c.execute(
        "SELECT display FROM tool_calls WHERE id=?", (ctx.tool_call_id,)).fetchone()[0]))
    assert shown["model_file"]["display"] == {"words": "Ready", "tone": "success"}


async def test_a_download_that_failed_says_the_helpers_sentence(hub):
    hub.helper.script = ["fetching", "failed"]
    ctx = await _ctx(hub)
    await _images(hub).request_model(ctx, preset="krea2-turbo", kind="model",
                                     file="civitai:444", why=WHY)
    await _card(hub)
    _answer(hub, True)
    text = await _notice_for(hub, ctx.conversation_id)
    assert text == ("The model krea2/big was not downloaded: The checksum did not match.")
    assert (await _row(hub))["state"] == "failed"


@pytest.mark.parametrize("code, said", [
    ("civitai_key_missing", "This file needs a Civitai key, which sam sets in Settings, "
                            "Media."),
    ("civitai_key_refused", "Civitai refused the key set in Settings, Media for "
                            "this file.")])
async def test_a_download_that_failed_on_the_civitai_key_says_where_the_key_is_set(
        hub, code, said):
    hub.helper.script = ["fetching", "failed"]
    hub.helper.fail_why, hub.helper.fail_code = KEY, code
    ctx = await _ctx(hub)
    await _images(hub).request_model(ctx, preset="krea2-turbo", kind="model",
                                     file="civitai:444", why=WHY)
    await _card(hub)
    _answer(hub, True)
    assert await _notice_for(hub, ctx.conversation_id) == (
        f"The model krea2/big was not downloaded: {said}")
    assert (await _row(hub))["error"] == said


async def test_a_denied_request_downloads_nothing_and_says_so(hub):
    ctx = await _ctx(hub)
    await _images(hub).request_model(ctx, preset="qwen2.1-smart", kind="lora", file=HF_VALUE,
                                     why=WHY)
    await _card(hub)
    _answer(hub, False)
    text = await _notice_for(hub, ctx.conversation_id)
    assert text == ("The request for the LoRA qwen2.1/fusal is over: sam denied it; nothing "
                    "was done.")
    assert hub.helper.posts == [] and (await _row(hub))["state"] == "denied"


async def test_a_restart_follows_the_download_again_and_closes_an_open_card(hub):
    hub.helper.script = ["fetching"]
    ctx = await _ctx(hub)
    async with anyio.create_task_group() as g:
        hub.model_files = model_files.ModelFiles(hub, g)
        await _images(hub).request_model(ctx, preset="qwen2.1-smart", kind="lora",
                                         file=HF_VALUE, why=WHY)
        await _card(hub)
        _answer(hub, True)

        async def fetching() -> bool:
            r = await _row(hub)
            return r["state"] == "fetching" and r["done"] == 210 * MB
        await until(fetching)
        g.cancel_scope.cancel()                 # the app stops; the helper goes on
    hub.helper.script = ["ready"]
    again = model_files.ModelFiles(hub, hub.tg)
    await again.resume()
    text = await _notice_for(hub, ctx.conversation_id)
    assert text.startswith("The LoRA qwen2.1/fusal for Qwen 2.1 is ready")
    assert len(hub.helper.posts) == 1, "the job it had is followed, not asked again"

    other = await _ctx(hub)
    async with anyio.create_task_group() as g:
        hub.model_files = model_files.ModelFiles(hub, g)
        await _images(hub).request_model(other, preset="krea2-turbo", kind="model",
                                         file="civitai:444", why=WHY)
        await _card(hub)
        g.cancel_scope.cancel()                 # a restart while the card is open
    assert hub.approvals.waiting == {}
    await model_files.ModelFiles(hub, hub.tg).resume()
    assert await _notice_for(hub, other.conversation_id) == (
        "The request for the model krea2/big is over: sam did not answer before a restart "
        "closed the request; nothing was done.")


# ---- looking around a hub ---------------------------------------------------------------

async def test_a_search_on_each_hub_pages_and_its_value_goes_to_the_request_unchanged(hub):
    skill, ctx = _images(hub), await _ctx(hub, "image_generator__search_models")
    r = await skill.search_models(ctx, hub="civitai", kind="lora", query="ink wash",
                                  base="Qwen")
    assert r.content.splitlines() == [
        "Civitai, LoRAs for \"ink wash\", base \"Qwen\":",
        "1. Ink wash (LORA, Qwen, 200 MB), 12,345 downloads; trigger words: inkwash style. "
        "file: civitai:222",
        "2. Night (LORA, Qwen, 200 MB), marked mature. file: civitai:777/9",
        "More: image_generator__search_models(hub=\"civitai\", kind=\"lora\", query=\"ink "
        "wash\", base=\"Qwen\", after=\"p2\")"]
    assert hub.helper.searches[-1] == {"hub": "civitai", "kind": "lora", "limit": hubs.PAGE,
                                       "query": "ink wash", "base": "Qwen"}
    assert r.display["class"] == "nsfw", "a page with a mature result"
    r = await skill.search_models(ctx, hub="civitai", kind="lora", query="ink wash",
                                  base="Qwen", after="p2")
    assert hub.helper.searches[-1]["after"] == "p2" and "More:" not in r.content
    r = await skill.search_models(ctx, hub="huggingface", kind="lora", query="anime")
    assert r.content.splitlines()[1].endswith(f"file: {HF_VALUE}")
    assert r.display["class"] == "public"
    value = r.content.splitlines()[1].rsplit("file: ", 1)[1]
    got = await skill.request_model(await _ctx(hub), preset="qwen2.1-smart", kind="lora",
                                    file=value, why=WHY)
    assert not got.is_error and hub.helper.looks[-1] == HF_RESULT["source"]


def test_an_empty_search_page_still_gives_the_call_for_the_next_one():
    args = {"hub": "civitai", "kind": "lora", "query": "", "sort": "newest"}
    assert hubs.search_text({"results": [], "next": "p2"}, "civitai", "lora",
                            args).splitlines() == [
        "Civitai, LoRAs, newest first: nothing on this page.",
        "More: image_generator__search_models(hub=\"civitai\", kind=\"lora\", "
        "sort=\"newest\", after=\"p2\")"]
    assert hubs.search_text({"results": []}, "civitai", "lora", args) == \
        "Civitai, LoRAs, newest first: nothing found."


async def test_a_listing_with_no_words_and_a_preset_that_takes_no_such_file(hub):
    skill, ctx = _images(hub), await _ctx(hub, "image_generator__search_models")
    r = await skill.search_models(ctx, hub="civitai", kind="lora", sort="most_downloaded",
                                  period="week")
    assert r.content.startswith("Civitai, LoRAs, most downloaded first, the last week:")
    assert hub.helper.searches[-1] == {"hub": "civitai", "kind": "lora", "limit": hubs.PAGE,
                                       "sort": "most_downloaded", "period": "week"}
    r = await skill.search_models(ctx, hub="civitai", kind="model", preset="qwen2.1-smart")
    assert (r.is_error, r.content) == (True, "Qwen 2.1 takes no model choice.")
    assert len(hub.helper.searches) == 1, "the preset is checked before the hub is asked"


async def test_a_preset_keeps_a_civitai_search_to_its_base(hub):
    """preset= only checked that the preset takes LoRAs: eight of ten results were of other
    bases, which the preset cannot load (#52)."""
    skill, ctx = _images(hub), await _ctx(hub, "image_generator__search_models")
    r = await skill.search_models(ctx, hub="civitai", kind="lora", preset="qwen2.1-smart",
                                  sort="most_downloaded")
    assert hub.helper.searches[-1]["base"] == "Qwen 2.1"
    assert r.content.startswith(
        "Civitai, LoRAs, base \"Qwen 2.1\", most downloaded first:\n1. Ink wash")
    await skill.search_models(ctx, hub="civitai", kind="lora", preset="qwen2.1-smart",
                              base="Qwen")
    assert hub.helper.searches[-1]["base"] == "Qwen", "a base given goes before the preset's"
    r = await skill.search_models(ctx, hub="huggingface", kind="lora",
                                  preset="qwen2.1-smart")
    assert "base" not in hub.helper.searches[-1]
    assert r.content.splitlines()[1] == (
        "Files of every base: Qwen 2.1 names no base for Hugging Face. Give base, as "
        "Hugging Face names it, to keep to its own.")


async def test_each_example_says_the_base_it_was_made_with_and_its_version(hub, monkeypatch):
    """An example made with another base looked like what the file does (#52)."""
    skill = _images(hub)
    r = await skill.read_model(await _ctx(hub, "image_generator__read_model"),
                               model="civitai:222")
    assert "Example prompts posted to version v2 (Qwen) (look_examples shows" in r.content
    assert "2. prompt: ink wash, a heron 2 (has a picture)\n   made with: SD 1.5\n" in r.content
    assert "3. prompt: ink wash, a heron 3 (has a picture)\n   negative:" in r.content
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    r = await skill.look_examples(await _ctx(hub, "image_generator__look_examples"),
                                  model="civitai:222", examples=[1])
    assert r.content.startswith("Example pictures posted to version v2 (Qwen) from "
                                "civitai.red")
    assert "1. prompt: ink wash, a heron 1 (has a picture)\n   made with: Qwen\n" in r.content


async def test_a_read_comes_in_parts_and_a_file_the_helper_does_not_take_has_no_value(hub):
    hub.helper.read = {**READ, "description": "\n\n".join(
        f"Paragraph {n}: " + "soft ink " * 300 for n in range(30))}
    skill, ctx = _images(hub), await _ctx(hub, "image_generator__read_model")
    r = await skill.read_model(ctx, model="civitai:222")
    lines = r.content.splitlines()
    assert lines[0] == hubs.HEAD.format(site="civitai.red")
    assert "  - Ink Wash v2.safetensors, 200 MB: file: civitai:222/333" in lines
    assert "  - Ink Wash v2.pt, 200 MB: not taken: Only a .safetensors file is taken." in lines
    assert "part 1 of" in r.content and "image_generator__read_model(model=\"civitai:222\", " \
                                       "part=2)" in r.content
    last = await skill.read_model(ctx, model="civitai:222", part=99)
    assert "1. prompt: ink wash, a heron 1 (has a picture)" in last.content
    assert r.display == {"hub": "civitai", "class": "public"}


async def test_what_a_hub_sends_is_marked_as_outside_like_a_web_search(hub):
    for name, args in (("web__search", {"query": "x"}),
                       ("image_generator__search_models", {"hub": "civitai"}),
                       ("image_generator__read_model", {"model": "civitai:222"}),
                       ("image_generator__look_examples", {"model": "civitai:222"})):
        got = sources.label(name, args, ToolResult(content="x", display={}))
        assert got is not None and got["by"] is None, name
    assert sources.label("image_generator__search_models", {"hub": "civitai"},
                         ToolResult(content="x"))["source"] == "civitai search"
    assert sources.label("image_generator__generate", {}, ToolResult(content="x")) is None
    assert guard_facts.result_class("image_generator__search_models", {}, None) == "public"
    assert guard_facts.result_class("image_generator__look_examples", {"class": "nsfw"},
                                    None) == "nsfw"
    assert not sources.never("image_generator__request_model")
    assert sources.never("image_generator__generate")
    assert guard_facts.sink({"name": "image_generator__request_model",
                             "args": {"file": "civitai:222"}}) == ("public", ["civitai:222"])


async def test_example_pictures_reach_a_model_that_sees_and_are_described_to_one_that_does_not(
        hub, monkeypatch):
    skill, ctx = _images(hub), await _ctx(hub, "image_generator__look_examples")
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    r = await skill.look_examples(ctx, model="civitai:222")
    assert len(r.images) == 3, "four asked, the second refused"
    assert "Picture 2: Example 2 is a video, which is not taken." in r.content
    assert r.content.count(" follows.") == 3 and "1. prompt: ink wash, a heron 1" in r.content
    assert r.display == {"hub": "civitai", "count": 3, "class": "nsfw"}, "the third is mature"
    rows = await hub.store.read(lambda c: [dict(x) for x in c.execute(
        "SELECT tool_call_id, message_id, batch_id FROM attachments")])
    assert rows == [{"tool_call_id": ctx.tool_call_id, "message_id": None,
                     "batch_id": None}] * 3

    r = await skill.look_examples(ctx, model="civitai:222", examples=[1, 2, 3, 4, 5])
    assert (r.is_error, r.content) == (True, "Look at most 4 examples at once.")
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(False))

    async def described(llm, data, cid, tid, question=None):
        return "Description by eyes: a heron in ink."
    monkeypatch.setattr(vision, "describe", described)
    r = await skill.look_examples(ctx, model="civitai:222", examples=[4])
    assert r.images is None and r.display["class"] == "public"
    assert "Picture 4: Description by eyes: a heron in ink." in r.content


def _sees(yes: bool):
    async def sees(cfg, ctx) -> bool:
        return yes
    return sees


# ---- presets of its own -----------------------------------------------------------------

async def _propose(app, ok: bool | None, **kw):
    """propose_preset, its card answered `ok` (None: none comes) -> (result, card)."""
    out: dict = {}
    skill = _images(app)
    ctx = await _ctx(app, "image_generator__propose_preset")

    async def run():
        out["r"] = await skill.propose_preset(ctx, **{"label": "Ink wash", "workflow": UPLOAD,
                                                      "why": "A preset for ink paintings.",
                                                      **kw})
    async with anyio.create_task_group() as tg:
        tg.start_soon(run)
        if ok is not None:
            out["card"] = await _card(app)
            _answer(app, ok)
    return out["r"], out.get("card")


async def test_read_preset_gives_the_editors_values_and_the_workflow_with_placeholders(hub):
    r = await _images(hub).read_preset(await _ctx(hub, "image_generator__read_preset"),
                                       preset="krea2-turbo")
    head, body = r.content.split("\n", 1)
    assert head == ("Krea 2 turbo (`krea2-turbo`), new images: it came with the app. Its "
                    "label, values and workflow below are what propose_preset takes.")
    got = json.loads(body)
    assert got["values"]["default.mode"] == "direct" and "label" not in got["values"]
    assert got["workflow"] == presets.with_placeholders(presets.of("images")["krea2-turbo"])
    r = await MusicGenerator(hub.cfg).read_preset(await _ctx(hub), preset="krea2-turbo")
    assert r.is_error and r.content.startswith("There is no music preset 'krea2-turbo'.")


async def test_a_node_is_looked_up_while_comfyui_is_down(hub):
    hub.helper.comfy_down = True
    skill, ctx = _images(hub), await _ctx(hub, "image_generator__look_up_node")
    r = await skill.look_up_node(ctx, node="KSampler")
    assert r.content.splitlines() == [
        "KSampler, category sampling.", "Inputs, required:",
        "- seed: INT (default 0, 0 to 99)",
        "- sampler_name: one of " + ", ".join(f"s{n}" for n in range(20)) + " and 10 more",
        "Outputs, by their index in a link [node, index]: 0 LATENT."]
    r = await skill.look_up_node(ctx, search="lora loader")
    assert r.content == "- LoraLoaderModelOnly (loaders)"
    r = await skill.look_up_node(ctx, node="Nope")
    assert r.is_error and r.content.startswith("ComfyUI has no node Nope")


async def test_a_proposal_with_problems_comes_back_with_every_sentence_and_no_card(hub):
    wf = json.loads(json.dumps(UPLOAD))
    wf["3"]["inputs"]["seed"] = 1
    r, card = await _propose(hub, None, workflow=wf, values={"size.step": -1})
    assert r.is_error and card is None and hub.approvals.waiting == {}
    assert r.content == ("The preset was not proposed: {{seed}} is missing: put it as the "
                         "value of the input that takes the seed.")
    r, _ = await _propose(hub, None, values={"size.step": -1})
    assert r.content == "The preset was not proposed: A size is a number above 0."


async def test_an_approved_proposal_is_the_users_own_on_at_the_end_and_a_batch_makes_it(hub):
    hub.helper.unknown = {"CheckpointLoaderSimple"}
    r, card = await _propose(hub, True, values={"good_at": "Ink paintings."})
    assert not r.is_error, r.content
    assert card["kind"] == "preset"
    assert card["title"] == f"{hub.cfg.name} asks to add the preset Ink wash."
    assert card["facts"][:5] == [
        {"label": "Name", "value": "Ink wash"}, {"label": "Task", "value": "New images"},
        {"label": "Nodes", "value": "CLIPTextEncode, CheckpointLoaderSimple, "
                                    "EmptyLatentImage, KSampler, SaveImage"},
        {"label": "Parameters", "value": "prompt, width, height, seed"},
        {"label": "Missing on this ComfyUI", "value": "The preset Ink wash needs the node "
                                                      "CheckpointLoaderSimple, which ComfyUI "
                                                      "does not have."}]
    assert card["detail"]["title"] == "Workflow"
    assert json.loads(card["detail"]["text"])["1"]["inputs"]["text"] == "{{prompt}}"
    assert r.content == ("Added the preset Ink wash (`ink-wash`) at the end of the Image "
                         "generation list: image_generator__generate takes it as "
                         "preset=\"ink-wash\".")
    p = presets.of("images")["ink-wash"]
    assert (p.origin, p.proposed, p.good_at) == ("own", True, "Ink paintings.")
    assert hub.cfg.settings.chain("image_generation")[-1] == "od/ink-wash"
    hub.helper.unknown = set()
    b = await hub.batches.create("images", [{"prompt": "a heron", "preset": "ink-wash"}])
    b = await hub.batches.run(b["id"])
    assert b["items"][0]["state"] == "done", b["items"][0].get("error")
    assert hub.fake.graphs[-1]["1"]["inputs"]["text"] == "a heron"

    r, card = await _propose(hub, True, label="Ink wash 2", replaces="ink-wash",
                             values={"good_at": "Ink, bolder."})
    assert {"label": "Replaces", "value": "Ink wash"} in card["facts"]
    assert card["title"] == f"{hub.cfg.name} asks to replace the preset Ink wash."
    assert r.content.startswith("Replaced the preset Ink wash (`ink-wash`)")
    p = presets.of("images")["ink-wash"]
    assert (p.label, p.good_at, p.proposed) == ("Ink wash 2", "Ink, bolder.", True)
    assert hub.cfg.settings.chain("image_generation").count("od/ink-wash") == 1


async def test_a_shipped_preset_and_one_the_user_made_are_never_replaced(hub):
    r, _ = await _propose(hub, None, replaces="krea2-turbo")
    assert r.content == ("Krea 2 turbo came with the app, so it is never replaced: propose a "
                         "new preset, without replaces.")
    mine = presets.upload("images", "Mine", json.dumps(UPLOAD))
    r, _ = await _propose(hub, None, replaces=mine.id)
    assert r.content == ("Mine is one sam made, so it is never replaced: propose a new "
                         "preset, without replaces.")
    assert hub.approvals.waiting == {}


async def test_a_denied_proposal_saves_nothing(hub):
    r, _ = await _propose(hub, False)
    assert (r.is_error, r.content) == (True, "sam denied it; nothing was done.")
    assert "ink-wash" not in presets.of("images")


# ---- the page ----------------------------------------------------------------------------

def _web(hub):
    from fastapi import FastAPI
    from celine.errors import install_error_handler
    web = FastAPI()
    install_error_handler(web)
    web.include_router(comfy_api.router)
    web.state.services = hub
    return web


async def test_the_page_lists_what_came_and_what_is_on_its_way_and_removes_one(hub):
    web = _web(hub)
    ctx = await _ctx(hub)
    await _images(hub).request_model(ctx, preset="qwen2.1-smart", kind="lora",
                                     file="civitai:222", why=WHY)
    await _card(hub)
    _answer(hub, True)
    await _notice_for(hub, ctx.conversation_id)
    hub.helper.script = ["fetching"]
    await _images(hub).request_model(await _ctx(hub), preset="qwen2.1-smart", kind="lora",
                                     file=HF_VALUE, why=WHY)
    async with client(web) as c:
        got = (await c.get("/api/model-files")).json()
        assert got["free"] == 500 * GB
        [came] = [f for f in got["files"] if f["state"] == "ready"]
        [asked] = [f for f in got["files"] if f["state"] != "ready"]
        assert came == {"path": "loras/qwen2.1/Ink_Wash_v2.safetensors",
                        "id": "qwen2.1/Ink_Wash_v2", "kind": "lora", "size": 200 * MB,
                        "at": "2026-11-11T10:00:00.000+00:00", "preset": "qwen2.1-smart",
                        "preset_label": "Qwen 2.1", "site": "civitai.red",
                        "title": "Ink wash, v2",
                        "address": "https://civitai.red/models/111?modelVersionId=222",
                        "request": came["request"], "state": "ready",
                        "display": {"words": "Ready", "tone": "success"}, "done": None,
                        "removable": True}
        assert (asked["state"], asked["display"]["words"], asked["removable"],
                asked["title"]) == ("asked", "Waiting for you", False, "MicksHF/Anime")
        r = await c.delete("/api/model-files/loras/qwen2.1/old.safetensors")
        assert r.status_code == 409 and "came with the set" in r.json()["error"]["message"]
        r = await c.delete("/api/model-files/loras/qwen2.1/Ink_Wash_v2.safetensors")
        assert r.json() == {"removed": True}
        assert [f["path"] for f in (await c.get("/api/model-files")).json()["files"]] == [
            "loras/qwen2.1/fusal.safetensors"]


async def test_the_civitai_key_goes_to_the_helper_and_the_app_keeps_nothing_of_it(
        hub, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    kept = {"state": "set", "display": {"words": "Set", "tone": "success"}}
    none = {"state": "not set", "display": {"words": "Not set", "tone": "muted"}}
    async with client(_web(hub)) as c:
        assert (await c.get("/api/civitai-key")).json() == none
        r = await c.put("/api/civitai-key", json={"key": "two words"})
        assert (r.status_code, r.json()["error"]["message"]) == (
            400, "That is not an API key (it has spaces); nothing changed.")
        assert (await c.put("/api/civitai-key", json={"key": 5})).status_code == 400
        r = await c.put("/api/civitai-key", json={"key": "wrong-" + CIVITAI_KEY})
        assert (r.status_code, r.json()["error"]["message"]) == (400, CHECK_REFUSED), \
            "Civitai's refusal is the save's error, in the helper's words"
        assert hub.helper.civitai is None
        r = await c.put("/api/civitai-key", json={"key": CIVITAI_KEY})
        assert r.json() == kept and hub.helper.civitai == CIVITAI_KEY
        assert (await c.get("/api/civitai-key")).json() == kept
        assert (await c.delete("/api/civitai-key")).json() == none
        assert hub.helper.civitai is None
        await c.put("/api/civitai-key", json={"key": CIVITAI_KEY})

    events: list[str] = []

    async def changed() -> bool:    # the two puts that went through and the delete; no refused one
        events[:] = await hub.store.read(lambda c: [r[0] for r in c.execute(
            "SELECT data FROM global_events WHERE type='model_files.changed'")])
        return len(events) == 3
    await until(changed)
    assert all(CIVITAI_KEY not in d for d in events)
    for f in tmp_path.rglob("*"):
        assert not f.is_file() or CIVITAI_KEY.encode() not in f.read_bytes(), f
    for rec in caplog.records:
        assert CIVITAI_KEY not in rec.getMessage() and CIVITAI_KEY not in str(vars(rec))
