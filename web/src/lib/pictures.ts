/* The inline picture block in the story and the chat (components/PictureBlock): one block
   from start to end. The state is the server's (a batch item, a picture beat); these
   helpers only put it into words. */
import { post } from './api'
import { stamp } from './format'
import { clearedFile, madeFile, type Batch, type BatchItem } from './media'

/** a failed, stopped or cleared item made again as it was asked: the batch runs for it,
    its events bring the block from the prompt to the picture again */
export const retryItem = (bid: string, index: number) =>
  post<Batch>(`/batches/${encodeURIComponent(bid)}/items/${index}/retry`)

/** a picture made or being made: its file once done */
type PictureFile = { id: string; sha256: string; filename: string; width?: number | null; height?: number | null }

/** the inline picture block, one block from start to end: the prompt while it runs, the
    server's line when it failed, the picture when done; cleared, what made it and when */
export type PictureState = {
  status: 'running' | 'failed' | 'done' | 'cleared'
  prompt: string | null
  /** running: what it does now ("In the queue"), failed: the server's sentence,
      cleared: the server's word for it and when ("Cleared Fri 9 Oct, 19:00") */
  line: string | null
  picture: PictureFile | null
  /** how it was made: the preset, its size and seed */
  preset?: string | null
  width?: number | null
  height?: number | null
  seed?: number | null
  /** done: kept from clearing (the server's) */
  kept?: boolean
  /** while it is made: the steps of the node at work, when it counts them (the server's) */
  steps?: { value: number; max: number } | null
}

/** what an item made that was cleared, as a line: the server's word and when */
export function clearedLine(i: BatchItem): string | null {
  const life = clearedFile(i)?.life
  return life ? `${life.display.words} ${stamp(life.at)}` : null
}

/** one item of an image batch as a picture block */
export function pictureOf(i: BatchItem): PictureState {
  const file = madeFile(i)
  const cleared = clearedLine(i)
  const status = file ? 'done' : cleared ? 'cleared' : i.state === 'failed' || i.state === 'stopped' ? 'failed' : 'running'
  return {
    status, prompt: i.prompt ?? null, picture: file,
    line: cleared ?? (i.state === 'stopped' ? 'Stopped.' : i.state === 'failed' ? i.error ?? null : i.note ?? null),
    preset: i.preset ?? null, width: i.attachment?.width ?? i.width ?? null, height: i.attachment?.height ?? i.height ?? null,
    seed: i.seed ?? null, kept: file?.life?.state === 'kept',
    steps: i.state === 'running' ? i.steps ?? null : null,
  }
}

/** what a picture shows, for its title (the Viewer's, its tooltip): the prompt's first
    phrase, never a file name */
export function titleOf(prompt: string | null | undefined, fallback = 'Image'): string {
  const first = (prompt ?? '').split(/[,.;\n]/)[0]!.trim()
  if (!first) return fallback
  const t = first.length > 80 ? `${first.slice(0, 79).trimEnd()}…` : first
  return t.charAt(0).toUpperCase() + t.slice(1)
}

/** "anima-turbo, 832 x 1216, seed 1234": how it was made, for the Viewer's "..." */
export function madeWords(p: { preset?: string | null; width?: number | null; height?: number | null; seed?: number | null }): string | null {
  const parts = [p.preset, p.width && p.height ? `${p.width} x ${p.height}` : null,
                 p.seed != null ? `seed ${p.seed}` : null].filter(Boolean)
  return parts.length ? parts.join(', ') : null
}

/** a failure you have to fix (a server that does not answer, a preset that is gone):
    where in Settings */
export function needsOwner(line: string | null): boolean {
  return !!line && /refused the connection|did not answer within|is unreachable|stopped answering|Settings, /.test(line)
}
