import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, type RepoConfig, type ReviewSummary } from '../api'
import StatusBadge from '../components/StatusBadge'

/** Review a branch on its own: no run, no gates. The findings stay in this list until deleted. */
export default function ReviewsPage() {
  const navigate = useNavigate()
  const [reviews, setReviews] = useState<ReviewSummary[]>([])
  const [repos, setRepos] = useState<RepoConfig[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [branch, setBranch] = useState('')
  const [issueKey, setIssueKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => api.listReviews().then((r) => alive && setReviews(r)).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    api
      .config()
      .then((cfg) => {
        if (!alive) return
        setRepos(cfg.repos)
        setSelected(cfg.repos.filter((r) => r.default_selected).map((r) => r.name))
      })
      .catch(() => {})
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  const toggleRepo = (name: string) =>
    setSelected((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const review = await api.startReview(branch.trim(), selected, issueKey.trim() || undefined)
      navigate(`/reviews/${encodeURIComponent(review.id)}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid-2">
      <div>
        <div className="card">
          <h2>Review a branch</h2>
          <p className="muted small">
            A read-only agent reviews everything the branch changes since it left the target branch. Nothing is
            edited, committed or posted to Bitbucket; the findings appear here.
          </p>
          <form onSubmit={submit}>
            <div className="field">
              <label>Branch</label>
              <input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="feature/WAAS-643-retry" autoFocus />
            </div>
            {repos.length > 0 && (
              <div className="field">
                <label>Repositories</label>
                <p className="muted small" style={{ margin: '0 0 6px' }}>
                  The branch must exist in every repository you tick.
                </p>
                {repos.map((repo) => (
                  <label key={repo.name} className="small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={selected.includes(repo.name)} onChange={() => toggleRepo(repo.name)} />
                    <strong>{repo.name}</strong> <span className="muted">target: {repo.target_branch}</span>
                  </label>
                ))}
              </div>
            )}
            <div className="field">
              <label>Jira issue key (optional)</label>
              <input value={issueKey} onChange={(e) => setIssueKey(e.target.value)} placeholder="WAAS-643" />
              <p className="muted small" style={{ margin: '4px 0 0' }}>
                With an issue, the change is judged against it. Without one, the review covers the team rules,
                correctness and security only.
              </p>
            </div>
            {error && <div className="error-box">{error}</div>}
            <div className="actions">
              <button className="primary" disabled={busy || !branch.trim() || selected.length === 0}>
                {busy ? 'Starting…' : 'Review'}
              </button>
            </div>
          </form>
        </div>
      </div>
      <div className="card">
        <h2>Reviews</h2>
        {reviews.length === 0 ? (
          <p className="muted">No reviews yet.</p>
        ) : (
          <table className="runs-table">
            <thead>
              <tr>
                <th>Branch</th>
                <th>Repositories</th>
                <th>Status</th>
                <th>Findings</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {reviews.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={`/reviews/${encodeURIComponent(r.id)}`}>{r.branch}</Link>
                  </td>
                  <td>{r.repos.join(', ')}</td>
                  <td>
                    <StatusBadge status={r.running ? 'running' : r.status} />
                  </td>
                  <td>{r.status === 'done' ? `${r.findings} (${r.must} must)` : <span className="muted">—</span>}</td>
                  <td className="muted small">{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
