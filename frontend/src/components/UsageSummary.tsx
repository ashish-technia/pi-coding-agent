import type { RunUsage } from '../api'

const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : `${n}`)
const usd = (n: number) => `$${n.toFixed(n < 1 ? 3 : 2)}`

/** What the run has spent on model calls so far, per stage. Hidden until something was recorded. */
export default function UsageSummary({ usage }: { usage?: RunUsage | null }) {
  if (!usage || usage.calls === 0) return null
  const overall = usage.budget_usd ? `${usd(usage.cost_usd)} of ${usd(usage.budget_usd)}` : usd(usage.cost_usd)
  return (
    <details className="card" style={{ marginBottom: 12 }}>
      <summary className="small">
        Model cost so far: <strong>{overall}</strong> · {tokens(usage.input)} in / {tokens(usage.output)} out
        {usage.unpriced_calls > 0 && (
          <span className="muted"> · {usage.unpriced_calls} call(s) not priced, so this is a lower bound</span>
        )}
      </summary>
      <table className="small" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th align="left">Stage</th>
            <th align="right">Calls</th>
            <th align="right">Input</th>
            <th align="right">Cached</th>
            <th align="right">Output</th>
            <th align="right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {usage.by_stage.map((s) => (
            <tr key={s.stage}>
              <td>{s.stage}</td>
              <td align="right">{s.calls}</td>
              <td align="right">{tokens(s.input)}</td>
              <td align="right">{tokens(s.cache_read)}</td>
              <td align="right">{tokens(s.output)}</td>
              <td align="right">{s.unpriced === s.calls ? 'not priced' : usd(s.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}
