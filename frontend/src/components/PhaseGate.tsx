import type { Decision, Pending } from '../api'
import DiffViewer from './DiffViewer'

type PhasePending = Extract<Pending, { type: 'phase_gate' }>

export default function PhaseGate({ pending, busy, onDecide }: { pending: PhasePending; busy: boolean; onDecide: (d: Decision) => void }) {
  return (
    <div className="card success">
      <h2>
        Phase {pending.phase_index + 1} of {pending.phases_total} passed review
      </h2>
      {pending.review_feedback && (
        <p className="small">
          <strong>Reviewer:</strong> {pending.review_feedback}
        </p>
      )}
      <p className="small">
        Files changed so far:{' '}
        {pending.files_changed.map((f) => (
          <span className="tag" key={f}>
            {f}
          </span>
        ))}
      </p>
      <DiffViewer diffs={pending.diffs} />
      <div className="actions">
        <button className="success" disabled={busy} onClick={() => onDecide({ action: 'continue' })}>
          ▶ Implement phase {pending.phase_index + 2}
        </button>
        <button disabled={busy} onClick={() => onDecide({ action: 'stop' })}>
          ■ Stop here and go to final review
        </button>
      </div>
    </div>
  )
}
