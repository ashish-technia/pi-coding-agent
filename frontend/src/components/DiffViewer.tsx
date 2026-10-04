import { html } from 'diff2html'
import { useMemo, useState } from 'react'

/** One repo's diff. Kept separate so each gets its own diff2html render. */
function RepoDiff({ diff, sideBySide }: { diff: string; sideBySide: boolean }) {
  const rendered = useMemo(
    () =>
      html(diff, {
        drawFileList: true,
        matching: 'lines',
        outputFormat: sideBySide ? 'side-by-side' : 'line-by-line',
      }),
    [diff, sideBySide],
  )
  return <div className="diff-wrap" dangerouslySetInnerHTML={{ __html: rendered }} />
}

/**
 * Renders the working-tree diff of every repo a run touched. A multi-repo change is
 * several independent diffs, so each is labelled and rendered on its own.
 */
export default function DiffViewer({ diffs }: { diffs: Record<string, string> }) {
  const [sideBySide, setSideBySide] = useState(false)
  const entries = Object.entries(diffs ?? {}).filter(([, d]) => d.trim())

  if (entries.length === 0) return <p className="muted">No changes in the working tree.</p>

  return (
    <div>
      <div className="actions" style={{ marginTop: 0, marginBottom: 8 }}>
        <button type="button" className="small" onClick={() => setSideBySide((v) => !v)}>
          {sideBySide ? 'Unified view' : 'Side-by-side view'}
        </button>
      </div>
      {entries.map(([repo, diff]) => (
        <div key={repo} style={{ marginBottom: entries.length > 1 ? 18 : 0 }}>
          {entries.length > 1 && (
            <h4 style={{ margin: '0 0 6px' }}>
              <span className="tag">{repo}</span>
            </h4>
          )}
          <RepoDiff diff={diff} sideBySide={sideBySide} />
        </div>
      ))}
    </div>
  )
}
