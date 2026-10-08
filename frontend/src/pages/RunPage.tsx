import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Decision, type RunStatus } from '../api'
import UsageSummary from '../components/UsageSummary'
import ActivityPanel from '../components/ActivityPanel'
import DiffViewer from '../components/DiffViewer'
import FinalGate from '../components/FinalGate'
import PhaseGate from '../components/PhaseGate'
import PlanDetails from '../components/PlanDetails'
import PlanGate from '../components/PlanGate'
import RequirementsGate from '../components/RequirementsGate'
import StatusBadge from '../components/StatusBadge'
import Stepper from '../components/Stepper'

export default function RunPage() {
  const { issueKey = '' } = useParams()
  const [run, setRun] = useState<RunStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const r = await api.getRun(issueKey)
      setRun(r)
      return r
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      return null
    }
  }, [issueKey])

  // Poll every second while running, every 5 s otherwise.
  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const tick = async () => {
      const r = await refresh()
      if (!alive) return
      timer = window.setTimeout(tick, r?.running ? 1000 : 5000)
    }
    tick()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [refresh])

  async function decide(d: Decision) {
    setBusy(true)
    setError(null)
    try {
      if (!run?.pending) return
      setRun(await api.decide(issueKey, run.pending.gate_id, d))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      // 409: the run moved on (another tab or a second click answered first). Show where it is now.
      if (e instanceof ApiError && e.status === 409) void refresh()
    } finally {
      setBusy(false)
    }
  }

  async function retry(skipPrReview = false) {
    setBusy(true)
    setError(null)
    try {
      setRun(await api.retry(issueKey, skipPrReview))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!run) return <p className="muted">{error ?? 'Loading…'}</p>
  const issue = run.issue_details

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 12 }}>
        <h1 style={{ margin: 0 }}>
          <Link to="/">Runs</Link> / {run.issue}
        </h1>
        <StatusBadge status={run.running ? 'running' : run.status} />
        {run.channel === 'jira' && <span className="badge">Jira-driven</span>}
        {issue?.url && (
          <a href={issue.url} target="_blank" rel="noreferrer" className="small">
            Open in Jira ↗
          </a>
        )}
      </div>
      {issue && <p className="muted">{issue.summary}</p>}

      <Stepper run={run} />

      {run.running && (
        <div className="notice">
          <span className="badge running">
            <span className="dot" />
            {run.node_label ?? 'Working'}
          </span>
          {run.status === 'coding' && run.iteration != null && run.iteration > 0 && run.review_feedback && (
            <div className="small" style={{ marginTop: 6 }}>
              Retry {run.iteration + 1}/{run.max_iterations}: addressing "{run.review_feedback}"
            </div>
          )}
          {run.execution_mode === 'phased' && (
            <div className="small muted" style={{ marginTop: 6 }}>
              Phase {(run.phase_index ?? 0) + 1} of {run.phases_total}
            </div>
          )}
        </div>
      )}

      <ActivityPanel run={run} />
      <UsageSummary usage={run.usage} />

      {error && <div className="error-box" style={{ marginBottom: 12 }}>{error}</div>}

      {run.status === 'stuck_error' && (
        <div className="card danger">
          <h2>Run is stuck</h2>
          <pre className="small">Stuck on {run.stuck_on?.join(', ')}: {run.error}</pre>
          <p className="small muted">Fix the cause (API key, timeout, repository state) and retry from the checkpoint.</p>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => retry()}>
              ↻ Retry
            </button>
            {run.stuck_on?.includes('pr_review') && (
              <button disabled={busy} onClick={() => retry(true)} title="The final gate will say the change was not reviewed">
                Continue without the PR review
              </button>
            )}
          </div>
        </div>
      )}

      {/* --- Gates -------------------------------------------------------- */}
      {!run.running && run.pending?.type === 'requirements_approval' && (
        <RequirementsGate
          key={JSON.stringify(run.pending.scope_check)}
          requirements={run.pending.requirements}
          scopeCheck={run.pending.scope_check}
          busy={busy}
          onDecide={decide}
        />
      )}
      {!run.running && run.pending?.type === 'plan_approval' && (
        <PlanGate plan={run.pending.plan} phasesTotal={run.pending.phases_total} busy={busy} onDecide={decide} />
      )}
      {!run.running && run.pending?.type === 'phase_gate' && (
        <PhaseGate pending={run.pending} busy={busy} onDecide={decide} />
      )}
      {!run.running && run.pending?.type === 'final_review' && (
        <FinalGate pending={run.pending} busy={busy} onDecide={decide} />
      )}

      {/* --- Terminal states ------------------------------------------------ */}
      {run.status === 'done' && (
        <div className="card success">
          <h2>Completed</h2>
          {Object.keys(run.pr_urls ?? {}).length > 0 ? (
            <>
              <p>Pull request{Object.keys(run.pr_urls).length > 1 ? 's' : ''}:</p>
              <ul className="small">
                {Object.entries(run.pr_urls).map(([repo, url]) => (
                  <li key={repo}>
                    {Object.keys(run.pr_urls).length > 1 && <span className="tag">{repo}</span>}{' '}
                    <a href={url} target="_blank" rel="noreferrer">
                      {url}
                    </a>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">Finished without creating a pull request. Changes remain in the local clone(s).</p>
          )}
          <DiffViewer diffs={run.diffs} />
        </div>
      )}
      {run.status === 'failed' && (
        <div className="card danger">
          <h2>Review failed after {run.iteration} attempt(s)</h2>
          <pre className="small">{run.review_feedback}</pre>
          <DiffViewer diffs={run.diffs} />
        </div>
      )}
      {run.status === 'cancelled' && (
        <div className="card">
          <h2>Cancelled</h2>
        </div>
      )}

      {/* --- Context (always visible once available) ----------------------- */}
      <div className="grid-2" style={{ marginTop: 8 }}>
        <div>
          {issue && (
            <div className="card">
              <h3>Jira issue</h3>
              <dl className="kv">
                <dt>Type / status</dt>
                <dd>
                  {issue.issue_type || '—'} / {issue.status || '—'}
                </dd>
                <dt>Reporter</dt>
                <dd>{issue.reporter ?? '—'}</dd>
                {issue.labels.length > 0 && (
                  <>
                    <dt>Labels</dt>
                    <dd>
                      {issue.labels.map((l) => (
                        <span className="tag" key={l}>
                          {l}
                        </span>
                      ))}
                    </dd>
                  </>
                )}
              </dl>
              <h3 style={{ marginTop: 12 }}>Description</h3>
              <pre className="small">{issue.description || '(empty)'}</pre>
              {issue.comments.length > 0 && (
                <>
                  <h3 style={{ marginTop: 12 }}>Comments ({issue.comments.length})</h3>
                  {issue.comments.map((c) => (
                    <div className="comment" key={c.id}>
                      <div className="who">
                        {c.author} · {c.created}
                      </div>
                      <pre className="small">{c.body}</pre>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
        <div>
          {run.requirements && run.pending?.type !== 'requirements_approval' && (
            <div className="card">
              <h3>Approved requirements</h3>
              <strong>{run.requirements.title}</strong>
              <p className="small">{run.requirements.problem}</p>
              <dl className="kv">
                <dt>Goals</dt>
                <dd>
                  <ul className="small">
                    {run.requirements.goals.map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </dd>
                <dt>Acceptance</dt>
                <dd>
                  <ul className="small">
                    {run.requirements.acceptance_criteria.map((g, i) => (
                      <li key={i}>{g}</li>
                    ))}
                  </ul>
                </dd>
                {run.requirements.out_of_scope.length > 0 && (
                  <>
                    <dt>Out of scope</dt>
                    <dd>
                      <ul className="small muted">
                        {run.requirements.out_of_scope.map((g, i) => (
                          <li key={i}>{g}</li>
                        ))}
                      </ul>
                    </dd>
                  </>
                )}
              </dl>
            </div>
          )}
          {run.plan_result && run.pending?.type !== 'plan_approval' && (
            <div className="card">
              <h3>Approved plan{run.execution_mode === 'phased' ? ' (phased)' : ''}</h3>
              <PlanDetails plan={run.plan_result} compact />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
