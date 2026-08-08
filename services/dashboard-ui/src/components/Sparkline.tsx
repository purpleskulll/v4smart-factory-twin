// Leichtgewichtige SVG-Sparkline (60 Punkte) — bewusst ohne Chart-Bibliothek
// (CLAUDE.md §16, View 2).

interface Props {
  values: number[]
  color: string
  min?: number
  max?: number
  height?: number
}

export function Sparkline({ values, color, min, max, height = 28 }: Props) {
  const width = 120
  if (values.length < 2) {
    return <svg width={width} height={height} role="img" aria-label="keine Daten" />
  }

  const lo = min ?? Math.min(...values)
  const hi = max ?? Math.max(...values)
  const span = hi - lo || 1
  const step = width / (values.length - 1)

  const points = values
    .map((v, i) => {
      const x = i * step
      const y = height - ((v - lo) / span) * (height - 2) - 1
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
