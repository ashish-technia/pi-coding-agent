import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, type RepoConfig, type RunSummary } from '../api'
import StatusBadge from '../components/StatusBadge'

export default function HomePage() {
  const navigate = useNavigate()
  const [issueKey, setIssueKey] = useState('')
  const [manual, setManual] = useState(false)
  const [summary, setSummary] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [repos, setRepos] = useState<RepoConfig[]>([])
  const [selected, setSelected] = useState<string[]>([])

  useEffect(() => {
    let alive = true
    const load = () => api.listRuns().then((r) => alive && setRuns(r)).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  // The configured repos and their default ticks come from repos.json via /api/config.
  useEffect(() => {
    let alive = true
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
    }
  }, [])

  const toggleRepo = (name: string) =>
    setSelected((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]))

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    const key = issueKey.trim().toUpperCase()
    if (!key) return
    setBusy(true)
    try {
      await api.startRun(key, manual && summary.trim() ? { summary, description } : undefined, selected)
      navigate(`/runs/${encodeURIComponent(key)}`)
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
          <h2>Start a run</h2>
          <p className="muted small">
            Enter a Jira issue key. The agent fetches the summary, description and comments, frames the
            requirement, and pauses for your approval at every stage.
          </p>
          <form onSubmit={submit}>
            <div className="field">
              <label>Jira issue key</label>
              <input
                value={issueKey}
                onChange={(e) => setIssueKey(e.target.value)}
                placeholder="WAAS-643"
                autoFocus
              />
            </div>
            {repos.length > 1 && (
              <div className="field">
                <label>Repositories</label>
                <p className="muted small" style={{ margin: '0 0 6px' }}>
                  The agents read and edit every repository you tick, and open one pull request per
                  repository they change.
                </p>
                {repos.map((repo) => (
                  <label
                    key={repo.name}
                    className="small"
                    style={{ display: 'flex', gap: 6, alignItems: 'baseline', marginBottom: 4 }}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(repo.name)}
                      onChange={() => toggleRepo(repo.name)}
                    />
                    <span>
                      <strong>{repo.name}</strong>{' '}
                      <span className="muted">
                        {Object.entries(repo.properties)
                          .map(([k, v]) => `${k}: ${v}`)
                          .join(' · ') || repo.path}
                      </span>
                    </span>
                  </label>
                ))}
                {selected.length === 0 && (
                  <p className="muted small" style={{ margin: '4px 0 0' }}>
                    Nothing ticked — the run will use the default repositories.
                  </p>
                )}
              </div>
            )}
            <label className="small muted" style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 10 }}>
              <input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} />
              Provide the issue text manually (no Jira access)
            </label>
            {manual && (
              <>
                <div className="field">
                  <label>Summary</label>
                  <input value={summary} onChange={(e) => setSummary(e.target.value)} />
                </div>
                <div className="field">
                  <label>Description</label>
                  <textarea value={description} onChange={(e) => setDescription(e.target.value)} />
                </div>
              </>
            )}
            {error && <div className="error-box">{error}</div>}
            <div className="actions">
              <button className="primary" disabled={busy || !issueKey.trim()}>
                {busy ? 'Starting…' : 'Start'}
              </button>
            </div>
          </form>
        </div>
      </div>
      <div className="card">
        <h2>Recent runs</h2>
        {runs.length === 0 ? (
          <p className="muted">No runs yet.</p>
        ) : (
          <table className="runs-table">
            <thead>
              <tr>
                <th>Issue</th>
                <th>Summary</th>
                <th>Status</th>
                <th>Channel</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.issue_key}>
                  <td>
                    <Link to={`/runs/${encodeURIComponent(r.issue_key)}`}>{r.issue_key}</Link>
                  </td>
                  <td>{r.summary || <span className="muted">—</span>}</td>
                  <td>
                    <StatusBadge status={r.running ? 'running' : r.status} />
                  </td>
                  <td className="muted">{r.channel}</td>
                  <td className="muted small">{new Date(r.updated_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
