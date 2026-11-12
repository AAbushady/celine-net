/* The model files the agent asked for, on Media while Images is on and its ComfyUI
   starts on demand (server/celine/comfyui/model_files.py, GET /api/model-files): a row per
   file that came by request (its hub's title, the preset it was asked for, where it came
   from, its size, the day it came) with Remove behind a confirm, and a row per request
   still on its way with the server's state and how far it downloaded; the free space on
   the models disk. It follows `model_files.changed` on the global feed. */
import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { bytes, dayLabel, errorText } from '../../lib/format'
import { MODEL_FILE_EVENTS, mediaApi, type ModelFile } from '../../lib/media'
import { useOnline } from '../../lib/socket'
import { useResource } from '../../lib/useResource'
import { ConfirmDialog, EmptyState, IconButton, InlineError, ListRow, LoadingRow, Section, StateWord } from '../../ui'

const name = (f: ModelFile) => f.title ?? f.path.split('/').pop() ?? f.path

export function ModelFiles() {
  const online = useOnline()
  const list = useResource(() => mediaApi.modelFiles(), [], { events: MODEL_FILE_EVENTS })
  const [removing, setRemoving] = useState<ModelFile | null>(null)
  const files = list.data?.files ?? []
  return (
    <Section title="Model files">
      {list.data?.free != null && <StateWord tone="muted">{`${bytes(list.data.free)} free`}</StateWord>}
      {list.error && !list.data ? <InlineError message={list.error} onRetry={list.reload} />
        : !list.data ? <LoadingRow label="Loading model files" />
        : files.length === 0 ? <EmptyState compact>No model files</EmptyState>
        : (
          <div className="rows">
            {files.map(f => {
              const where = [f.preset_label && `For ${f.preset_label}`, f.site && `from ${f.site}`].filter(Boolean).join(', ')
              const busy = f.state !== 'ready'
              return (
                <ListRow key={f.request ?? f.path} density="lg" title={name(f)} tooltip={f.path}
                         running={f.state === 'fetching' || f.state === 'listing'}
                         subtitle={busy ? <StateWord tone={f.display.tone}>{f.display.words}</StateWord> : where || undefined}
                         detail={busy ? where || undefined : undefined}
                         meta={f.size != null ? (f.done != null && busy ? `${bytes(f.done)} of ${bytes(f.size)}` : bytes(f.size)) : undefined}
                         aside={!busy && f.at ? dayLabel(Date.parse(f.at)) : undefined}
                         progress={f.state === 'fetching' && f.size ? (f.done ?? 0) / f.size : undefined}
                         trailing={f.removable
                           ? <IconButton icon={Trash2} size="sm" label={`Remove ${name(f)}`} disabled={!online}
                                         onClick={() => setRemoving(f)} />
                           : undefined} />
              )
            })}
          </div>
        )}
      {removing && (
        <ConfirmDialog title={`Remove ${name(removing)}?`} danger confirmLabel="Remove"
                       body="The file is deleted from the models disk."
                       onClose={() => setRemoving(null)}
                       onConfirm={async () => {
                         try { await mediaApi.removeModelFile(removing.path); setRemoving(null); list.reload() }
                         catch (e) { return errorText(e) }
                       }} />
      )}
    </Section>
  )
}
