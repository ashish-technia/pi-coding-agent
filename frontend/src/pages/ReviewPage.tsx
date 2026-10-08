import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, type Review } from '../api'
import PrReviewPanel from '../components/PrReviewPanel'
import StatusBadge from '../components/StatusBadge'

/** One standalone review: which commits it covered and what it found. */
export default function ReviewPage() {
  const { reviewId = '' } = useParams()
  const navigate = useNavigate()
  const [review, setReview] = useState<Review | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setReview(await api.getReview(reviewId))
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [reviewId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Poll only while it is working; a finished review never changes.
  useEffect(() => {
    if (!review?.running) return
    const t = setInterval(() => void refresh(), 1500)
    return () => clearInterval(t)
  }, [review?.running, refresh])

  async function remove() {
    if (!window.confirm('Delete this review and its findings?')) return
    setBusy(true)
    try {
      await api.deleteReview(reviewId)
      navigate('/reviews')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      setBusy(false)
    }
  }

  if (!review) return error ? <div className="error-box">{error}</div> : <p className="muted">Loading…</p>

  const result = review.result
  const tools = review.activity.filter((e) => e.ev === 'tool')
  return (
    <div>
      <p className="small">
        <Link to="/reviews">Reviews</Link> / {review.id}
      </p>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>
          {review.branch} <StatusBadge status={review.running ? 'running' : review.status} />
        </h2>
        <p className="small muted">
          {review.repos.join(', ')}
          {review.issue_key && <> · judged against {review.issue_key}</>} · started by {review.started_by || 'unknown'}{' '}
          on {new Date(review.created_at).toLocaleString()}
        </p>
        {Object.keys(review.commits ?? {}).length > 0 && (
          <ul className="small">
            {Object.entries(review.commits).map(([repo, c]) => (
              <li key={repo}>
                <strong>{repo}</strong>: <code>{c.base.slice(0, 10)}</code> → <code>{c.head.slice(0, 10)}</code>
              </li>
            ))}
          </ul>
        )}
        {review.running && (
          <p className="small">
            <span className="badge running">
              <span className="dot" />
              {review.node_label ?? 'Working'}
            </span>{' '}
            {tools.length > 0 && <span className="muted">{tools.length} tool call(s) so far</span>}
          </p>
        )}
        {review.status === 'failed' && <pre className="error-box small">{review.error}</pre>}
        {review.status === 'interrupted' && (
          <div className="error-box">The service restarted while this review was running. Start it again.</div>
        )}
        {error && <div className="error-box">{error}</div>}
        {!review.running && (
          <div className="actions">
            <button disabled={busy} onClick={remove}>
              Delete
            </button>
          </div>
        )}
      </div>

      {result && (
        <PrReviewPanel
          review={{
            enabled: true,
            skipped: false,
            summary: result.summary,
            findings: result.findings,
            resolved: [],
            not_reviewed: result.not_reviewed,
            dropped_findings: result.dropped_findings.length,
            fix_rounds: 0,
            max_fix_rounds: 0,
            // A standalone review only reports; there is no run to send findings back to.
            fix_available: false,
            standalone: true,
          }}
          busy={false}
          onFix={() => {}}
        />
      )}
    </div>
  )
}
