import { useEffect, useRef, useState } from 'react'

interface Props {
  label: string
  items: string[]
  onChange: (items: string[]) => void
  flagged?: Set<string>
  placeholder?: string
}

/** Editable list of strings; flagged items get a warning border; new rows take focus. */
export default function ListEditor({ label, items, onChange, flagged, placeholder }: Props) {
  const [focusIndex, setFocusIndex] = useState<number | null>(null)
  const inputs = useRef<Array<HTMLInputElement | null>>([])

  useEffect(() => {
    if (focusIndex !== null) {
      inputs.current[focusIndex]?.focus()
      setFocusIndex(null)
    }
  }, [focusIndex, items.length])

  const update = (i: number, value: string) => onChange(items.map((v, j) => (j === i ? value : v)))
  const remove = (i: number) => onChange(items.filter((_, j) => j !== i))
  const add = () => {
    onChange([...items, ''])
    setFocusIndex(items.length)
  }
  return (
    <div className="field list-editor">
      <label>{label}</label>
      {items.map((item, i) => (
        <div className={`row${flagged?.has(item) ? ' flagged' : ''}`} key={i}>
          <input
            ref={(el) => {
              inputs.current[i] = el
            }}
            value={item}
            onChange={(e) => update(i, e.target.value)}
            placeholder={placeholder}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                add()
              }
            }}
          />
          <button type="button" className="small" onClick={() => remove(i)} title="Remove">
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="small" onClick={add}>
        + Add
      </button>
    </div>
  )
}
