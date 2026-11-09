import { createContext, useCallback, useEffect, useRef, useState, type RefObject } from 'react'

/** within this many px of the bottom a scroll down pins again, and further up the jump to
    the bottom shows: a few lines, since a finger stops near a bottom that moves while text
    comes, and a phone's line is near 30 px */
export const REPIN = 96

/** the distance from the bottom: 0 at the bottom */
export const gapOf = (el: { scrollHeight: number; clientHeight: number; scrollTop: number }) =>
  Math.max(0, el.scrollHeight - el.clientHeight - el.scrollTop)

/** a scroll event the user made upward: the top went up and the view is not clamped at the
    bottom (content that shrinks lowers the top by itself, which is no scroll of yours) */
export const scrolledUp = (before: number, top: number, gap: number) => top < before - 1 && gap > 1

/** where the jump glides this frame, from `top` toward `target`: a quarter of the way,
    at least 2 px, arriving exactly */
export const glide = (top: number, target: number) =>
  target - top <= 2 ? target : top + Math.max(2, Math.ceil((target - top) / 4))

/** a message whose place on screen is kept while what is around it changes (a version, an
    edit, Regenerate): its id, given before the action */
export const KeepPlace = createContext<(id: string) => void>(() => {})

/** A transcript pinned to its bottom while it grows: each growth, of the text or of the box
    (the composer, a keyboard), is followed at once, so the motion is the text's own. Only you
    unpin it: a scroll up with the wheel, a finger, the keys or the scrollbar, selecting
    text; a scroll the layout makes by itself never does. A click that opens a fold pauses
    following until your next scroll. A scroll down
    to within REPIN px pins it again. Not following and more than REPIN px above the bottom,
    `below` holds (the jump shows), and `unseen` says a new message came meanwhile. `fresh`:
    what makes a message new (`said`: the messages with words). `reset`: another conversation,
    pinned at once. `observe`: when the scroller or its content element may have changed.
    `jump()` glides to the bottom (your jump); `jump({ smooth: false })` is there at once (a
    send); a flick still coasting does not stop either. `keep(id)` holds the message with data-id `id` where
    it is on screen. The scroller says `data-unpinned` while it is not pinned (native
    anchoring keeps your reading place then). */
/** the messages that say something: every user message and every reply with words (a
    round of tool calls with no text is not a new message) */
export function said(messages: { role: string; content: string }[]): number {
  return messages.reduce((n, m) => n + (m.role === 'user' || (m.role === 'assistant' && m.content.trim()) ? 1 : 0), 0)
}
export function useStickToBottom(ref: RefObject<HTMLElement | null>, fresh: unknown[],
                                 reset: unknown[], observe: unknown[]) {
  const pinned = useRef(true)
  const paused = useRef(false)
  const [unseen, setUnseen] = useState(false)
  const [below, setBelow] = useState(false)
  // the top a set of ours left, until a scroll event shows it
  const ours = useRef<number | null>(null)
  // a finger or a pointer is down in the transcript (a drag, the scrollbar, a selection)
  const held = useRef(false)
  const lastTop = useRef(0)
  const raf = useRef<number | null>(null)
  // the element above the kept message and its offset in the scroller
  const anchor = useRef<{ id: string; top: number } | null>(null)

  const stop = () => {
    if (raf.current != null) { cancelAnimationFrame(raf.current); raf.current = null }
  }

  const set = (el: HTMLElement, top: number) => {
    el.scrollTop = top
    lastTop.current = ours.current = el.scrollTop
  }

  const pin = useCallback((on: boolean) => {
    pinned.current = on
    const el = ref.current
    if (el) el.toggleAttribute('data-unpinned', !on)
    if (on) { paused.current = false; anchor.current = null; setUnseen(false); setBelow(false) }
  }, [ref])

  const offsetOf = (el: HTMLElement, id: string) => {
    const at = el.querySelector(`[data-id="${CSS.escape(id)}"]`)
    return at ? at.getBoundingClientRect().top - el.getBoundingClientRect().top : null
  }

  /** after a change of size: to the bottom at once while pinned, else the kept place */
  const settle = useCallback(() => {
    const el = ref.current
    if (!el) return
    if (pinned.current && !paused.current) {
      if (raf.current == null) set(el, el.scrollHeight - el.clientHeight)
      return
    }
    const a = anchor.current
    const now = a && offsetOf(el, a.id)
    if (a && now != null && Math.abs(now - a.top) >= 1) set(el, el.scrollTop + now - a.top)
    setBelow(gapOf(el) > REPIN)
  }, [ref])

  const release = useCallback(() => {
    stop()
    pin(false)
  }, [pin])

  const onScroll = useCallback(() => {
    const el = ref.current
    if (!el) return
    const top = el.scrollTop
    const before = lastTop.current
    lastTop.current = top
    // where a set of ours left it (one event may stand for several): nothing of yours. By the
    // place, not a flag, so a scroll of yours in the same event still counts
    const mine = ours.current
    ours.current = null
    if (mine != null && Math.abs(top - mine) < 1) return
    const gap = gapOf(el)
    // a scroll up lets go only while you hold the transcript (a finger, the scrollbar); the
    // wheel, a drag and the keys let go by themselves (below). Without you it is the layout's
    // own: a box that grows under it (the keyboard going down, the composer emptied after a
    // send) clamps its top lower while text comes. While your jump glides, a scroll up is a
    // flick from before it, still coasting.
    if (scrolledUp(before, top, gap) && held.current && raf.current == null) {
      if (pinned.current) release()
      setBelow(gap > REPIN)
      return
    }
    if (gap <= REPIN) { if (!pinned.current || paused.current) pin(true) }
    else setBelow(!pinned.current || paused.current)
  }, [ref, release, pin])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    // a gesture of yours ends a pause and lets go of a kept place: the view is yours now
    const gesture = () => {
      anchor.current = null
      if (paused.current) { paused.current = false; pinned.current = gapOf(el) <= REPIN; el.toggleAttribute('data-unpinned', !pinned.current) }
    }
    const onWheel = (e: WheelEvent) => { gesture(); if (e.deltaY < 0) release() }
    let touchY = 0
    const onTouchStart = (e: TouchEvent) => { held.current = true; gesture(); touchY = e.touches[0]?.clientY ?? 0 }
    const onTouchMove = (e: TouchEvent) => { if ((e.touches[0]?.clientY ?? 0) > touchY + 5) release() }
    const onKeyDown = (e: KeyboardEvent) => {
      gesture()
      if (e.key === 'PageUp' || e.key === 'ArrowUp' || e.key === 'Home') release()
    }
    // a pan ends a touch's pointer early (pointercancel): the touch itself says when it ends
    const onPointerDown = () => { held.current = true }
    const onLetGo = () => { held.current = false }
    // selecting text inside: following would move it under the pointer
    const onSelection = () => {
      const s = document.getSelection()
      if (held.current && s && !s.isCollapsed && s.anchorNode && el.contains(s.anchorNode)) release()
    }
    // a click that opens a fold: its growth does not move the view
    const onClick = (e: MouseEvent) => {
      const t = (e.target as Element).closest?.('[aria-expanded="false"], summary')
      if (t && el.contains(t) && pinned.current) paused.current = true
    }
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('touchstart', onTouchStart, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: true })
    el.addEventListener('keydown', onKeyDown, { passive: true })
    el.addEventListener('pointerdown', onPointerDown, { passive: true })
    el.addEventListener('click', onClick, { capture: true })
    window.addEventListener('pointerup', onLetGo, { passive: true })
    window.addEventListener('touchend', onLetGo, { passive: true })
    window.addEventListener('touchcancel', onLetGo, { passive: true })
    document.addEventListener('selectionchange', onSelection)
    return () => {
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
      el.removeEventListener('keydown', onKeyDown)
      el.removeEventListener('pointerdown', onPointerDown)
      el.removeEventListener('click', onClick, { capture: true })
      window.removeEventListener('pointerup', onLetGo)
      window.removeEventListener('touchend', onLetGo)
      window.removeEventListener('touchcancel', onLetGo)
      document.removeEventListener('selectionchange', onSelection)
      stop()
    }
  }, [ref, release, ...observe]) // eslint-disable-line react-hooks/exhaustive-deps

  // a new message while you read above: "New messages"
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    if (!pinned.current || paused.current) setUnseen(true)
  }, fresh) // eslint-disable-line react-hooks/exhaustive-deps

  // another conversation: at its bottom at once, pinned
  useEffect(() => {
    stop()
    pin(true)
    const el = ref.current
    if (el) set(el, el.scrollHeight - el.clientHeight)
  }, reset) // eslint-disable-line react-hooks/exhaustive-deps

  // the content and the box itself: a reply growing, a picture or a fold, the composer
  // growing or a keyboard opening
  useEffect(() => {
    const el = ref.current
    const inner = el?.firstElementChild
    if (!el || !inner) return
    const ro = new ResizeObserver(settle)
    ro.observe(inner)
    ro.observe(el)
    return () => { ro.disconnect(); stop() }
  }, observe) // eslint-disable-line react-hooks/exhaustive-deps

  const jump = useCallback((opts?: { smooth?: boolean } | unknown) => {
    const el = ref.current
    if (!el) return
    stop()
    pin(true)
    const smooth = !(opts && typeof opts === 'object' && (opts as { smooth?: boolean }).smooth === false)
      && !matchMedia('(prefers-reduced-motion: reduce)').matches
    // your jump glides to the moving bottom and lands on it; at once, it is there this
    // frame. Each frame sets the top again until it holds: a flick still coasting (iOS)
    // overrides a set, and its scroll up does not let go meanwhile (onScroll)
    const step = () => {
      const e = ref.current
      if (!e || !pinned.current) { raf.current = null; return }
      const target = e.scrollHeight - e.clientHeight
      set(e, smooth ? glide(e.scrollTop, target) : target)
      raf.current = e.scrollTop >= target - 1 ? null : requestAnimationFrame(step)
    }
    if (smooth) raf.current = requestAnimationFrame(step)
    else step()
  }, [ref, pin])

  const keep = useCallback((id: string) => {
    const el = ref.current
    const at = el?.querySelector(`[data-id="${CSS.escape(id)}"]`)
    if (!el || !at) return
    // the element above it stays the same through the change; the first message keeps the top
    let above = at.previousElementSibling as HTMLElement | null
    while (above && !above.dataset.id) above = above.previousElementSibling as HTMLElement | null
    const ids = el.querySelectorAll('[data-id]')
    // the last message at the bottom stays followed: its change is what you read next
    if (ids[ids.length - 1] === at && pinned.current) return
    stop()
    pin(false)
    anchor.current = above?.dataset.id ? { id: above.dataset.id, top: offsetOf(el, above.dataset.id)! } : null
  }, [ref, pin])

  return { onScroll, below, unseen, jump, keep }
}
