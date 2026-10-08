import { useState } from 'react'
import type { PrReview, ReviewFinding } from '../api'

const where = (f: ReviewFinding) => {
  const file = f.repo ? `${f.repo}/${f.file}` : f.file
  if (!file) return null
  return f.line ? `${file}:${f.line}` : file
}

const severityClass: Record<ReviewFinding['severity'], string> = { must: 'danger', should: 'warn', note: 'muted' }

/**
 * The review of the whole change at the final gate: what it found, what it did not look at,
 * and (while fix rounds are left) a way to send chosen findings back to the coding agent.
 */
export default function PrReviewPanel({
  review,
  busy,
  onFix,
}: {
  review: PrReview
  busy: boolean
  onFix: (findings: number[], notes: string) => void
}) {
  // `must` findings start ticked: they are what a fix is usually for.
  const [chosen, setChosen] = useState<number[]>(() =>
    review.findings.filter((f) => f.severity === 'must').map((f) => f.number),
  )
  const [notes, setNotes] = useState('')

  if (!review.enabled) return null
  if (review.skipped) {
    return (
      <div className="error-box" style={{ margin: '12px 0' }}>
        The review of the whole change did not run: it failed and the run was continued without it. Nothing below
        was checked against the requirement by the PR reviewer.
      </div>
    )
  }

  const toggle = (n: number) => setChosen((prev) => (prev.includes(n) ? prev.filter((x) => x !== n) : [...prev, n]))
  const roundsLeft = review.max_fix_rounds - review.fix_rounds

  return (
    <div className="card" style={{ margin: '12px 0' }}>
      <h3 style={{ marginTop: 0 }}>Review of the whole change</h3>
      {review.summary && <p className="small">{review.summary}</p>}
      {review.resolved.length > 0 && (
        <p className="small muted">Fixed since the last review: finding(s) {review.resolved.join(', ')}.</p>
      )}
      {review.findings.length === 0 ? (
        <p className="small muted">No findings.</p>
      ) : (
        <ul className="small" style={{ listStyle: 'none', paddingLeft: 0 }}>
          {review.findings.map((f) => (
            <li key={f.number} style={{ marginBottom: 8 }}>
              <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                {review.fix_available && (
                  <input
                    type="checkbox"
                    checked={chosen.includes(f.number)}
                    onChange={() => toggle(f.number)}
                    aria-label={`Fix finding ${f.number}`}
                  />
                )}
                <span>
                  <strong>{f.number}.</strong> <span className={`tag ${severityClass[f.severity]}`}>{f.severity}</span>{' '}
                  <span className="muted">{f.category.replace('_', ' ')}</span>
                  {where(f) && (
                    <>
                      {' '}
                      <code>{where(f)}</code>
                    </>
                  )}
                  <br />
                  {f.claim}
                  {f.suggestion && (
                    <>
                      <br />
                      <span className="muted">Suggested fix: {f.suggestion}</span>
                    </>
                  )}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {review.not_reviewed.length > 0 && (
        <p className="small warn">
          Not reviewed (the reviewer never opened them): {review.not_reviewed.join(', ')}
        </p>
      )}
      {review.dropped_findings > 0 && (
        <p className="small muted">
          {review.dropped_findings} finding(s) were discarded because they cited a file the reviewer had not opened.
        </p>
      )}

      {review.fix_available ? (
        <>
          <div className="field">
            <label>Anything to add for the fix (optional)</label>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} style={{ minHeight: 60 }} />
          </div>
          <div className="actions">
            <button disabled={busy || (chosen.length === 0 && !notes.trim())} onClick={() => onFix(chosen, notes.trim())}>
              ↻ Fix {chosen.length ? `${chosen.length} finding(s)` : 'with these notes'}
            </button>
            <span className="muted small">
              Sends them back to the coding agent, then reviews the change again. {roundsLeft} round(s) left.
            </span>
          </div>
        </>
      ) : (
        review.findings.length > 0 && (
          <p className="small muted">
            The change was sent back {review.fix_rounds} time(s), which is the limit. Create the pull request or
            finish.
          </p>
        )
      )}
    </div>
  )
}
