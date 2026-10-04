import { useMemo, useState } from 'react'
import type { Decision, Requirements, ScopeCheck } from '../api'
import ListEditor from './ListEditor'

interface Props {
  requirements: Requirements
  scopeCheck: ScopeCheck | null
  busy: boolean
  onDecide: (d: Decision) => void
}

const LIST_FIELDS: Array<{ key: keyof Requirements; label: string }> = [
  { key: 'goals', label: 'Goals' },
  { key: 'acceptance_criteria', label: 'Acceptance criteria' },
  { key: 'in_scope', label: 'In scope' },
  { key: 'out_of_scope', label: 'Out of scope' },
  { key: 'assumptions', label: 'Assumptions' },
  { key: 'open_questions', label: 'Open questions' },
]

export default function RequirementsGate({ requirements, scopeCheck, busy, onDecide }: Props) {
  const [draft, setDraft] = useState<Requirements>(requirements)
  const [notes, setNotes] = useState('')
  const [showNotes, setShowNotes] = useState(false)

  const outOfScope = useMemo(
    () => (scopeCheck?.findings ?? []).filter((f) => f.verdict === 'out_of_scope'),
    [scopeCheck],
  )
  const unclear = useMemo(() => (scopeCheck?.findings ?? []).filter((f) => f.verdict === 'unclear'), [scopeCheck])
  const flagged = useMemo(() => new Set(outOfScope.map((f) => f.item)), [outOfScope])

  const setList = (key: keyof Requirements, items: string[]) => setDraft({ ...draft, [key]: items })

  function removeFlagged() {
    const next = { ...draft }
    for (const f of LIST_FIELDS) {
      next[f.key] = (draft[f.key] as string[]).filter((v) => !flagged.has(v)) as never
    }
    setDraft(next)
  }

  const clean = (r: Requirements): Requirements => ({
    ...r,
    goals: r.goals.filter(Boolean),
    acceptance_criteria: r.acceptance_criteria.filter(Boolean),
    in_scope: r.in_scope.filter(Boolean),
    out_of_scope: r.out_of_scope.filter(Boolean),
    assumptions: r.assumptions.filter(Boolean),
    open_questions: r.open_questions.filter(Boolean),
  })

  return (
    <div className={`card ${outOfScope.length ? 'warn' : ''}`}>
      <h2>Requirements awaiting your approval</h2>
      <p className="muted small">
        Framed from the issue description and comments. Edit anything; additions beyond the issue's scope are
        checked before planning starts.
      </p>

      {outOfScope.length > 0 && (
        <div className="notice">
          <strong>Scope warning.</strong> These edits go beyond what the Jira issue asks for:
          <ul>
            {outOfScope.map((f, i) => (
              <li key={i}>
                <strong>{f.item}</strong> <span className="muted">({f.kind})</span>: {f.reason}
              </li>
            ))}
          </ul>
          <div className="actions">
            <button type="button" onClick={removeFlagged} disabled={busy}>
              Remove out-of-scope items
            </button>
            <button
              type="button"
              className="danger"
              disabled={busy}
              onClick={() => onDecide({ action: 'approve', requirements: clean(draft), acknowledge_scope: true })}
            >
              Keep them anyway and continue
            </button>
          </div>
        </div>
      )}
      {unclear.length > 0 && (
        <div className="notice">
          <strong>Unclear scope:</strong>
          <ul>
            {unclear.map((f, i) => (
              <li key={i}>
                {f.item}: {f.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="field">
        <label>Title</label>
        <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
      </div>
      <div className="field">
        <label>Problem</label>
        <textarea value={draft.problem} onChange={(e) => setDraft({ ...draft, problem: e.target.value })} />
      </div>
      {LIST_FIELDS.map((f) => (
        <ListEditor
          key={f.key}
          label={f.label}
          items={draft[f.key] as string[]}
          onChange={(items) => setList(f.key, items)}
          flagged={flagged}
        />
      ))}
      {draft.sources.length > 0 && (
        <div className="field">
          <label>Sources the analyst used</label>
          <ul className="small muted">
            {draft.sources.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}

      {showNotes && (
        <div className="field">
          <label>What should the analyst change?</label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} autoFocus />
        </div>
      )}

      <div className="actions">
        <button
          className="success"
          disabled={busy}
          onClick={() => onDecide({ action: 'approve', requirements: clean(draft) })}
        >
          ✓ Approve and plan
        </button>
        <button
          disabled={busy || (showNotes && !notes.trim())}
          onClick={() => {
            if (!showNotes) return setShowNotes(true)
            onDecide({ action: 'revise', notes: notes.trim() })
          }}
        >
          ✎ {showNotes ? 'Send revision notes' : 'Ask for a revision'}
        </button>
        <button className="danger" disabled={busy} onClick={() => onDecide({ action: 'cancel' })}>
          ✕ Cancel run
        </button>
      </div>
    </div>
  )
}
