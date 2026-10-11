// Display-only progress of a background cpipe run, shown above the input of the chat that started it.
// Lines come from the run itself (plugin.cpipe.progress); nothing here reaches the model.
import { COMPOSER_AREAS, host, useValue } from '@hermes/plugin-sdk'
import { useSyncExternalStore } from 'react'
import { jsx } from 'react/jsx-runtime'

const lines = new Map() // session id → latest line
const listeners = new Set()
let version = 0
const KEEP_FINAL_MS = 60000

function show(session, text, done) {
  if (text) lines.set(session, text)
  else lines.delete(session)
  version += 1
  listeners.forEach(fn => fn())
  if (done) {
    setTimeout(() => lines.get(session) === text && show(session, '', false), KEEP_FINAL_MS)
  }
}

function Progress() {
  useSyncExternalStore(fn => (listeners.add(fn), () => listeners.delete(fn)), () => version)
  const stored = useValue(host.state.focusedStoredSessionId)
  const runtime = useValue(host.state.focusedSessionId)
  const text = lines.get(stored) ?? lines.get(runtime)
  if (!text) return null
  return jsx('div', {
    role: 'status',
    title: text,
    className: 'truncate px-3 py-1 text-[0.6875rem] text-(--ui-text-tertiary)',
    children: `cpipe · ${text}`
  })
}

export default {
  id: 'cpipe',
  name: 'cpipe',
  register(ctx) {
    ctx.onEvent('plugin.cpipe.progress', ({ payload }) => {
      if (payload && typeof payload.session_id === 'string' && payload.session_id && typeof payload.text === 'string') {
        show(payload.session_id, payload.text, payload.done === true)
      }
    })
    ctx.register({ id: 'progress', area: COMPOSER_AREAS.top, render: () => jsx(Progress, {}) })
  }
}
