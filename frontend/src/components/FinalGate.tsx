import { useState } from 'react'
import type { Decision, Pending } from '../api'
import DiffViewer from './DiffViewer'
import PartialReviewNote from './PartialReviewNote'
import PrReviewPanel from './PrReviewPanel'

type FinalPending = Extract<Pending, { type: 'final_review' }>

export default function FinalGate({ pending, busy, onDecide }: { pending: FinalPending; busy: boolean; onDecide: (d: Decision) => void }) {
  const [showPr, setShowPr] = useState(false)
  const [title, setTitle] = useState(pending.suggested_pr_title)
  const [description, setDescription] = useState(pending.suggested_pr_description)

  return (
    <div className="card success">
      <h2>Final changes passed review</h2>
      <p className="small muted">
        {pending.phases_completed}/{pending.phases_total} phase(s) implemented, {pending.diff_lines} diff lines.
        {pending.review_feedback && (
          <>
            {' '}
            Reviewer: {pending.review_feedback}
          </>
        )}
      </p>
      <p className="small">
        {pending.files_changed.map((f) => (
          <span className="tag" key={f}>
            {f}
          </span>
        ))}
      </p>
      <PartialReviewNote files={pending.review_omitted_files} />
      {pending.pr_review && (
        <PrReviewPanel
          // A new review (after a fix round) starts from its own findings.
          key={`${pending.pr_review.fix_rounds}-${pending.pr_review.findings.length}`}
          review={pending.pr_review}
          busy={busy}
          onFix={(findings, notes) => onDecide({ action: 'fix', findings, notes })}
        />
      )}
      <DiffViewer diffs={pending.diffs} />

      {showPr && (
        <div style={{ marginTop: 14 }}>
          <div className="field">
            <label>Pull request title</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label>Pull request description</label>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} style={{ minHeight: 160 }} />
          </div>
        </div>
      )}

      <div className="actions">
        {pending.pr_enabled ? (
          <button
            className="success"
            disabled={busy || (showPr && !title.trim())}
            onClick={() => {
              if (!showPr) return setShowPr(true)
              onDecide({ action: 'create_pr', pr_title: title.trim(), pr_description: description })
            }}
          >
            {showPr ? '✓ Commit, push and create PR' : 'Create pull request…'}
          </button>
        ) : (
          <span className="muted small">PR creation is disabled in this environment (PR_CREATION_ENABLED).</span>
        )}
        <button disabled={busy} onClick={() => onDecide({ action: 'finish' })}>
          Finish without a PR
        </button>
      </div>
    </div>
  )
}
