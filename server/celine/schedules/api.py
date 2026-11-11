"""Schedules and their runs. A schedule write is
schedules/edit.py's, the same as its own (schedules/skill.py)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request

from ..core.ids import iso
from ..core.statuses import SCHEDULED, WAITING_FOR_YOU, RunState
from ..errors import CelineError, NotFound
from ..settings import files, seed
from ..settings.files import Invalid
from ..settings.registry import ROLES
from ..clock import zone
from ..core.route_params import TheConfig, TheServices, TheStore
from . import VARIABLES, fill, run_date, run_roles, shown, words
from .runs import IDLE_MINUTES, ROW, STOPPED, _repeated, first_slot, results, \
    view as run_view

router = APIRouter(prefix="/api")
DAYS = 14                       # the past days the page shows
PLANNED = 100                   # the slots of one schedule the rest of a day lists at most
# a field's "?" on the schedule page (ui Help): at most two sentences, filled by the server;
# what runs.py does (_fail, _idle_skip), the period in the Routines page's word (day)
HELP = {
    "on_fail": "Retry runs a failed run again after 15 minutes, 1 hour and 4 hours for the "
               "same day, never past the next run, unless it was stopped or over the time "
               "limit. Skip ends it with no notice.",
    "idle_only": f"A run starts only when nobody used the app in the last {IDLE_MINUTES} "
                 "minutes, else it is skipped at once; so is one still waiting for another "
                 f"reason {IDLE_MINUTES} minutes after its time. Run now and Retry are not "
                 "skipped for this.",
    "wall_minutes": "The most minutes one run may work; waiting for a model does not count. "
                    "Empty uses the Routine time limit in Settings, Advanced.",
}
CHOICES = {"on_fail": [{"value": "retry", "label": "Retry"}, {"value": "skip", "label": "Skip"}]}


def _folder(request: Request):
    return seed.schedules_dir(files.settings_dir(request.app.state.services.cfg))


def _schedules(request: Request) -> dict:
    return request.app.state.services.cfg.schedules


def _today(cfg) -> date:
    """The day the page opens on: today's date in the app's zone."""
    return datetime.now(zone(cfg)).date()


def _view(s, cfg) -> dict:
    """`label` as written (its {{username}} and {{name}} kept, so a save keeps them),
    `title` the label filled; `every` its cron in words ("Daily at 02:00", schedules.words)."""
    return {"name": s.name, "label": s.label, "title": shown(s), "summary": s.summary, "cron": s.cron, "time": s.time,
            "every": words(s.cron),
            "prompt": s.prompt, "after": list(s.after),
            "reviewer": s.reviewer, "reviewer_prompt": s.reviewer_prompt, "turns": s.turns,
            "note": s.note, "role": s.role,
            "on": s.on, "idle_only": s.idle_only,
            "daytime": s.daytime,
            "wall_minutes": s.wall_minutes, "on_fail": s.on_fail}


def _planned(cfg, taken: set[tuple[str, str]], now_: datetime) -> list[dict]:
    """What is still to come on the day of `now_` (a time in the app's zone): every slot of
    a schedule that is on, after `now_` and before midnight, that has no run yet (`taken`:
    the (schedule, slot) of the day's runs), soonest first; each {schedule, label, slot
    (UTC), at (its time of day)}. A schedule with several slots a day gives each of them,
    at most PLANNED; the wall time the clocks repeat is left out, as the scheduler leaves
    it."""
    from croniter import croniter
    out = []
    for s in (getattr(cfg, "schedules", None) or {}).values():
        if not s.on:
            continue
        slots = croniter(s.cron, now_)
        for _ in range(PLANNED):
            at = slots.get_next(datetime)
            if at.date() != now_.date():
                break
            if (s.name, iso(at)) not in taken and not _repeated(at, now_.tzinfo):
                out.append({"schedule": s.name, "label": shown(s), "slot": iso(at),
                            "at": f"{at:%H:%M}", "display": SCHEDULED})
    return sorted(out, key=lambda p: (p["slot"], p["schedule"]))


def _scheduler(request: Request):
    """The running Scheduler: set while Routines is on."""
    if (s := request.app.state.services.schedules) is None:
        raise CelineError("unavailable", "Routines are off.", 409)
    return s


@router.get("/schedules")
async def list_schedules(request: Request, cfg: TheConfig) -> dict:
    """{schedules: [{name, label, title, summary, cron, time, every, prompt, after, reviewer,
    reviewer_prompt, turns, note, role, on, idle_only, wall_minutes, on_fail}],
    roles, variables, today, choices, help}: by time of day; `time` "02:00" for a daily one (else
    null), `every` in words ("Daily at 02:00", "Sundays at 07:00"),
    `roles` the chat roles a schedule can run on,
    `variables` the words a prompt can hold, `today` the day the page opens on,
    `choices` a field's choices ({on_fail: [{value, label}]}), `help` a field's "?" text
    (on_fail, idle_only, wall_minutes)."""
    rows = sorted(_schedules(request).values(), key=lambda s: (s.time or "99", s.name))
    return {"schedules": [_view(s, cfg) for s in rows],
            "roles": [{"name": r, "label": ROLES[r]["label"]} for r in run_roles()],
            "variables": [*VARIABLES, *(f"result:{s.name}" for s in rows)],
            "today": _today(cfg).isoformat(), "choices": CHOICES,
            "help": {k: cfg.substitute(v) for k, v in HELP.items()}}


@router.put("/schedules/{name}")
async def put_schedule(request: Request, cfg: TheConfig, services: TheServices, name: str) -> dict:
    """{label, summary, cron or time ("02:00": every day then), prompt, after, reviewer,
    reviewer_prompt, turns, note, role, on, idle_only
    (start only when nobody used the app for 30 minutes, else skip that run),
    wall_minutes (minutes it may run, null: the Routine time limit),
    daytime (true: its dates are the calendar date of its slot, not the night's),
    on_fail (retry or skip; daytime and on_fail left out, the stored ones stay)}: the
    whole schedule, made or replaced.
    `reviewer_prompt` is the reviewer's text, written to the file `reviewer` names
    (`<name>-reviewer.md` when none is named); empty: no reviewer. -> the schedule as in
    the list, and `applied`."""
    from ..api.deps import json_body
    from .edit import FIELDS, save
    body = await json_body(request, allow=FIELDS)
    s, applied = await save(services, name, body, "user")
    return {**_view(s, cfg), "applied": applied}


@router.delete("/schedules/{name}")
async def delete_schedule(request: Request, services: TheServices, name: str) -> dict:
    """The schedule goes, its reviewer prompt with it when no other schedule talks with
    it. Its waiting and running runs stop, all its runs stay readable. Refused while
    another schedule follows it."""
    from .. import reload
    from ..settings import write
    every_ = _schedules(request)
    s = every_.get(name)
    if s is None:
        raise NotFound("schedule")
    after = [shown(o) for o in every_.values() if name in o.after]
    if after:
        raise Invalid("after", f"{after[0]} runs after {shown(s)}. Take it out of there first.")
    await _scheduler(request).stop_schedule(name)
    folder = _folder(request)
    async with write.lock():
        (folder / f"{name}.toml").unlink(missing_ok=True)
        if s.reviewer and not any(o.reviewer == s.reviewer for o in every_.values()
                                  if o.name != name):
            (folder / s.reviewer).unlink(missing_ok=True)
        applied = reload.apply(services)
    write.publish(services, ["schedules"])
    _scheduler(request).wake()
    return {"deleted": name, "applied": applied}


@router.post("/schedules/{name}/run")
async def run_schedule(request: Request, name: str) -> dict:
    """A run of the schedule at this second, waiting like any other. -> {run}."""
    if name not in _schedules(request):
        raise NotFound("schedule")
    return {"run": await _scheduler(request).run_now(name, by="user")}


def _date(raw, field: str) -> date:
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise Invalid(field, "The day is a date, such as 2026-01-10.") from None


@router.post("/schedules/preview")
async def preview(request: Request, store: TheStore, cfg: TheConfig, services: TheServices) -> dict:
    """{night, text}: `text` (a prompt, a reviewer prompt or a note path) filled as a run
    of that night gets it, with the results that night has so far and {{mood}} and the
    others as they are now (schedules/variables.py). -> {text}."""
    from ..api.deps import json_body
    body = await json_body(request, need=("night", "text"), allow=("night", "text"))
    night = _date(body["night"], "night")
    if not isinstance(body["text"], str):
        raise Invalid("text", "The text is text.")
    res = await store.read(lambda c: results(c, night.isoformat()))
    from .variables import extras
    return {"text": fill(cfg, body["text"], night, res,
                         await extras(services, body["text"], night))}


@router.get("/runs")
async def list_runs(services: TheServices, day: str | None = None,
                    schedule: str | None = None, until: str | None = None) -> dict:
    """{runs, planned, days, today}: the runs of `day` (a calendar date in the app's zone,
    midnight to midnight: a run's `day`; default: today) or of one `schedule` (its newest
    50), oldest slot first; `planned` what is still to come today, when `day` is today
    (`_planned`: every slot left of every schedule that is on), else nothing;
    `filters` the State options of the day's list, each {value, label, count} (the
    server's words, counted over the day's runs);
    `days` the DAYS days up to `until` (default and at most today),
    newest first, each {day, failed: the newest try of one of its schedules on that day
    failed}. A run's `asked`: an approval waits in it or in a sub-agent of it
    (approvals.asked)."""
    from ..approvals import asked
    cfg, store = services.cfg, services.store
    waits = asked(services)
    held = getattr(services.schedules, "held", None)
    today = _today(cfg)
    which = _date(day, "day") if day else today
    last = min(_date(until, "until"), today) if until else today
    first = last - timedelta(days=DAYS - 1)
    def fn(c):
        if schedule is not None:
            rows = c.execute(f"{ROW} WHERE r.schedule = ? ORDER BY r.slot DESC LIMIT 50",
                             (schedule,)).fetchall()[::-1]
        else:
            rows = c.execute(f"{ROW} WHERE r.day = ? ORDER BY r.slot",
                             (which.isoformat(),)).fetchall()
        # each schedule's newest try of each day (a later slot replaces an earlier one)
        newest: dict[tuple[str, str], str] = {}
        for d in c.execute("SELECT day, schedule, state FROM runs WHERE day >= ? AND "
                           "day <= ? ORDER BY slot", (first.isoformat(), last.isoformat())):
            newest[(d["day"], d["schedule"])] = d["state"]
        return [run_view(r, cfg, r["id"] == held) for r in rows], newest
    runs, newest = await store.read(fn)
    failed = {n for (n, _), state in newest.items() if state == "failed"}
    days = [{"day": n, "failed": n in failed} for n in
            ((last - timedelta(days=i)).isoformat() for i in range(DAYS))]
    runs = [{**r, "asked": r["conversation_id"] in waits,
             **({"display": WAITING_FOR_YOU} if r["conversation_id"] in waits else {})}
            for r in runs]
    filters = [{"value": "all", "label": "All", "count": len(runs)},
               *({"value": s.value, "label": s.display["words"],
                  "count": sum(1 for r in runs if r["state"] == s)}
                 for s in (RunState.RUNNING, RunState.WAITING, RunState.FAILED, RunState.DONE))]
    planned = _planned(cfg, {(r["schedule"], r["slot"]) for r in runs},
                       datetime.now(zone(cfg))) if schedule is None and which == today else []
    return {"runs": runs, "planned": planned, "filters": filters, "days": days,
            "today": today.isoformat()}


@router.get("/runs/{rid}")
async def get_run(request: Request, services: TheServices, rid: str) -> dict:
    """{run, prompt, reviewer_prompt, note}: the run, and what its schedule asks filled
    for its date (run_date of its first try, first_slot; null for a deleted schedule)."""
    cfg, store = services.cfg, services.store
    def one(c):
        r = c.execute(f"{ROW} WHERE r.id = ?", (rid,)).fetchone()
        return r, r and first_slot(c, r)
    r, slot = await store.read(one)
    if r is None:
        raise NotFound("run")
    s = _schedules(request).get(r["schedule"])
    res = await store.read(lambda c: results(c, r["night"]))

    def filled(text: str) -> str | None:
        return fill(cfg, text, run_date(s, datetime.fromisoformat(slot), zone(cfg)),
                    res) if s and text else None
    held = getattr(services.schedules, "held", None) == rid
    return {"run": run_view(r, cfg, held), "prompt": filled(s.prompt if s else ""),
            "reviewer_prompt": filled(s.reviewer_prompt if s else ""),
            "note": filled(s.note if s else "")}


@router.post("/runs/{rid}/stop")
async def stop_run(request: Request, store: TheStore, cfg: TheConfig, rid: str) -> dict:
    """A waiting or running run stops, `stopped` "Stopped by <user>"; a running one's
    turn is aborted. -> {run}. 404: no such run; 400: it has finished."""
    exists = await store.read(lambda c: c.execute(
        "SELECT 1 FROM runs WHERE id = ?", (rid,)).fetchone())
    if exists is None:
        raise NotFound("run")
    out = await _scheduler(request).end(rid, "stopped",
                                        cfg.substitute(STOPPED))
    if out is None:
        raise Invalid("run", "The run has finished.")
    return {"run": out}


@router.post("/runs/{rid}/retry")
async def retry_run(request: Request, rid: str) -> dict:
    """A failed, skipped or stopped run runs again for its night, now: a new waiting run
    `again_of` it (Scheduler.retry). -> {run}. 404: no such run; 400: it has not ended,
    it is done, it ran again already, or its schedule was deleted."""
    return {"run": await _scheduler(request).retry(rid)}
