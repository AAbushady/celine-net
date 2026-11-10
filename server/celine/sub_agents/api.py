"""The agent's sub-agents over HTTP: the overview, start one, stop one. The same code its
tools run (skill.py begin, stop_agent), so the API and it see one thing. Each answer is a
model here (core/dto.py), and the page's types are written from them."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..core.dto import Answer, ConversationCrumb, Display, FilterOption, Home
from ..core.statuses import AgentState, MissionStatus
from ..errors import CelineError, NotFound
from . import store as sub_agents

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


class Report(Answer):
    """A sub-agent's report up: the intercom message's id and its text."""
    id: int
    text: str


class SubAgent(Answer):
    """One sub-agent as the overview shows it (store.agents_of)."""
    id: str
    parent: str = Field(description="the conversation that started it")
    name: str
    access: Literal["read", "work"] | None
    folder: str | None
    model: str | None = Field(description="its model's label")
    cli: str | None = Field(description="its agent CLI in the box")
    device: str | None = Field(description="the device it runs on")
    working: bool = Field(description="at work: queued, or something will still come of it")
    state: AgentState
    display: Display
    reason: str | None = Field(description="why it failed, stopped or paused")
    filter: AgentState = Field(description="the State option of the list it falls under")
    step: str | None = Field(description="the tool its live turn runs")
    step_words: str | None = Field(description="that step in words")
    report: Report | None = Field(description="its latest report")
    last: str | None = Field(description="its last words, before a report")
    created_at: str
    updated_at: str
    home: Home = Field(description="where it lives, its root's home")
    link: str = Field(description="the app path that opens it")


class SubAgentList(Answer):
    """A sub-agents list with the State options of its rows."""
    agents: list[SubAgent]
    filters: list[FilterOption]


class TreeAgent(Answer):
    """One sub-agent in the tree under a top conversation (store.tree_of)."""
    id: str
    kind: Literal["agent"]
    parent: str
    depth: int = Field(description="1: started by the top conversation")
    name: str
    state: AgentState
    display: Display
    reason: str | None = Field(description="why it failed, stopped or paused")
    step_words: str | None
    model: str | None = Field(description="the newest model that answered, as its label")
    prompt_tokens: int
    completion_tokens: int
    seconds: int
    link: str


class TreeMission(Answer):
    """A mission the top or one of its sub-agents started, in the same tree."""
    id: str
    kind: Literal["mission"]
    parent: str
    depth: int
    name: str
    state: MissionStatus
    display: Display
    reason: str | None = Field(description="its note while it waits for you or stopped")
    step_words: str | None
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    seconds: int
    link: str


class Work(Answer):
    """What turns took: tokens in and out as the servers counted them, and run time."""
    prompt_tokens: int
    completion_tokens: int
    seconds: int


class SubAgentTree(Answer):
    """Everything under the top of a conversation's chain."""
    top: str
    origin: list[ConversationCrumb] = Field(description="where the top came from, a mission's run")
    agents: list[TreeAgent]
    missions: list[TreeMission]
    total: Work = Field(description="what the top and everything under it took")


class Started(Answer):
    conversation_id: str
    resumed: bool


class Stopped(Answer):
    stopped: bool


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
                      home: str | None = None) -> SubAgentList:
    """The sub-agents of one conversation, or every one not in the archive, newest
    first; with `home` only those that live there (homes.py: `missions`)."""
    services = request.app.state.services
    rows = await services.store.read(
        lambda c: sub_agents.agents_of(c, conversation, own=services.store.tool_words))
    rows = [{**a, "filter": AgentState.WORKING if a["working"] else a["state"]}
            for a in sub_agents.labelled(getattr(services.cfg, "settings", None), rows)
            if home is None or a["home"] == home]
    return SubAgentList(
        agents=[SubAgent.of(a) for a in rows],
        filters=[FilterOption(value="all", label="All", count=len(rows)),
                 *(FilterOption(value=s.value, label=s.display["words"],
                                count=sum(1 for a in rows if a["filter"] == s))
                   for s in FILTERS)])


@router.get("/conversations/{cid}/sub-agents")
async def agent_tree(request: Request, cid: str) -> SubAgentTree:
    """The sub-agents under the top of `cid`'s chain, and the missions they started, as
    a tree (store.tree_of)."""
    services = request.app.state.services
    out = await services.store.read(
        lambda c: sub_agents.tree_of(c, cid, services.store.tool_words))
    if out is None:
        raise NotFound("chat")
    settings = getattr(services.cfg, "settings", None)
    return SubAgentTree(
        top=out["top"], origin=out["origin"], total=out["total"],
        agents=[TreeAgent.of(n) for n in sub_agents.labelled(settings, out["agents"])],
        missions=[TreeMission.of(n) for n in sub_agents.labelled(settings, out["missions"])])


@router.post("/conversations/{cid}/sub-agents")
async def start_agent(request: Request, cid: str, body: AgentIn) -> Started:
    """Start a sub-agent of conversation `cid` with a brief, like its
    sub_agents__start: the name of one that is done gives it the brief (resumed). The
    deepest sub-agent starts none (skill.MAX_DEPTH)."""
    store = request.app.state.services.store
    if not await store.read(lambda c: sub_agents.is_conversation(c, cid)):
        raise NotFound("chat")
    sub, refusal, resumed, _ = await _skill(request).begin(
        store, cid, body.brief, body.name, body.access, body.folder, body.model, body.cli,
        body.device)
    if refusal:
        raise CelineError("refused", refusal, 409)
    return Started(conversation_id=sub, resumed=resumed)


@router.post("/sub-agents/{sub}/stop")
async def stop_agent(request: Request, sub: str) -> Stopped:
    """Stop a sub-agent: its turn, its jobs, and what it scheduled."""
    store = request.app.state.services.store
    if not await store.read(lambda c: sub_agents.is_sub_agent(c, sub)):
        raise NotFound("agent")
    await _skill(request).stop_agent(sub)
    return Stopped(stopped=True)
