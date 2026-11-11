"""Everything the running app holds, as one typed object, and what each part offers the
others.

A service's class lives in its feature, and the core imports no feature (.importlinter).
So each field is typed here with an interface: the members other packages use of that
service, and no more. A feature's class fits its interface by having those members (a
Protocol: nothing inherits), the start-up in __main__.py is the one place that names the
classes, and mypy holds every typed use of a service to its interface. A member another
package starts to use is added to the interface first.

build_app makes the object and the lifespan fills each field as its service starts. Until
then a field is None (`_LATER`), and a test fills only what it uses. Skills get the
object from `skills.build` as `self.app` and read a field when a tool runs, never when
they are built, so a skill rebuilt by a config reload needs no rewiring. The routes have
the same object as `app.state.services`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import Config
from .bus import EventBus
from .runners import Runners
from .store import Store

# a call with many options, held to its name alone until its callers are typed
Call = Callable[..., Any]


class LLM(Protocol):
    """The model layer: its queue and its router (backends/llm.py LLM)."""
    router: Any                         # backends/router.py Router: providers, hosts, what is loaded
    scheduler: Any                      # backends/queue.py Scheduler: the one queue
    turn_priority: dict[str, Any]       # turn id -> the priority its model calls run at

    def make(self, ref: str, thinking: str | None = None, voice: dict | None = None) -> Any: ...
    @property
    def bind(self) -> Call: ...         # a backend bound to the queue for one caller
    @property
    def gpu_lease(self) -> Call: ...    # the GPU held for a provider on the same card
    def turn_paused(self, turn_id: str | None) -> AbstractContextManager[None]: ...  # its clock held


class KBs(Protocol):
    """The knowledge bases (kb/__init__.py KBs)."""
    tasks: Any                          # kb/tasks.py Tasks
    all: dict[str, Any]

    def get(self, kid: str) -> Any: ...
    def documents(self) -> Any: ...
    def chats(self) -> list[Any]: ...   # the chat sources that are open
    def open(self, kid: str) -> None: ...
    def close(self, kid: str) -> None: ...
    @property
    def search(self) -> Call: ...
    @property
    def facets(self) -> Call: ...
    @property
    def parse(self) -> Call: ...
    @property
    def resolve_refs(self) -> Call: ...


class Registry(Protocol):
    """The skills and their tools (skills/__init__.py Registry)."""
    skills: dict[str, Any]
    tools: dict[str, Any]

    def get(self, wire_name: str) -> Any: ...


class Dispatcher(Protocol):
    """The turn engine (turns/dispatcher.py Dispatcher)."""
    work: Any                           # work.py Pool: background model jobs
    jobs: Any                           # jobs/jobs.py Jobs, once started
    backend_factory: Any
    run_binding: Any

    def notify(self) -> None: ...
    def on_end(self, fn: Callable[[str, str | None], Awaitable[None]]) -> None: ...
    def resize(self) -> None: ...
    async def abort_conversation(self, cid: str) -> str | None: ...
    @property
    def bound(self) -> Call: ...        # a backend for one conversation's model calls


class Intercom(Protocol):
    """Messages between everyone (intercom/service.py Intercom)."""
    @property
    def send(self) -> Call: ...         # (from, to, text, ...) -> the message's ids, or None
    async def cancel(self, iid: int) -> bool: ...
    async def cancel_scheduled(self, cid: str) -> list[int]: ...


class Terminals(Protocol):
    """The terminals' state kept in step with the box (terminals/sync.py TerminalSync)."""
    ready: Any

    @property
    def run(self) -> Call: ...
    @property
    def close_opened_by(self) -> Call: ...


class Mcp(Protocol):
    """The MCP servers (mcp.py Supervisor)."""
    @property
    def run_one(self) -> Call: ...
    def retry(self, name: str) -> bool: ...


class Triggers(Protocol):
    """Watch and event triggers (triggers/__init__.py Triggers)."""
    @property
    def run(self) -> Call: ...
    def apply(self) -> None: ...


class Schedules(Protocol):
    """The routines' scheduler (schedules/runs.py Scheduler)."""
    def wake(self) -> None: ...
    async def run_now(self, name: str, by: str = "user") -> dict: ...


class Vault(Protocol):
    """Its password vault (vault/__init__.py Vault)."""
    ready: Any                          # set once the login was tried: its turns wait on it
    session: Any
    forget: Any                         # called when it locks: the start-up sets it

    async def connect(self) -> str: ...
    async def unlock(self, master: str) -> str: ...
    async def lock_now(self) -> str: ...
    async def entry(self, name: str) -> tuple[dict | None, list[str]]: ...
    async def secret(self, name: str, which: str) -> tuple[dict, str]: ...
    async def make(self, name: str, username: str, url: str, notes: str) -> dict: ...
    async def rotate(self, entry: dict) -> dict: ...
    async def items(self) -> list[dict]: ...


class Approvals(Protocol):
    """What waits for the user's yes before it runs (approvals.py Approvals)."""
    waiting: dict[Any, Any]

    def forget(self, kind: str = "vault") -> None: ...
    def view(self, kind: str | None = None) -> list[dict]: ...
    def in_turns(self) -> int: ...
    def decide(self, aid: str, ok: bool, by: Any) -> bool: ...
    def seen(self, conversation: str, tool: str) -> None: ...
    @property
    def ask(self) -> Call: ...


_LATER: Any = None      # a field before the start-up fills it


@dataclass
class Services:
    cfg: Config = _LATER
    store: Store = _LATER
    bus: EventBus = _LATER
    llm: LLM = _LATER
    kbs: KBs = _LATER
    registry: Registry = _LATER
    dispatcher: Dispatcher = _LATER
    intercom: Intercom = _LATER
    terminals: Terminals = _LATER
    mcp: Mcp = _LATER
    # connectors, batches and push are handed on whole: no package reads a member of
    # them through here
    connectors: Any = None          # kb/connectors.py Connectors
    batches: Any = None             # comfyui/batches.py Batches, while images or music are on
    # None while Routines is off (__main__.py): its users ask first
    triggers: Triggers | None = None
    schedules: Schedules | None = None
    vault: Vault = _LATER
    approvals: Approvals = _LATER
    push: Any = None                # push.py Push: web push to the user's devices
    runners: Runners = _LATER       # core/runners.py: what runs for each service feature
    feature_probes: Any = None      # settings/features.py probe_all(): (monotonic, {id: probe})
