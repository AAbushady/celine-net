/* Waiting for you (server/celine/waits.py, GET /api/waits): everything that needs you,
   the server's list and count, asked again on waits.changed. A request is its card; every
   other item a row: a chat it started for you, a mission in Needs you, a lost login.
   `unread`: the Intercom threads with something you have not read, the Intercom's own count. */
import { request } from './api'
import type { Approval } from './settingsApi'
import { useResource } from './useResource'

export type Wait = { group: string } & (
  | { kind: 'approval'; approval: Approval }
  | { kind: 'chat' | 'mission' | 'login'; id: string; title: string; line: string; link: string | null })

export const waitsApi = {
  list: (signal?: AbortSignal) => request<{ items: Wait[]; count: number; unread: number }>('/waits', { signal }),
}

export const isWaitsEvent = (ev: { type: string }) => ev.type === 'waits.changed'

/** the server's list, asked again on waits.changed */
export function useWaits() {
  return useResource(signal => waitsApi.list(signal), [], { events: isWaitsEvent })
}
