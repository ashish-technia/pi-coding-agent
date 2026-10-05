export default function PartialReviewNote({ files }: { files?: string[] }) {
  if (!files || files.length === 0) return null
  return (
    <p className="small">
      <strong>The review saw a partial diff.</strong> Not reviewed because the diff is over the size limit:{' '}
      {files.map((f) => (
        <span className="tag" key={f}>
          {f}
        </span>
      ))}
    </p>
  )
}
