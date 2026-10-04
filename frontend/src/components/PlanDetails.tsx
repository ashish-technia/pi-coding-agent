import type { AgentResult } from '../api'

export default function PlanDetails({ plan, compact = false }: { plan: AgentResult; compact?: boolean }) {
  return (
    <dl className="kv">
      {plan.notes_response && (
        <>
          <dt>Reply to your notes</dt>
          <dd>
            <pre>{plan.notes_response}</pre>
          </dd>
        </>
      )}
      <dt>Branch</dt>
      <dd className="mono">{plan.branch_name}</dd>
      {!compact && (
        <>
          <dt>Commit message</dt>
          <dd className="mono">{plan.commit_message}</dd>
        </>
      )}
      {plan.analysis && (
        <>
          <dt>Code analysis</dt>
          <dd>
            <pre>{plan.analysis}</pre>
          </dd>
        </>
      )}
      {plan.phases.length > 0 && (
        <>
          <dt>Phases</dt>
          <dd>
            {plan.phases.map((p, i) => (
              <div className="phase" key={i}>
                <strong>
                  {i + 1}. {p.name}
                </strong>{' '}
                <span className="muted small">steps {p.step_indexes.map((s) => s + 1).join(', ')}</span>
                {p.description && <div className="small">{p.description}</div>}
              </div>
            ))}
          </dd>
        </>
      )}
      <dt>Planned edits</dt>
      <dd>
        {plan.plan_steps.map((s, i) => (
          <div className="plan-step" key={i}>
            <div className="file">
              <span className="action">{s.action}</span>
              {i + 1}. {s.repo ? `${s.repo}/${s.file}` : s.file}
            </div>
            <div>{s.change}</div>
            {s.evidence && <div className="evidence">Evidence: {s.evidence}</div>}
          </div>
        ))}
      </dd>
      {plan.verification.length > 0 && (
        <>
          <dt>Verification</dt>
          <dd>
            <ul>
              {plan.verification.map((v, i) => (
                <li key={i}>{v}</li>
              ))}
            </ul>
          </dd>
        </>
      )}
      {plan.open_questions.length > 0 && (
        <>
          <dt>Open questions</dt>
          <dd>
            <ul>
              {plan.open_questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
          </dd>
        </>
      )}
      {!compact && (
        <>
          <dt>PR description draft</dt>
          <dd>
            <pre>{plan.pr_description}</pre>
          </dd>
        </>
      )}
    </dl>
  )
}
