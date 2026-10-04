import { useState } from 'react'
import type { AgentResult, Decision } from '../api'
import PlanDetails from './PlanDetails'

interface Props {
  plan: AgentResult
  phasesTotal: number
  busy: boolean
  onDecide: (d: Decision) => void
}

export default function PlanGate({ plan, phasesTotal, busy, onDecide }: Props) {
  const [notes, setNotes] = useState('')
  const [showNotes, setShowNotes] = useState(false)
  const canPhase = phasesTotal > 1 && plan.phases.length > 1

  return (
    <div className="card">
      <h2>Plan awaiting your approval</h2>
      <p className="muted small">
        Produced in plan mode: the agent read the repository but could not modify it. Every path below was
        opened and verified.
      </p>
      <PlanDetails plan={plan} />

      {showNotes && (
        <div className="field">
          <label>Refinement notes or questions for the planner</label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} autoFocus />
        </div>
      )}

      <div className="actions">
        <button className="success" disabled={busy} onClick={() => onDecide({ action: 'approve', mode: 'all' })}>
          ✓ Approve, implement all
        </button>
        {canPhase && (
          <button
            className="primary"
            disabled={busy}
            onClick={() => onDecide({ action: 'approve', mode: 'phased' })}
            title="Implement one phase at a time; you review after each"
          >
            ✓ Approve, phase by phase ({plan.phases.length})
          </button>
        )}
        <button
          disabled={busy || (showNotes && !notes.trim())}
          onClick={() => {
            if (!showNotes) return setShowNotes(true)
            onDecide({ action: 'refine', notes: notes.trim() })
          }}
        >
          ✎ {showNotes ? 'Send to planner' : 'Refine'}
        </button>
        <button className="danger" disabled={busy} onClick={() => onDecide({ action: 'reject' })}>
          ✕ Reject
        </button>
      </div>
    </div>
  )
}
