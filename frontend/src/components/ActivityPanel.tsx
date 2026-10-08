import { useEffect, useRef, useState } from 'react'
import type { ActivityEvent, RunStatus } from '../api'

function fmt(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m ? `${m}m ${s.toString().padStart(2, '0')}s` : `${s}s`
}

function describe(e: ActivityEvent): { icon: string; text: string; cls?: string } {
  switch (e.ev) {
    case 'node':
      return { icon: '▶', text: e.label ?? e.node ?? 'node', cls: 'muted' }
    case 'start':
      return { icon: '⚙', text: `${e.mode} mode · ${e.model} · thinking ${e.thinking} · tools: ${e.tools}` }
    case 'model_turn':
      return { icon: '…', text: `model turn ${e.turn}: thinking / deciding next action`, cls: 'muted' }
    case 'assistant':
      return { icon: '💬', text: e.text ?? '' }
    case 'tool':
      return { icon: '🔧', text: `${e.tool} ${e.args ?? ''}` }
    case 'tool_done':
      return e.error
        ? { icon: '✗', text: `${e.tool} failed`, cls: 'danger' }
        : { icon: '✓', text: `${e.tool} → ${e.size ?? 0} chars`, cls: 'muted' }
    case 'blocked':
      return { icon: '⛔', text: `${e.tool} blocked: ${e.text ?? ''}`, cls: 'warn' }
    case 'usage':
      return {
        icon: '$',
        text: `session used ${e.input ?? 0} input / ${e.output ?? 0} output tokens · $${(e.cost ?? 0).toFixed(3)}`,
        cls: 'muted',
      }
    case 'validation':
      return { icon: '⚠', text: `plan validation round ${e.round}: ${(e.problems ?? []).join(' | ')}`, cls: 'warn' }
    case 'done':
      return {
        icon: '🏁',
        text: `${e.mode} finished · tools ${e.tools} · ${e.files_read} file(s) read · ${e.plan_steps} step(s) · ${e.corrections} correction(s)`,
      }
    case 'error':
      return { icon: '✗', text: e.text ?? 'error', cls: 'danger' }
    case 'llm_call':
      return { icon: '🧠', text: `${e.stage}: ${e.text ?? 'calling model'}` }
    case 'llm_done':
      return { icon: '✓', text: `${e.stage}: ${e.text ?? 'done'}`, cls: 'muted' }
    default:
      return { icon: '·', text: JSON.stringify(e) }
  }
}

export default function ActivityPanel({ run }: { run: RunStatus }) {
  const [open, setOpen] = useState(true)
  const listRef = useRef<HTMLDivElement>(null)
  const events = run.activity ?? []

  useEffect(() => {
    if (open && run.running && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [events.length, open, run.running])

  if (events.length === 0 && !run.running) return null

  const toolCalls = events.filter((e) => e.ev === 'tool').length
  const filesRead = new Set(events.filter((e) => e.ev === 'tool' && e.tool === 'read').map((e) => e.args)).size
  const elapsed = run.node_elapsed_s ?? 0
  const budget = run.pi_timeout_s
  const pct = budget ? Math.min(100, Math.round((elapsed / budget) * 100)) : null

  return (
    <div className="card activity">
      <div className="activity-head">
        <div>
          <strong>Agent activity</strong>{' '}
          <span className="muted small">
            {toolCalls} tool call(s), {filesRead} file(s) read
            {run.running && run.node_label ? ` · ${run.node_label}` : ''}
          </span>
        </div>
        <div className="small muted">
          {run.running && (
            <>
              elapsed {fmt(elapsed)}
              {budget ? ` of ${fmt(budget)} limit` : ''}
            </>
          )}
          <button type="button" className="small" style={{ marginLeft: 10 }} onClick={() => setOpen((v) => !v)}>
            {open ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
      {run.running && pct !== null && (
        <div className="progress">
          <div className={`bar${pct > 85 ? ' warn' : ''}`} style={{ width: `${pct}%` }} />
        </div>
      )}
      {open && (
        <div className="activity-list" ref={listRef}>
          {events.map((e) => {
            const d = describe(e)
            return (
              <div className={`activity-row ${d.cls ?? ''}`} key={e.seq}>
                <span className="when">{e.t !== undefined ? fmt(e.t) : ''}</span>
                <span className="icon">{d.icon}</span>
                <span className="what">{d.text}</span>
              </div>
            )
          })}
          {run.running && events.length === 0 && <div className="muted small">Waiting for the first event…</div>}
        </div>
      )}
    </div>
  )
}
