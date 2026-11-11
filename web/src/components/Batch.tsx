/* A batch of images or music, in the batch's order, which never changes. An image is the
   one picture block (PictureBlock) from the prompt while it is made to the picture or the
   sentence why it failed; a track its title and player, or the server's words. What an
   item made can be kept (Keep, Unkeep); a cleared one shows when, what made it and Retry.
   Used in its replies (ToolRow ToolLines) and the Generate surface on the Models page;
   both follow the batch's events and never guess an item's state. */
import { createContext, useState } from 'react'
import { Bookmark, RefreshCw } from 'lucide-react'
import type { Message, ToolCall } from '../lib/api'
import { asBatch, clearedFile, fileUrl, madeFile, mediaApi, type Batch, type BatchItem } from '../lib/media'
import { clearedLine, madeWords, pictureOf, retryItem } from '../lib/pictures'
import { useAction } from '../lib/useAction'
import { useOnline } from '../lib/socket'
import { Button, IconButton, InlineError, cx } from '../ui'
import { PictureBlock } from './PictureBlock'
import './Batch.css'

/** an image to show full size: one it made (with what it was made from), a browser shot */
export type Made = {
  id: string; sha256: string; filename: string; width: number; height: number
  prompt?: string; preset?: string; seed?: number
}
/** "anima-turbo, 832 x 1216, seed 1234" */
export const madeDetails = (img: Made): string | null => madeWords(img)

/** a done image item, still there, as an image to show */
function madeOf(b: Batch, i: BatchItem): Made | null {
  const a = madeFile(i)
  if (b.kind !== 'images' || !a) return null
  return { id: a.id, sha256: a.sha256, filename: a.filename, width: a.width ?? i.width ?? 0,
           height: a.height ?? i.height ?? 0, prompt: i.prompt ?? undefined, preset: i.preset ?? undefined,
           seed: i.seed }
}
export const madeImages = (b: Batch) => b.items.flatMap(i => madeOf(b, i) ?? [])

/* In a conversation, a click opens the conversation's own viewer, which flips through
   every image made there (Celine.tsx). */
type GalleryContextValue = {
  images: { img: Made }[]
  openImage: (idOrSha: string, fallback?: Made) => void
}
export const GalleryContext = createContext<GalleryContextValue | null>(null)

/** every image made in a conversation, in order (a read of a batch shows it again: once) */
export function collectGeneratedImages(messages: Message[], toolCalls: Record<string, ToolCall[]>): { img: Made }[] {
  const seen = new Set<string>()
  const out: { img: Made }[] = []
  for (const m of messages) {
    for (const c of toolCalls[m.id] ?? []) {
      const b = asBatch(c.display)
      for (const img of b ? madeImages(b) : []) {
        if (!seen.has(img.sha256)) { seen.add(img.sha256); out.push({ img }) }
      }
    }
  }
  return out
}

/** "2 of 4": how many are done */
export const doneWords = (b: Batch) => `${b.items.filter(i => i.state === 'done').length} of ${b.items.length}`

export function StopBatch({ batch }: { batch: Batch }) {
  const online = useOnline()
  const stop = useAction(() => mediaApi.stop(batch.id))
  if (batch.state !== 'running') return null
  return (
    <>
      <Button size="sm" variant="ghost" loading={stop.busy} disabled={!online} onClick={() => void stop.run()}>Stop</Button>
      {stop.error && <InlineError compact message={stop.error} />}
    </>
  )
}

/** Keep and Unkeep on what an item made: pressed while it is kept (the server's) */
export function KeepButton({ batch, item }: { batch: Batch; item: BatchItem }) {
  const online = useOnline()
  const kept = madeFile(item)?.life?.state === 'kept'
  const keep = useAction(() => mediaApi.keep(batch.id, item.index, !kept))
  if (!madeFile(item)) return null
  return (
    <>
      <IconButton icon={Bookmark} size="sm" label={kept ? 'Unkeep' : 'Keep'} pressed={kept} fill={kept}
                  className={cx('batch__keep', kept && 'is-on')} loading={keep.busy} disabled={!online}
                  onClick={() => void keep.run()} />
      {keep.error && <InlineError compact message={keep.error} />}
    </>
  )
}

/** Retry on a cleared, failed or stopped item outside its gallery (a track's row): the
    batch's events bring the item back */
export function RetryItem({ batch, item }: { batch: Batch; item: BatchItem }) {
  const online = useOnline()
  const retry = useAction(() => retryItem(batch.id, item.index))
  if (batch.state === 'running') return null
  return (
    <>
      <Button size="sm" variant="secondary" icon={RefreshCw} loading={retry.busy} disabled={!online}
              onClick={() => void retry.run()}>Retry</Button>
      {retry.error && <InlineError compact message={retry.error} />}
    </>
  )
}

/** a batch's pictures or tracks */
export function BatchGallery({ batch: shown }: { batch: Batch }) {
  // a Retry shows its items waiting at once, until the batch's next event replaces it
  const [optimistic, setOptimistic] = useState<{ on: Batch; batch: Batch } | null>(null)
  const batch = optimistic && optimistic.on === shown ? optimistic.batch : shown
  const stop = useAction(() => mediaApi.stop(batch.id))
  const handleRetry = async (index: number) => {
    const on = shown
    setOptimistic({ on, batch: {
      ...batch,
      state: 'running',
      items: batch.items.map(it => (it.state === 'failed' || it.state === 'stopped' || it.index === index)
        ? { ...it, state: 'waiting', error: undefined, seconds: undefined, note: undefined, attachment: undefined }
        : it),
    } })
    try {
      const updated = await retryItem(batch.id, index)
      setOptimistic(o => (o && o.on === on ? { on, batch: updated } : o))
    } catch (e) {
      setOptimistic(null)
      throw e
    }
  }
  if (batch.kind === 'images') {
    const items = batch.items
    return (
      <div className="batch batch--images">
        {items.map(i => (
          <PictureBlock key={i.index} pic={pictureOf(i)}
                        onRetry={batch.state !== 'running' ? () => handleRetry(i.index) : undefined}
                        onStop={batch.state === 'running' ? () => void stop.run() : undefined}
                        onKeep={kept => mediaApi.keep(batch.id, i.index, kept)} />
        ))}
        {stop.error && <InlineError compact message={stop.error} />}
      </div>
    )
  }
  return (
    <div className="batch batch--music">
      {batch.items.map(i => (
        <Track key={i.index} batch={batch} item={i}
               onRetry={batch.state !== 'running' ? () => handleRetry(i.index) : undefined} />
      ))}
    </div>
  )
}

/* a track: its title, player and Keep once done; the server's words while it waits or
   runs, why it failed, or when it was cleared with its title and Retry */
function Track({ batch, item: i, onRetry }: { batch: Batch; item: BatchItem; onRetry?: () => Promise<unknown> }) {
  const retry = useAction(async () => { await onRetry?.() })
  const file = madeFile(i)
  const cleared = clearedFile(i)
  const state = cleared ? 'cleared' : i.state
  const again = onRetry && (cleared || i.state === 'failed' || i.state === 'stopped')
  return (
    <div className={`batch__tile batch__tile--${state}`}>
      {file ? (
        <div className="batch__track">
          <span className="batch__head">
            <span className="batch__title" title={i.style ?? undefined}>{i.title ?? file.filename}</span>
            <KeepButton batch={batch} item={i} />
          </span>
          <audio controls preload="metadata" src={fileUrl(file)} />
        </div>
      ) : (
        <span className="batch__say">
          <span className="batch__words">{cleared ? clearedLine(i) : i.state === 'failed' ? i.error : i.state === 'stopped' ? 'Stopped' : i.note}</span>
          {cleared && <span className="batch__title" title={i.style ?? undefined}>{i.title ?? cleared.filename}</span>}
          {again && <Button size="sm" variant="secondary" icon={RefreshCw} loading={retry.busy} onClick={() => void retry.run()}>Retry</Button>}
          {retry.error && <InlineError compact message={retry.error} />}
        </span>
      )}
    </div>
  )
}
