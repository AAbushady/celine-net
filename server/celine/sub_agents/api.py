"""The agent's sub-agents over HTTP: the overview, start one, stop one. The same code its
tools run (skill.py begin, stop_agent), so the API and it see one thing."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..core.statuses import AgentState
from ..errors import CelineError, NotFound
from .store import agents_of, labelled, tree_of

router = APIRouter(prefix="/api")


class AgentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    access: str | None = None        # read | work
    folder: str | None = None
    model: str | None = None         # one of its models (settings/agent_models.py)
    cli: str | None = None           # an agent CLI in its shellbox (cli.py CLIS)
    device: str | None = None        # a device it runs on, a drone (drone.py devices)


def _skill(request: Request):
    reg = request.app.state.services.registry
    skill = reg.skills.get("sub_agents") if reg is not None else None
    if skill is None:
        raise CelineError("unavailable", "Sub-agents are off.", 409)
    return skill


# the State options of a sub-agents list: a row at work (queued too) falls under Running
FILTERS = (AgentState.WORKING, AgentState.FAILED, AgentState.DONE)


@router.get("/sub-agents")
async def list_agents(request: Request, conversation: str | None = None,
                      home: str | None = None) -> dict:
    """The sub-agents of one conversation, or every one not in the archive, newest
    first, with `home` only those that live there (homes.py: `missions`): id, parent,
    name, access, folder, model, cli, device, working, state and its display, the step
    at work, the latest report, its last words, and `filter`, the State option it falls
    under. `filters`: those options, each {value, label, count} over the rows sent."""
    services = request.app.state.services
    rows = await services.store.read(lambda c: agents_of(c, conversation, own=services.store.tool_words))
    rows = [{**a, "filter": AgentState.WORKING if a["working"] else a["state"]}
            for a in rows if home is None or a["home"] == home]
    filters = [{"value": "all", "label": "All", "count": len(rows)},
               *({"value": s.value, "label": s.display["words"],
                  "count": sum(1 for a in rows if a["filter"] == s)} for s in FILTERS)]
    return {"agents": labelled(getattr(services.cfg, "settings", None), rows),
            "filters": filters}


@router.get("/conversations/{cid}/sub-agents")
async def agent_tree(request: Request, cid: str) -> dict:
    """The sub-agents under the top of `cid`'s chain, and the missions they started, as
    a tree (store.tree_of): {top, origin, agents, missions, total}, each with its depth,
    state, reason, model (its label), tokens, seconds and link."""
    services = request.app.state.services
    out = await services.store.read(lambda c: tree_of(c, cid, services.store.tool_words))
    if out is None:
        raise NotFound("chat")
    settings = getattr(services.cfg, "settings", None)
    return {**out, "agents": labelled(settings, out["agents"]),
            "missions": labelled(settings, out["missions"])}


@router.post("/conversations/{cid}/sub-agents")
async def start_agent(request: Request, cid: str, body: AgentIn) -> dict:
    """Start a sub-agent of conversation `cid` with a brief, like its
    sub_agents__start: the name of one that is done gives it the brief (resumed). The
    deepest sub-agent starts none (skill.MAX_DEPTH)."""
    store = request.app.state.services.store
    if await store.read(lambda c: c.execute(
            "SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone()) is None:
        raise NotFound("chat")
    sub, refusal, resumed, _ = await _skill(request).begin(
        store, cid, body.brief, body.name, body.access, body.folder, body.model, body.cli,
        body.device)
    if refusal:
        raise CelineError("refused", refusal, 409)
    return {"conversation_id": sub, "resumed": resumed}


@router.post("/sub-agents/{sub}/stop")
async def stop_agent(request: Request, sub: str) -> dict:
    """Stop a sub-agent: its turn, its jobs, and what it scheduled."""
    store = request.app.state.services.store
    if await store.read(lambda c: c.execute(
            "SELECT 1 FROM conversations WHERE id=? AND agent_of IS NOT NULL",
            (sub,)).fetchone()) is None:
        raise NotFound("agent")
    await _skill(request).stop_agent(sub)
    return {"stopped": True}
