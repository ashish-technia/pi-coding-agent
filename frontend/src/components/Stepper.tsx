import type { RunStatus, Stage } from '../api'

const STAGES: Array<{ id: Stage; label: string }> = [
  { id: 'requirements', label: 'Requirements' },
  { id: 'plan', label: 'Plan' },
  { id: 'code', label: 'Code' },
  { id: 'review', label: 'Review' },
  { id: 'final', label: 'Final review' },
  { id: 'pr', label: 'PR' },
]

function activeIndex(run: RunStatus): { index: number; error: boolean; allDone: boolean } {
  if (run.status === 'done') return { index: STAGES.length, error: false, allDone: true }
  if (run.stage) {
    const idx = STAGES.findIndex((s) => s.id === run.stage)
    return { index: idx, error: run.status === 'stuck_error' || run.status === 'failed', allDone: false }
  }
  return { index: 0, error: false, allDone: false }
}

export default function Stepper({ run }: { run: RunStatus }) {
  const { index, error, allDone } = activeIndex(run)
  return (
    <div className="stepper">
      {STAGES.map((s, i) => {
        let cls = 'step'
        if (allDone || i < index) cls += ' done'
        else if (i === index) cls += error ? ' error' : ' active'
        return (
          <div className={cls} key={s.id}>
            <div className="dot">{i + 1}</div>
            <div className="label">{s.label}</div>
          </div>
        )
      })}
    </div>
  )
}
