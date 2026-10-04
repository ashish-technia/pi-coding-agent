import { useEffect, useState } from 'react'
import { api, type AppConfig } from '../api'

export default function SettingsPage() {
  const [cfg, setCfg] = useState<AppConfig | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.config().then(setCfg).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="error-box">{error}</div>
  if (!cfg) return <p className="muted">Loading…</p>

  return (
    <div className="grid-2">
      <div>
        <div className="card">
          <h2>Models per stage</h2>
          <p className="muted small">
            Set in <code>.env</code>: <code>REQUIREMENTS_*</code>, <code>PLANNING_*</code>, <code>CODING_*</code>,{' '}
            <code>REVIEW_*</code>. Planning and coding run inside the Pi harness; requirements and review are
            direct chat calls.
          </p>
          <dl className="kv">
            {(Object.keys(cfg.stages) as Array<keyof AppConfig['stages']>).map((s) => (
              <div key={s} style={{ display: 'contents' }}>
                <dt>{s}</dt>
                <dd>
                  <span className="tag">{cfg.stages[s].provider}</span>
                  <span className="tag">{cfg.stages[s].model}</span>
                </dd>
              </div>
            ))}
            <dt>Pi thinking level</dt>
            <dd>{cfg.pi.thinking_level}</dd>
            <dt>Pi timeout</dt>
            <dd>{cfg.pi.timeout_seconds} s</dd>
            <dt>Agent context dir</dt>
            <dd className="mono">{cfg.pi.agent_dir}</dd>
          </dl>
        </div>
        <div className="card">
          <h2>Repository and delivery</h2>
          <dl className="kv">
            <dt>Repositories</dt>
            <dd>
              {cfg.repos.map((r) => (
                <div key={r.name} className="small" style={{ marginBottom: 4 }}>
                  <span className="tag">{r.name}</span>
                  {r.default_selected && <span className="muted"> default</span>}
                  <div className="mono muted">
                    {r.path || 'no local path'} → {r.bitbucket_repo_slug || 'no slug'}@{r.target_branch}
                  </div>
                  {Object.entries(r.properties).map(([k, v]) => (
                    <div key={k} className="muted">
                      {k}: {v}
                    </div>
                  ))}
                </div>
              ))}
            </dd>
            <dt>Repo config</dt>
            <dd className="mono">{cfg.repo.config_path}</dd>
            <dt>Default target branch</dt>
            <dd>{cfg.repo.target_branch}</dd>
            <dt>Branch prep</dt>
            <dd>{cfg.repo.prepare_branch_before_pr ? 'on' : 'off'}</dd>
            <dt>PR creation</dt>
            <dd>{cfg.delivery.pr_creation_enabled ? 'enabled' : 'disabled (review only)'}</dd>
            <dt>Bitbucket</dt>
            <dd className="mono">
              {cfg.delivery.bitbucket_workspace}/{cfg.delivery.bitbucket_repo_slug}
            </dd>
            <dt>Jira</dt>
            <dd className="mono">{cfg.delivery.jira_base_url}</dd>
            <dt>Jira comments</dt>
            <dd>{cfg.delivery.jira_comments_enabled ? 'on' : 'off'}</dd>
            <dt>Jira comment channel</dt>
            <dd>
              {cfg.delivery.jira_comment_channel_enabled ? 'on' : 'off'} (trigger label{' '}
              <span className="tag">{cfg.delivery.jira_trigger_label}</span>)
            </dd>
            <dt>Persistence</dt>
            <dd>
              checkpoints: {cfg.persistence.checkpointer}, queue: {cfg.persistence.queue}
            </dd>
          </dl>
        </div>
      </div>
      <div className="card">
        <h2>Review rules</h2>
        <p className="muted small">
          Read from <code>{cfg.review.rules_path}</code> on every review. Max coding/review iterations per phase:{' '}
          {cfg.review.max_iterations}.
        </p>
        {cfg.review.rules ? (
          <pre className="small">{cfg.review.rules}</pre>
        ) : (
          <p className="muted">No rules file found. Create it to give the reviewer must-check items.</p>
        )}
      </div>
    </div>
  )
}
