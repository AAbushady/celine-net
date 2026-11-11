"""Entrypoint: preflight, then serve."""
from __future__ import annotations

import argparse
import contextlib
import functools
import logging
import os
import socket
import sys
from typing import NoReturn
from pathlib import Path

import anyio
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config as config_mod
from .api import origin as origin_mod
from .conversations import store as conversations
from . import attachments, boot, keys, reload
from .backends import llm as llm_mod
from .core.parts import Part, Run
from .core.services import Services
from .api.part import part as api_part
from .jobs.part import part as jobs_part
from .conversations.part import part as conversations_part
from .missions.part import part as missions_part
from .schedules.part import part as routines_part
from .sub_agents.part import part as sub_agents_part
from .intercom.part import part as intercom_part
from .terminals.part import part as terminals_part
from .browser.part import part as browser_part
from .artifacts.part import part as artifacts_part
from .shares import part as shares_part
from .push import part as push_part
from .comfyui.part import part as comfyui_part
from .kb.part import part as kb_part
from .leftovers import part as leftovers_part
from .skills.part import part as skills_part
from .guard.part import part as guard_part
from .bugreports.part import part as bugreports_part
from .events import part as events_part
from .mcp import Supervisor
from .db.migrate import migrate_all
from .core.store import Store
from .core.bus import EventBus
from .skills import build as build_registry
from .turns import store as turns
from .turns.dispatcher import Dispatcher
from .errors import install_error_handler, set_secrets
from .clock import use_zone
from .logs import setup as setup_logging

log = logging.getLogger("celine")

STATIC_DIR = config_mod.STATIC_DIR     # the web build it serves (config.py)


def _refuse(message: str) -> NoReturn:
    print(f"celine: {message}", file=sys.stderr)
    raise SystemExit(2)


def _load_config(load=boot.load) -> config_mod.Config:
    try:
        return load()
    except config_mod.ConfigError as e:
        _refuse(str(e))


def own_data_dir(data_dir: Path) -> None:
    """What the app writes is its user's alone: umask 077 for this process, the data dir
    made 0700, or closed when it is its own and was open. What another container reads
    from the volume (keys/*.hash, connector-clients, searxng-settings.yml) is mounted as
    a file of its own, with the mode keys.py gives it, so the closed folder above it
    changes nothing there."""
    os.umask(0o077)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = data_dir.stat()
    if st.st_uid == os.getuid() and st.st_mode & 0o077:
        data_dir.chmod(0o700)


def preflight(argv: argparse.Namespace) -> config_mod.Config:
    # a second worker process means a second writer and broken event sequencing
    if int(os.environ.get("WEB_CONCURRENCY", "1")) != 1:
        _refuse("WEB_CONCURRENCY must be 1. Celine has a single writer task; a "
                "second worker process would break event sequencing.")

    cfg = _load_config(config_mod.load)    # how it is served, where its data lives
    from .skills import check_guides
    if bad := check_guides():
        _refuse(f"A skill guide is missing or misnamed: {bad}")
    if argv.port:
        reload.CLI_SERVER["port"] = argv.port
        cfg.server.update(reload.CLI_SERVER)

    try:
        own_data_dir(cfg.data_dir)
        for p in (cfg.blobs_dir, cfg.data_dir / "logs"):
            p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _refuse(f"The data dir {cfg.data_dir} is not writable ({e.strerror}). Set "
                f"CELINE_DATA_DIR or paths.data in config/celine.toml to a folder this "
                f"user may write.")
    setup_logging(cfg.data_dir / "logs", dev=argv.dev,
                  level="DEBUG" if argv.verbose else "INFO")

    # hard refusal: a shell-running API on a network without a login
    from .auth import startup_problem
    problem = startup_problem(cfg.server, cfg.host, cfg.port, argv.dev)
    if problem:
        _refuse(problem)

    with contextlib.closing(socket.socket()) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((cfg.host, cfg.port))
        except OSError:
            _refuse(f"{cfg.host}:{cfg.port} is already in use. Another Celine is "
                    f"probably running.")

    made = keys.ensure(cfg.data_dir)      # the stack's own keys (keys.py)
    set_secrets(keys.values(cfg.data_dir), "keys")
    if made:
        log.info("keys made", extra={"event": "keys.made", "files": made,
                                     "dir": str(cfg.data_dir / "keys")})
    from .settings import files as settings_files, seed
    seed.seed(settings_files.settings_dir(cfg))    # the context shares: copied once
    version = migrate_all(cfg.db_path)
    cfg = _load_config()                            # everything, its row too
    cfg.server.update(reload.CLI_SERVER)
    set_secrets(cfg.secrets())
    use_zone(cfg)
    log.info("database ready", extra={"event": "db.ready", "version": version,
                                     "path": str(cfg.db_path)})

    for found in cfg.problems:
        log.warning("configuration problem", extra={"event": "config.problem",
                                                   "file": found.file,
                                                   "problem": found.message})
    return cfg


# Every feature's part (core/parts.py), in the order their steps run: a sub-agent's report
# lands before its mission is looked at, and Routines start before the check-ins.
PARTS: tuple[Part, ...] = (
    api_part, jobs_part, conversations_part, sub_agents_part, leftovers_part, routines_part,
    guard_part, missions_part, skills_part, kb_part, comfyui_part, intercom_part,
    terminals_part, browser_part, artifacts_part, shares_part, push_part, bugreports_part,
    events_part,
)


def build_app(cfg: config_mod.Config, dev: bool) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with anyio.create_task_group() as tg:
            services: Services = app.state.services     # typed: each service is held to its interface
            bus = services.bus = EventBus()
            store = services.store = Store(cfg.db_path, bus=bus, cfg=cfg)
            await store.start(tg)
            from .conversations.store import keep_lock
            keep_lock(store, cfg)               # Lock archived chats, where its SQL reads it
            # a fresh install's row of it; then the config again, with it
            from . import agent
            if await store.write(agent.seed):
                reload.refill(cfg)
            # the one LLM queue and router, before anything that asks a model (document
            # descriptions start with the first scan, captions with their scheduler)
            # no `publish`: a snapshot on every change filled the global feed's ring
            # (13,000 of its 20,000 rows); GET /api/llm-queue has the queue as it is
            llm = services.llm = llm_mod.LLM(cfg)
            from .settings import features     # what is on now (hot: core/runners.py)
            from .core.runners import Runner, Runners
            on = features.effective(cfg.settings.features)
            runners = services.runners = Runners()

            import asyncio
            from .kb import KBs
            kbs = services.kbs = await anyio.to_thread.run_sync(
                lambda: KBs(cfg, store.emit_global, llm))
            kbs.tasks.bind(asyncio.get_running_loop())
            from .kb import embed                     # index builds wait in the LLM queue
            embed.use_queue(llm.scheduler, asyncio.get_running_loop())
            from .kb import semantic as _semantic     # semantic topic search index updates
            tg.start_soon(_semantic.run_scheduler, cfg, kbs)
            from .kb import media as _media           # image captions (Settings, General)
            tg.start_soon(_media.run_scheduler, cfg, kbs)
            log.info("knowledge bases ready", extra={"event": "kb.ready",
                                                     "kbs": sorted(kbs.all)})

            # its vault: logged in in the background, locked until the user types the
            # master password (Settings, Vault); its turns wait for the login (the
            # dispatcher's ready)
            from .vault import Vault
            from .approvals import Approvals
            services.vault = Vault(cfg, store.emit_global)
            def approvals_changed(event: dict) -> None:
                store.emit_global(event)
                if services.dispatcher is not None:   # a waiting ask frees its turn's slot
                    services.dispatcher.resize()
            services.approvals = Approvals(cfg, approvals_changed, store)
            services.vault.forget = lambda: services.approvals.forget("vault")

            async def vault_run() -> None:
                try:
                    await services.vault.connect()
                finally:                     # its turns wait on it (dispatcher.ready)
                    services.vault.ready.set()
            runners["vault"] = Runner(tg, vault_run)
            if on["vault"]:
                await runners["vault"].start()
            else:
                services.vault.ready.set()

            registry = services.registry = build_registry(services)
            from .settings import conversations as conv_models
            await conv_models.load_all(store)      # per-conversation chat models
            from .settings import roles as conv_roles
            await conv_roles.load_all(store, cfg)  # per-conversation roles
            if "notebook" in registry.skills:
                await registry.skills["notebook"].load_marks(store)
                seeded = await registry.skills["notebook"].seed(store)
                if seeded:
                    log.info("notebook seeded", extra={"event": "notebook.seeded",
                                                       "files": seeded})
                fixed = await registry.skills["notebook"].reconcile(store)
                if any(fixed.values()):
                    log.info("notebook reconciled", extra={"event": "notebook.reconciled",
                                                           **fixed})
            log.info("skills ready", extra={"event": "skills.ready",
                                            "skills": len(registry.skills),
                                            "tools": len(registry.tools)})
            mcp = services.mcp = Supervisor(cfg, store, registry, kbs=kbs)
            await mcp.start(tg)
            # Discord and WhatsApp: the connectors' feeds and states (kb/connectors.py);
            # each with its MCP server and its archive under its feature's runner
            from .kb.connectors import Connectors
            connectors = services.connectors = Connectors(
                cfg, kbs, store.emit_global)
            tg.start_soon(connectors.run)

            from .comfyui import server as comfy_server
            if on["images"] or on["music"]:
                tg.start_soon(comfy_server.free_shared, cfg, llm)
            tg.start_soon(llm.router.check_all)  # each provider asked once; then requests tell

            # before the workers start: what a crash or restart left behind
            await attachments.gc(cfg, store)
            await conversations.prune_events(store, keep=int(cfg.r("events_keep")),
                                             requests_days=int(cfg.r("requests_keep")))
            tg.start_soon(conversations.keep_pruned, store, cfg)    # and every so often
            swept = await turns.sweep(store, registry)
            from .approvals import close_left
            await close_left(store)                 # the chats' requests of the last run
            if swept["total"]:
                log.info("swept orphans", extra={"event": "startup.swept",
                                                 "count": swept["total"],
                                                 "interrupted": swept["interrupted"]})
            dispatcher = services.dispatcher = Dispatcher(store, cfg, bus, None,
                                                          registry, llm=llm)
            dispatcher.ready = services.vault.ready
            dispatcher.approvals = services.approvals
            from .push import Push              # the notices to devices without a focused page
            push = services.push = Push(cfg, store, bus, app.state.auth)
            tg.start_soon(push.run)
            from . import waits                 # Waiting for you: one waits.changed per change
            tg.start_soon(waits.run, store, bus)
            from .intercom import Intercom      # messages between everyone, and their timer
            intercom = services.intercom = Intercom(store, dispatcher.notify, dispatcher.user_wrote)
            dispatcher.intercom = intercom       # a Stop's cascade cancels schedules
            # its terminals, for the pages: the routes always have the sync, its loop
            # follows the shellbox only when there is one (terminals/part.py)
            from .terminals.sync import TerminalSync
            services.terminals = TerminalSync(cfg, store)
            services.terminals.ready = services.vault.ready

            # the parts, phase by phase (core/parts.py). Every on_end before the workers
            # start: a turn resumed at start that ends while the awaits below run still
            # reports
            for part in PARTS:
                if part.wire is not None:
                    part.wire(services)
            runs: dict[str, Run] = {}
            for part in PARTS:
                if part.runs is not None:
                    runs.update(part.runs(services))
            from . import plugins                # each plugin's runner, by its switch
            from .api import plugins as plugins_api
            for p in plugins.all():
                runs[p.id] = Run(functools.partial(
                    plugins_api.run, services, app.state.plugin_routes, p))
            for fid, run in runs.items():
                runners[fid] = Runner(tg, run.what, features=run.features, on_stop=run.on_stop)

            async def start_runs(early: bool) -> None:
                for fid, run in runs.items():
                    if run.early is early and any(on[f] for f in run.features or (fid,)):
                        await runners[fid].start()
            await start_runs(early=True)
            await dispatcher.start(tg)
            await intercom.run(tg)
            for part in PARTS:                   # what queues or starts turns
                if part.start is not None:
                    await part.start(services)
            await start_runs(early=False)
            tg.start_soon(reload.watch, services)
            from .turns import warm               # Keep the chat ready (Advanced, Models)
            tg.start_soon(warm.run, services)

            auth = app.state.auth
            if auth is not None:
                auth.store.prune_sessions()
                auth.announce_setup_code()       # no passkey yet: a one-time code
            if cfg.server.get("health_port"):
                from .auth import health
                tg.start_soon(health.serve, int(cfg.server["health_port"]), lambda: store.alive)
            if cfg.server.get("metrics_port"):
                from . import metrics
                tg.start_soon(metrics.serve, int(cfg.server["metrics_port"]))
            log.info("celine is up", extra={"event": "startup.ready",
                                            "url": f"http://{cfg.host}:{cfg.port}",
                                            "login": "on" if auth else "off"})
            try:
                yield
            finally:
                embed.use_queue(None, None)    # its loop ends: builds after it take no ticket
                await _announce_shutdown(store, intercom)
                await dispatcher.stop()
                await store.aclose()
                tg.cancel_scope.cancel()

    app = FastAPI(title="Celine", lifespan=lifespan, docs_url=None, redoc_url=None)
    # everything the app runs, filled by the lifespan as each part starts (core/services.py)
    app.state.services = Services(cfg=cfg)
    install_error_handler(app)
    from . import auth as auth_mod
    from .auth.gate import not_found
    from .auth.install import install as install_login
    from .settings import address
    origin_mod.install(app, cfg.port, dev, log, server=cfg.server,
                       login_on=auth_mod.mode(cfg.server) == "on",
                       stored=lambda: address.get(cfg))
    install_login(app, cfg, dev)             # the session gate
    from .api.headers import SecurityHeaders
    app.add_middleware(SecurityHeaders)      # outermost: covers the gate's answers too
    for part in PARTS:
        for routes in part.routes:
            app.include_router(routes)

    # the plugins' routes, appended and removed by their runners (api/plugins.py)
    from .api import plugins as plugins_api
    plugins_mount, app.state.plugin_routes = plugins_api.mount()
    app.router.routes.append(plugins_mount)

    @app.get("/api/health", include_in_schema=False)
    async def _health() -> dict:
        return {"ok": True}

    # before the SPA catch-all, or an unknown /api path returns HTML; every method, or an
    # unknown POST matches this path's GET and answers 405. The login's gate answers
    # exactly this for everything while there is no session.
    @app.api_route("/api/{rest_of_path:path}", include_in_schema=False,
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def _api_404(rest_of_path: str):
        return not_found(f"/api/{rest_of_path}")

    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"),
                  name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa(full_path: str):
            # a file of web/public at the root of the build (the PWA manifest, its icons, theme.js)
            f = STATIC_DIR / full_path
            if "/" not in full_path and f.suffix in _ROOT_FILES and f.is_file():
                return FileResponse(f, headers={"cache-control": "no-cache"})
            # never from the browser cache: after a logout the same URL must reach the
            # gate and get the login page (the assets are content-hashed and may cache)
            return FileResponse(STATIC_DIR / "index.html", headers={"cache-control": "no-store"})
    else:
        @app.get("/", include_in_schema=False)
        async def _no_build():
            return JSONResponse({
                "message": "The frontend is not built. Run `make web` for the dev "
                           "server, or `make build` to bundle it here.",
            })

    return app


# what web/public may put at the root of the build
_ROOT_FILES = {".webmanifest", ".png", ".svg", ".ico", ".js"}


async def _announce_shutdown(store, intercom) -> None:
    """Every conversation with a job at work hears that the app restarts: it finds it in
    its next turn. What the restart cuts comes after the start (the sweep); a turn it
    cuts ends `interrupted` and waits for the user's Regenerate."""
    try:
        cids = await store.read(lambda c: [r[0] for r in c.execute(
            "SELECT DISTINCT conversation_id FROM jobs WHERE state='running'")])
        for cid in cids:
            await intercom.send("system:app", f"agent:{cid}", "The app is restarting now.",
                                kind="notice", wake=False)
    except Exception:
        log.exception("restart notice failed", extra={"event": "shutdown.notice_failed"})


def main() -> None:
    ap = argparse.ArgumentParser(prog="celine")
    ap.add_argument("--dev", action="store_true",
                    help="allow the Vite dev origin and log human-readably")
    ap.add_argument("--port", type=int, help="override [server] port")
    ap.add_argument("--verbose", action="store_true")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("doctor", help="check config, database and backend, then exit")
    exp = sub.add_parser("export", help="write every chat as JSON and Markdown")
    exp.add_argument("--out", type=Path, help="directory (default Documents/exports/export-<time>)")
    pur = sub.add_parser("purge", help="delete and ignore every chat under N messages, "
                                       "then the people nothing refers to")
    pur.add_argument("--under", type=int, required=True, help="messages a chat needs to stay")
    pur.add_argument("--dry-run", action="store_true", help="print the counts, change nothing")
    kys = sub.add_parser("keys", help="make the stack's keys under <dir>/keys when missing "
                                      "(default: the data dir), then exit")
    kys.add_argument("dir", nargs="?", type=Path)
    argv = ap.parse_args()

    if argv.command == "keys":
        from . import ops
        raise SystemExit(ops.keys(argv.dir or _load_config(config_mod.load).data_dir))

    if argv.command in ("doctor", "export", "purge"):
        from . import ops
        cfg = _load_config()
        set_secrets(cfg.secrets())
        set_secrets(keys.values(cfg.data_dir), "keys")
        own_data_dir(cfg.data_dir)
        migrate_all(cfg.db_path)
        if argv.command == "doctor":
            raise SystemExit(ops.doctor(cfg))
        if argv.command == "purge":
            raise SystemExit(ops.purge(cfg, argv.under, argv.dry_run))
        ops.export(cfg, argv.out)
        return

    cfg = preflight(argv)
    app = build_app(cfg, argv.dev)
    print(f"\n  http://{cfg.host}:{cfg.port}\n", file=sys.stderr)
    # forwarded client addresses are believed only from the configured proxies
    proxies = cfg.server.get("trusted_proxies") or []
    proxies = [proxies] if isinstance(proxies, str) else [str(p) for p in proxies]
    # the one socket (api/ws.py): a client sends only small control frames; a ping
    # every 20 s finds a dead connection behind a proxy
    # the standard loop, not uvloop: libuv resolves names on four threads, and a few
    # names that do not exist (a box not started) take seconds each to fail, so the
    # probes of the boxes that do run would wait behind them and time out
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning", loop="asyncio",
                access_log=False, proxy_headers=bool(proxies),
                forwarded_allow_ips=",".join(proxies) if proxies else None,
                ws_max_size=1 << 20, ws_ping_interval=20.0, ws_ping_timeout=20.0)


if __name__ == "__main__":
    main()
