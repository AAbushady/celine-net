import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/tokens.css'
import './styles/base.css'
// the kit before the views' own CSS, so a view can adjust a kit class
import './ui/ui.css'
import { App } from './App'
import { isTyping } from './lib/keys'

// the page is the visible part of the window: iOS ignores interactive-widget and lays the
// keyboard over the page, so the height follows the visual viewport (pinch zoom left alone).
// An on-screen keyboard is up while the visible part is clearly shorter than the tallest it
// has been at this width (Android shrinks the window with it, so the window says nothing)
// and a text field has the focus (a split screen or a shrunk window is no keyboard):
// only then the phone's bottom bar steps aside (App.css data-keyboard). Installed on the
// home screen, iOS leaves the home indicator's strip out of the visual viewport (WebKit bug
// 254868) while 100vh there is the whole screen: without a keyboard the page is 100vh, or
// it would end that strip short of the bottom, under a bar that clears the strip itself
const vv = window.visualViewport
if (vv) {
  let wide = 0, tall = 0
  const touch = matchMedia('(pointer: coarse)')
  const installed = matchMedia('(display-mode: standalone)')
  const fit = () => {
    if (Math.abs(vv.scale - 1) > 0.01) return
    const root = document.documentElement
    if (window.innerWidth !== wide) { wide = window.innerWidth; tall = 0 }
    tall = Math.max(tall, vv.height, window.innerHeight)
    const keyboard = touch.matches && tall - vv.height > 120 && isTyping(document.activeElement)
    root.style.setProperty('--vh', installed.matches && !keyboard ? '100vh' : `${Math.round(vv.height)}px`)
    if (keyboard) root.dataset.keyboard = ''
    else delete root.dataset.keyboard
    if (window.scrollY) window.scrollTo(0, 0)
  }
  vv.addEventListener('resize', fit)
  vv.addEventListener('scroll', fit)
  document.addEventListener('focusin', fit)
  // the focus has not landed yet while it leaves
  document.addEventListener('focusout', () => setTimeout(fit))
  fit()
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
