/* Images and music (server/celine/comfyui/api.py): the presets, and batches, the one way
   anything is made. A batch has one slot per item, in order; the server fills them and
   says so with `images.batch` / `music.batch` ({batch}) on the conversation's stream
   and the global feed. The agent's calls carry the same batch as their display. What an
   item made has its life (temporary, kept, cleared), the server's; Keep and Unkeep ask
   for a change and the batch's event brings it. */
import { del, json, post, request } from './api'
import type { Batch, BatchList, MadeFile, ModelFileList, Removed } from './api.gen'
import type { ServerEvent } from './bus'
import type { FormField } from '../ui'

export type { Batch, BatchItem, MadeFile, ModelFile, ModelFileList } from './api.gen'

export type Kind = 'images' | 'music'

/** a file kept with a tool call (the server's `files` block) */
export type MediaFile = {
  id: string; sha256: string; filename: string; media_type: string; bytes: number
  /** images */
  width?: number | null; height?: number | null
}

/** what an item made, while it is there: done and not cleared */
export const madeFile = (i: Batch['items'][number]): MadeFile | null =>
  i.state === 'done' && i.attachment && i.attachment.life?.state !== 'cleared' ? i.attachment : null

/** what an item made that was cleared: its file, with when */
export const clearedFile = (i: Batch['items'][number]): MadeFile | null =>
  i.state === 'done' && i.attachment?.life?.state === 'cleared' ? i.attachment : null

export type Param = {
  name: string
  type: 'text' | 'integer' | 'number' | 'boolean' | 'choice'
  label: string
  description: string
  default?: unknown
  choices?: string[]
  min?: number; max?: number; step?: number
}

export type Preset = {
  id: string
  kind: Kind
  label: string
  family: string
  task: 'generate' | 'edit' | 'music'
  good_at: string
  prompting: string
  example: string
  parameters: Param[]
  /** images */
  size?: { width: number; height: number; step: number; max_pixels: number }
  negative?: boolean
  /** how many images an edit takes (0 for new images) */
  images?: number
  catalog?: { models: boolean; max_loras: number } | null
  /** its place in the role, null when the role does not list it */
  order: number | null
  /** "provider/preset" in its role, null likewise */
  ref: string | null
  /** shipped as it came, a shipped one the user edited, or the user's own upload */
  origin: 'shipped' | 'edited' | 'own'
  /** an own one the agent proposed and the user approved */
  proposed: boolean
  /** the editor: its fields and their values (comfyui/presets.py fields, values) */
  fields: FormField[]
  values: Record<string, unknown>
}

/** the settings role that lists each kind's presets */
export const ROLE: Record<Kind, string> = { images: 'image_generation', music: 'music_generation' }
const EVENT: Record<Kind, string> = { images: 'images.batch', music: 'music.batch' }

/** a batch, from a call's display or an event's `batch` */
export function asBatch(v: unknown): Batch | null {
  const b = v as Batch | null | undefined
  return b && typeof b.id === 'string' && Array.isArray(b.items) ? b : null
}

/** the batch an event carries, of any kind or of `kind` */
export function batchEvent(ev: ServerEvent, kind?: Kind): Batch | null {
  if (kind ? ev.type !== EVENT[kind] : ev.type !== EVENT.images && ev.type !== EVENT.music) return null
  return asBatch((ev as { batch?: unknown }).batch)
}

/** an attachment's file under its own name; `download` saves even an image */
export const fileUrl = (f: { id: string }, download = false) => `/api/attachments/${f.id}/file${download ? '?download=1' : ''}`
/** the width of a picture on a message or a tool row (.msg__picture, .tool__picture: 18rem) */
export const TILE = 288
/** a picture for a tile `px` CSS pixels wide on this screen: the server's smaller copy
    (it snaps the width to a few); the Viewer and a download take fileUrl, the file itself */
export const tileUrl = (f: { id: string }, px: number) =>
  `/api/attachments/${f.id}/file?w=${Math.ceil(px * (window.devicePixelRatio || 1))}`

export const mediaApi = {
  presets: (kind: Kind) => request<{ presets: Preset[] }>(`/generation-presets?kind=${kind}`),
  batches: (kind: Kind, limit = 12) => request<BatchList>(`/batches?kind=${kind}&limit=${limit}`),
  start: (kind: Kind, items: Record<string, unknown>[], count: number) =>
    request<Batch>('/batches', json('POST', { kind, items, count })),
  stop: (id: string) =>
    post<{ stopped: boolean }>(`/batches/${encodeURIComponent(id)}/stop`),
  /** what item `index` made, kept or let go */
  keep: (id: string, index: number, kept: boolean) =>
    request<Batch>(`/batches/${encodeURIComponent(id)}/items/${index}`, json('PATCH', { kept })),
  /** a workflow in API format with placeholders: a new preset of the user's */
  upload: (kind: Kind, label: string, workflow: string) =>
    request<Preset>('/generation-presets', json('POST', { kind, label, workflow })),
  edit: (id: string, values: Record<string, unknown>) =>
    request<Preset>(`/generation-presets/${encodeURIComponent(id)}`, json('PUT', { values })),
  replaceWorkflow: (id: string, workflow: string) =>
    request<Preset>(`/generation-presets/${encodeURIComponent(id)}/workflow`, json('PUT', { workflow })),
  reset: (id: string) => post<Preset>(`/generation-presets/${encodeURIComponent(id)}/reset`),
  remove: (id: string) => del<{ deleted: boolean }>(`/generation-presets/${encodeURIComponent(id)}`),
  /** the model files the agent asked for, from the comfy helper (comfyui/model_files.py) */
  modelFiles: () => request<ModelFileList>('/model-files'),
  removeModelFile: (path: string) =>
    del<Removed>(`/model-files/${path.split('/').map(encodeURIComponent).join('/')}`),
}

/** the global events that change the list of model files */
export const MODEL_FILE_EVENTS = (ev: ServerEvent) => ev.type === 'model_files.changed'

/** a preset's workflow with its placeholders, as a file */
export const workflowUrl = (id: string) => `/api/generation-presets/${encodeURIComponent(id)}/workflow`
