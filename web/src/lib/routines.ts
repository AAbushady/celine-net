/* Routines: the schedules (jobs as data, GET/PUT/DELETE /schedules, POST run, POST
   preview) and their runs (GET /runs, GET /runs/{id}, POST stop, POST retry; `run.state`
   on the feed). Every state, reason and time is the server's; the page says them as they are.
   The schedule list and a saved schedule are the server's models (api.gen.ts, from
   server/celine/schedules/api.py). docs/ARCHITECTURE.md, Schedules. */
import { LOCALE } from './format'
import { del, json, post, request, type Display } from './api'
import type { SavedSchedule, Schedule, ScheduleList } from './api.gen'

export type { Schedule } from './api.gen'
export type OnFail = Schedule['on_fail']

export type ScheduleBody = {
  label: string
  summary: string
  time?: string
  cron?: string
  prompt: string
  after: string[]
  reviewer: string
  reviewer_prompt: string
  turns: number
  note: string
  role: string
  idle_only: boolean
  wall_minutes: number | null
  tool_rounds: number | null
  daytime: boolean
  on: boolean
  /** left out, the stored one stays */
  on_fail?: OnFail
}

export type RunState = 'waiting' | 'running' | 'done' | 'failed' | 'skipped' | 'stopped'

export type Run = {
  id: string
  schedule: string
  label: string
  /** in the list: an approval waits in it or in a sub-agent of it */
  asked?: boolean
  /** the planned time (UTC) */
  slot: string
  /** the slot's time of day in the app's zone */
  at: string
  /** the calendar day that time is on, the day the page lists it under */
  day: string
  /** its state as the page shows it, the server's words and tone (Waiting for you while `asked`) */
  display: Display
  state: RunState
  /** why it waits or why it was skipped, in words; '' when nothing holds it */
  reason: string
  /** the reason without the "Waiting" its state word says, for a row under that word */
  reason_why: string
  conversation_id: string | null
  result: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  /** the run it runs again (Retry) and its time of day, else null */
  again_of: { id: string; at: string } | null
  /** the run that runs it again and its time of day, else null */
  retried: { id: string; at: string } | null
}

/** one of the fourteen days: failed when a schedule's newest try that day failed */
export type Day = { day: string; failed: boolean }

/** a slot still to come today: its schedule's name and title, the time (UTC) and its time of day ("14:00") */
export type Planned = { schedule: string; label: string; slot: string; at: string; display: Display }
/** `planned`: every slot still to come today, the server's list; empty for another day.
    `filters`: the State options of the day's list with their labels and counts */
export type Runs = { runs: Run[]; planned: Planned[]; filters: { value: string; label: string; count: number }[]; days: Day[]; today: string }

/** GET /schedules: a field's choices and its "?" text, the server's */
export type ScheduleWords = Pick<ScheduleList, 'choices' | 'help'>

type RunDetail = { run: Run; prompt: string | null; reviewer_prompt: string | null; note: string | null }

export type Trigger = { name: string; label: string; about: string; skill: string; kind: 'event' | 'watch'; on: boolean; fired_at: string | null }

const enc = encodeURIComponent

export const routinesApi = {
  schedules: (signal?: AbortSignal) =>
    request<ScheduleList>('/schedules', { signal }),
  put: (name: string, body: ScheduleBody) =>
    request<SavedSchedule>(`/schedules/${enc(name)}`, json('PUT', body)),
  remove: (name: string) => del<{ deleted: string }>(`/schedules/${enc(name)}`),
  runNow: (name: string) => post<{ run: Run }>(`/schedules/${enc(name)}/run`),
  /** `text` filled as a run with the dates of `night` gets it (the page passes the day it shows) */
  preview: (night: string, text: string, signal?: AbortSignal) =>
    request<{ text: string }>('/schedules/preview', { ...json('POST', { night, text }), signal }).then(r => r.text),
  /** the runs of `day` (default today), the 14 days up to `until` (default today) */
  runs: (day?: string, signal?: AbortSignal, until?: string) => {
    const q = new URLSearchParams({ ...(day && { day }), ...(until && { until }) }).toString()
    return request<Runs>(`/runs${q ? `?${q}` : ''}`, { signal })
  },
  /** one schedule's newest runs, oldest first */
  runsOf: (schedule: string, signal?: AbortSignal) =>
    request<Runs>(`/runs?schedule=${enc(schedule)}`, { signal }).then(r => r.runs),
  run: (id: string, signal?: AbortSignal) => request<RunDetail>(`/runs/${enc(id)}`, { signal }),
  stop: (id: string) => post<{ run: Run }>(`/runs/${enc(id)}/stop`),
  /** a failed, skipped or stopped run runs again now, for its night */
  retry: (id: string) => post<{ run: Run }>(`/runs/${enc(id)}/retry`),
  /** what the events started: their conversations (GET /background), newest first */
  tasks: (signal?: AbortSignal) => request<{ background: EventTask[] }>('/background-conversations', { signal }).then(r => r.background),
  triggers: (signal?: AbortSignal) => request<{ triggers: Trigger[] }>('/triggers', { signal }).then(r => r.triggers),
  setTrigger: (name: string, on: boolean) =>
    request<Trigger & { applied: boolean }>(`/triggers/${enc(name)}`, json('PUT', { on })),
}

/** a server event that changes a run */
export const isRunEvent = (ev: { type: string }) => ev.type === 'run.state' || ev.type === 'approvals.changed'

/** a run that is over: Retry or Run now instead of Stop */
export const FINISHED: (RunState | 'planned')[] = ['done', 'failed', 'skipped', 'stopped']

/** a run that ended unfinished: Retry runs it again, once */
export const RETRIES: RunState[] = ['failed', 'skipped', 'stopped']

/** a day's date as a heading: "Saturday 10 October" */
export const dayWords = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'long' })

/** GET /background: the conversation a fired event started (the runs' are under their days) */
export type EventTask = {
  id: string
  title: string
  kind: 'background'
  /** the event's name */
  trigger: string | null
  working: boolean
  /** how it stands as the page shows it, the server's words and tone */
  display: Display
  last_line: string
  updated_at: string
  /** an approval waits in it or in a sub-agent of it */
  asked: boolean
}
