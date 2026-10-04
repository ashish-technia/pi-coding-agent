const LABELS: Record<string, { text: string; cls: string }> = {
  running: { text: 'Running', cls: 'running' },
  fetching: { text: 'Fetching issue', cls: 'running' },
  framing_requirements: { text: 'Framing requirements', cls: 'running' },
  pending_requirements: { text: 'Awaiting requirements approval', cls: 'warn' },
  planning: { text: 'Planning', cls: 'running' },
  pending_plan: { text: 'Awaiting plan approval', cls: 'warn' },
  coding: { text: 'Coding', cls: 'running' },
  reviewing: { text: 'Reviewing', cls: 'running' },
  pending_phase: { text: 'Phase done, awaiting decision', cls: 'warn' },
  pending_final: { text: 'Awaiting final review', cls: 'warn' },
  creating_pr: { text: 'Creating PR', cls: 'running' },
  done: { text: 'Completed', cls: 'success' },
  failed: { text: 'Failed review', cls: 'danger' },
  cancelled: { text: 'Cancelled', cls: '' },
  stuck_error: { text: 'Stuck (error)', cls: 'danger' },
  not_started: { text: 'Not started', cls: '' },
}

export default function StatusBadge({ status }: { status: string }) {
  const meta = LABELS[status] ?? { text: status, cls: '' }
  return (
    <span className={`badge ${meta.cls}`}>
      <span className="dot" />
      {meta.text}
    </span>
  )
}
