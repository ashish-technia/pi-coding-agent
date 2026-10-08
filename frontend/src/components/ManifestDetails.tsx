import type { RunManifest } from '../api'

const short = (value?: string) => (value && value !== 'unknown' ? value.slice(0, 10) : 'unknown')

/** What produced this run: the versions, models and prompt hashes recorded when it started. */
export default function ManifestDetails({ manifest }: { manifest?: RunManifest | null }) {
  if (!manifest || !manifest.stages) return null
  return (
    <details className="card" style={{ marginBottom: 12 }}>
      <summary className="small">
        Run manifest: agent <code>{short(manifest.agent_git_sha)}</code> · Pi SDK {manifest.pi_sdk_version} · runner{' '}
        <code>{manifest.runner_sha}</code>
      </summary>
      <table className="small" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th align="left">Stage</th>
            <th align="left">Model</th>
            <th align="left">Thinking</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(manifest.stages).map(([stage, s]) => (
            <tr key={stage}>
              <td>
                {stage}
                {s.enabled === false && <span className="muted"> (off)</span>}
              </td>
              <td>
                {s.provider}/{s.model}
              </td>
              <td>{s.thinking_level ?? <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small muted" style={{ marginBottom: 0 }}>
        Prompts:{' '}
        {Object.entries(manifest.prompts ?? {})
          .map(([name, p]) => `${name}${p.version ? ` v${p.version}` : ''} (${p.sha})`)
          .join(' · ')}
        <br />
        Rules: review {manifest.rules?.review} · PR review {manifest.rules?.pr_review} · pack {manifest.pack} · agent
        version {manifest.agent_version} · taken {manifest.created_at}
      </p>
    </details>
  )
}
