/* Place: a place is its page. A place with more than one list (Routines: Runs, Schedules,
   Triggers) names them as `sections`: the head of its top page shows them as tabs where its
   title would be (ui/Head.tsx), so they are in view at every width and take no column.

   One place has a list beside its page: the agent's chats. Its `side` is that list, with
   its own PlaceHead as `head` (Search, New chat, More). The list is --sidebar-w wide, on the
   canvas, split from the page by a hairline. Below 1024 px it is a Drawer (from the left on
   a tablet, a sheet from the bottom edge on a phone), opened by the panel button every
   PlaceHead of the page shows there; a link in it closes it. With `fold` the list folds
   away to the left on a wide screen: its head starts with Close sidebar, the page's head
   with Open sidebar while it is away, and this device keeps which. The page takes its new
   width at once and glides from where its middle was, with the list; its heads' actions on
   the right edge hold still.

     <Place title="Routines" current={view} sections={[{ id: 'runs', label: 'Runs', count: 3, onSelect: ... }, ...]}>
       <PlaceHead level={1} title="Runs" ... />
       ...
     </Place> */
import { createContext, useContext, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { usePref } from '../lib/prefs'
import { cx } from './cx'
import { curve, ms, reduced } from './motion'
import { Drawer } from './Drawer'
import { useNarrow } from './phone'

/** one of a place's lists: its tab in the head of the place's top page */
export type PlaceSection = { id: string; label: string; count?: number; onSelect: () => void }

/** a head's panel button: its label and what it does; `always` shows it at every width */
type Panel = { label: string; onClick: () => void; always: boolean }
/** what a PlaceHead inside a place draws for it: the panel button of a place with a list
    beside its page, the tabs of a place with sections (`current`: the open one, none on a
    page under them) */
type PlaceInfo = { title: string; panel: Panel | null; sections?: PlaceSection[]; current?: string | null }
const PlaceCtx = createContext<PlaceInfo | null>(null)

/** the place a head is in, or null */
export const usePlace = () => useContext(PlaceCtx)

export function Place({ title, sections, current, head, side, label, className, open: shown, onOpen, fold, children }: {
  /** the place's name: the name of its tabs and of its list */
  title: string
  /** the place's lists, when it has more than one */
  sections?: PlaceSection[]
  /** the id of the section on screen; none on a page under them (a run, a note) */
  current?: string | null
  /** the head of the list beside the page: its own PlaceHead */
  head?: ReactNode
  /** the list beside the page (the agent's chats) */
  side?: ReactNode
  label?: string
  className?: string
  /** the phone's drawer, when the page decides (a route that shows it open) */
  open?: boolean
  onOpen?: (open: boolean) => void
  /** the list folds away on a wide screen; the key this device keeps it under */
  fold?: string
  /** the page */
  children: ReactNode
}) {
  const narrow = useNarrow()
  const [own, setOwn] = useState(false)
  const open = shown ?? own
  const setOpen = onOpen ?? setOwn
  const [away, setFolded] = usePref(`place:${fold ?? ''}:folded`, false)
  const folded = !!fold && !narrow && away
  // the page's box before the list goes or comes; after, it glides from its middle there
  const main = useRef<HTMLDivElement>(null)
  const was = useRef<DOMRect | null>(null)
  const setAway = (on: boolean) => { was.current = main.current?.getBoundingClientRect() ?? null; setFolded(on) }
  useLayoutEffect(() => {
    const a = was.current, el = main.current
    was.current = null
    if (!a || !el || reduced()) return
    const b = el.getBoundingClientRect()
    const dx = a.left + a.width / 2 - (b.left + b.width / 2)
    // on the list's own curve: in on --dur-slow --ease, out on --dur --ease-exit
    const timing = folded ? { duration: ms('--dur'), easing: curve('--ease-exit') } : { duration: ms('--dur-slow'), easing: curve('--ease') }
    el.animate([{ translate: `${dx}px 0` }, { translate: '0 0' }], timing)
    // a head's actions sit on the right edge, which stays: they hold still
    for (const acts of el.querySelectorAll('.place-head__acts')) {
      acts.animate([{ translate: `${-dx}px 0` }, { translate: '0 0' }], { ...timing, composite: 'add' })
    }
  }, [folded])

  if (side == null) {
    return (
      <PlaceCtx.Provider value={{ title, panel: null, sections, current }}>
        <div className={cx('place', 'place--plain', className)}>
          <div ref={main} className="place__main">{children}</div>
        </div>
      </PlaceCtx.Provider>
    )
  }

  // a link or a row in the drawer goes somewhere: the drawer gets out of the way
  const closeOnGo = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('a[href], .list-row')) setOpen(false)
  }
  // inside the list a PlaceHead is the list's own: Close sidebar where it folds
  const inSide: Panel | null = fold && !narrow ? { label: 'Close sidebar', onClick: () => setAway(true), always: true } : null
  const body = <>
    {head && narrow && head}
    <div className="place-side__body">{side}</div>
  </>
  const page: Panel = folded ? { label: 'Open sidebar', onClick: () => setAway(false), always: true }
    : { label: label ?? title, onClick: () => setOpen(true), always: false }
  return (
    <PlaceCtx.Provider value={{ title, panel: page, sections, current }}>
      <div className={cx('place', folded && 'place--folded', className)}>
        <PlaceCtx.Provider value={{ title, panel: inSide }}>
          {narrow ? (
            <Drawer side="left" open={open} title={label ?? title} onClose={() => setOpen(false)} label={label ?? title} fill>
              <div className="place-side place-side--drawer" onClickCapture={closeOnGo}>{body}</div>
            </Drawer>
          ) : (
            <aside className="place-side" aria-label={label ?? title} inert={folded || undefined}>
              {head}
              {body}
            </aside>
          )}
        </PlaceCtx.Provider>
        <div ref={main} className="place__main">{children}</div>
      </div>
    </PlaceCtx.Provider>
  )
}
