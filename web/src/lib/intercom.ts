/* The intercom: messages between you, its conversations and the app, in threads like
   mail. The api client (GET /intercom/threads,
   GET /intercom?thread=, POST /intercom, POST /intercom/read). Pages hear changes as
   `intercom.state` on the feed (lib/bus). No EventSource here: each one holds
   one of the six HTTP/1.1 connections a browser keeps per site, so a few open tabs
   would starve every request. /intercom/stream is for other clients (curl, a CLI). */
import { createContext } from 'react'
import { json, post, query, request, type Attachment } from './api'

/** how many messages wait for you, the server's (the rail's count, App); null until it
    answered */
export const WaitingCount = createContext<number | null>(null)

/** where a message or a thread came from, from the server: the conversation that sent it
 *  (a chat, a mission, a schedule's run, a background task, a sub-agent) or the app's
 *  sender (a named token, a connector, the app); `link` opens it */
export type Source = {
  kind: 'chat' | 'mission' | 'run' | 'task' | 'sub-agent' | 'token' | 'connector' | 'app'
  title: string | null
  link: string | null
}

/** one message; addresses as stored: `user`, `agent:<cid>`, `team:<cid>`, `all`, `system:<name>` */
export type IntercomMessage = {
  id: number
  kind: Kind
  from: string
  /** the sender by name, from the server: your name, a mission's or sub-agent's name,
   *  its name for its chats, a token's name */
  from_name: string | null
  /** comma separated */
  to: string
  /** each address of `to` by name, the same way */
  to_names: Record<string, string | null>
  subject: string | null
  text: string
  created_at: string
  due_at: string | null
  until_at: string | null
  reply_to: number | null
  thread: number
  /** it starts a turn where it lands */
  wake: boolean
  data: Record<string, unknown>
  /** the files it carries */
  attachments: Attachment[]
  /** where it came from; null for your own */
  source: Source | null
  state: 'pending' | 'sent' | 'cancelled'
  /** it waits for you: landed and not read yet */
  unread: boolean
}

export type Thread = {
  thread: number
  /** who it is with, by name (everyone in it but you), from the server */
  name: string
  subject: string | null
  /** where it comes from */
  source: Source | null
  count: number
  /** messages in it that wait for you */
  unread: number
  /** the newest message */
  last: Omit<IntercomMessage, 'unread'>
}

/** who a block of threads is with, its newest threads first */
type ThreadGroup = {
  /** by name */
  name: string
  /** the same, as stored addresses */
  addresses: string[]
  threads: number
  /** threads with a message that waits for you */
  waiting: number
  items: Thread[]
  /** threads not in items */
  more: number
}

/** a page of GET /intercom/threads */
export type ThreadsPage = { groups: ThreadGroup[]; next: string | null }

/** `user` for the threads where something came to you, `all` for every thread you are in,
    `guard` for Bastet's, else a name or an address: every thread with them that you are in */
type ThreadsWith = string

/** a bug report it filed: each one is its own thread (bugreports/store.py) */
type BugReport = { id: number; title: string; severity: 'high' | 'medium' | 'low'; state: 'open' | 'closed'; thread: number }

export const KINDS = ['message', 'brief', 'report', 'job', 'notice', 'trigger'] as const
export type Kind = typeof KINDS[number]
/** the kinds a thread you are in holds: jobs and triggers go between her own conversations */
export const LISTED_KINDS: Kind[] = ['message', 'brief', 'report', 'notice']

export const intercomApi = {
  threads: (q: { with: ThreadsWith; kind: Kind | null; unread?: boolean; archived?: boolean; limit?: number; cursor?: string | null }, signal?: AbortSignal) =>
    request<ThreadsPage>(`/intercom/threads${query(q)}`, { signal }),
  /** a thread's messages, oldest first, where it comes from and whether it is archived */
  thread: (id: number, signal?: AbortSignal) =>
    request<{ messages: IntercomMessage[]; source: Source | null; archived: boolean }>(`/intercom?thread=${id}`, { signal }),
  /** what `from` (a stored address, like a mission's) wrote that waits for you, oldest first */
  waitingFrom: (from: string, signal?: AbortSignal) =>
    request<{ messages: IntercomMessage[] }>(`/intercom?from=${encodeURIComponent(from)}&waiting=1`, { signal }).then(r => r.messages),
  /** the bug report a thread is, or null */
  bugOf: (thread: number, signal?: AbortSignal) =>
    request<{ reports: BugReport[] }>(`/bugreports?thread=${thread}`, { signal }).then(r => r.reports[0] ?? null),
  /** Continue in a chat: a new chat with it that carries the thread; what waits in it is read */
  toChat: (thread: number) =>
    post<{ conversation_id: string }>(`/intercom/threads/${thread}/conversations`).then(r => r.conversation_id),
  /** a bug report's Close, on its thread: what waits in it is read too */
  closeBug: (id: number, why = '') => why ? request<BugReport>(`/bugreports/${id}/close`, json('POST', { why })) : post<BugReport>(`/bugreports/${id}/close`),
  /** from you the server takes a reply in a thread with Bastet and a reminder into a conversation
      (`in` "30 min" or `at` "19:00", to `agent:<id>`): you talk in chats */
  send: (body: { to: string; text: string; subject?: string; reply_to?: number; in?: string; at?: string; until?: string }) =>
    request<{ intercom_id: number }>('/intercom', json('POST', body)),
  read: (ids: number[]) =>
    request<{ read: number[] }>('/intercom/read', json('POST', { ids })),
  /** everything that waits for you, read: Dismiss all */
  readAll: () => post<{ read: number[] }>('/intercom/read?all=1'),
  /** a scheduled message that has not landed yet */
  cancel: (id: number) => post<{ ok: boolean }>(`/intercom/${id}/stop`),
  /** what waits for you in a thread, then read: Dismiss */
  dismiss: async (thread: number) => {
    const ids = (await intercomApi.thread(thread)).messages.filter(m => m.unread).map(m => m.id)
    if (ids.length) await intercomApi.read(ids)
  },
  /** out of the lists until something newer lands in them; what waits in them is read */
  archive: (threads: number[]) =>
    request<{ archived: number[] }>('/intercom/threads/archive', json('POST', { threads })).then(r => r.archived),
  /** Archive read: what the server finds done */
  archiveRead: () => post<{ archived: number[] }>('/intercom/threads/archive?read=1').then(r => r.archived),
  restore: (threads: number[]) =>
    request<{ restored: number[] }>('/intercom/threads/restore', json('POST', { threads })).then(r => r.restored),
}

/** a kind as one word, and as the name of its filter */
export const KIND_WORD: Record<Kind, string> = {
  message: 'Message', brief: 'Instructions', report: 'Result', job: 'Job', notice: 'Notice', trigger: 'Trigger',
}
export const KIND_PLURAL: Record<Kind, string> = {
  message: 'Direct messages', brief: 'Instructions', report: 'Results', job: 'Jobs', notice: 'Notices', trigger: 'Triggers',
}

/** waiting to land */
export const isScheduled = (m: Pick<IntercomMessage, 'due_at' | 'state'>) => !!m.due_at && m.state === 'pending'

/** an address as you read it (the server names it) */
export function addressName(addr: string, names: Record<string, string | null>): string {
  if (addr === 'all') return 'Everyone'
  const name = names[addr] ?? addr
  return addr.startsWith('team:') ? `${name} and its sub-agents` : name
}

/** your own messages read "You", in the list and in the thread */
export const senderName = (m: Pick<IntercomMessage, 'from' | 'from_name'>) => m.from === 'user' ? 'You' : m.from_name ?? m.from

export const isIntercomEvent = (ev: { type: string }) => ev.type === 'intercom.state'
