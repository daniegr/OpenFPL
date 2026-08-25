// Shared SVG chart components for the dashboard (dark surface #2d2d44).
// Palette: validated categorical slots (adjacent-pass on this surface; first
// three slots all-pairs-pass for radar/donut). Fixed order, never cycled.
import React, { useMemo, useRef, useState } from 'react'

export const VIZ = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181',
                    '#008300', '#9085e9', '#e66767']
export const VIZ_NEUTRAL = '#4b4b68'          // "Other" / de-emphasis
const SURFACE = '#2d2d44'
const GRID = '#3b3b58'
const INK_MUTED = '#9a9ab8'
// sequential blue ramp (dark mode: low recedes to surface, high brightens)
const SEQ = ['#16294a', '#104281', '#184f95', '#1c5cab', '#256abf',
             '#2a78d6', '#3987e5', '#5598e7', '#6da7ec', '#86b6ef']

export const seqColor = (t) =>
  SEQ[Math.max(0, Math.min(SEQ.length - 1, Math.floor(t * (SEQ.length - 1) + 0.5)))]
export const seqInk = (t) => (t > 0.55 ? '#0f2038' : '#d7d7ea')

/* ---------- tooltip plumbing (one absolutely-positioned div per chart) ---- */

export function useTip() {
  const [tip, setTip] = useState(null)        // {x, y, node}
  const ref = useRef(null)
  const show = (e, node) => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, node })
  }
  const hide = () => setTip(null)
  const el = tip && (
    <div className="viz-tip" style={{
      left: Math.min(tip.x + 14, (ref.current?.clientWidth || 300) - 170),
      top: tip.y + 14,
    }}>{tip.node}</div>
  )
  return { ref, show, hide, el }
}

export function Legend({ items }) {
  return (
    <div className="viz-legend">
      {items.map((it) => (
        <span key={it.label}>
          <i style={{ background: it.color }} />{it.label}
        </span>
      ))}
    </div>
  )
}

/* ------------------------------- radar ----------------------------------- */
// axes: [{key,label,fmt}] · series: [{name,color,values:[0..100],raw:[...]}]
export function Radar({ axes, series, size = 300 }) {
  const { ref, show, hide, el } = useTip()
  const cx = size / 2, cy = size / 2, R = size / 2 - 42
  const n = axes.length
  const angle = (i) => (Math.PI * 2 * i) / n - Math.PI / 2
  const pt = (i, v) => [cx + Math.cos(angle(i)) * R * (v / 100),
                        cy + Math.sin(angle(i)) * R * (v / 100)]
  const ring = (f) => axes.map((_, i) => pt(i, f * 100).join(',')).join(' ')

  return (
    <div className="viz-wrap" ref={ref}>
      <svg viewBox={`0 0 ${size} ${size}`} style={{ width: '100%', maxWidth: 430, display: 'block', margin: '0 auto' }}>
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <polygon key={f} points={ring(f)} fill="none" stroke={GRID}
            strokeWidth={f === 1 ? 1.2 : 1} opacity={f === 1 ? 1 : 0.65} />
        ))}
        {axes.map((a, i) => {
          const [x, y] = pt(i, 100)
          const [lx, ly] = pt(i, 122)
          return (
            <g key={a.key}>
              <line x1={cx} y1={cy} x2={x} y2={y} stroke={GRID} strokeWidth="1" opacity="0.65" />
              <text x={lx} y={ly} textAnchor="middle" dominantBaseline="middle"
                fontSize="10" fontWeight="700" fill={INK_MUTED}
                onMouseMove={(e) => show(e, (
                  <>
                    <b>{a.label}</b>
                    {series.map((s) => (
                      <div key={s.name} className="viz-tip-row">
                        <i style={{ background: s.color }} />{s.name}: <b>{a.fmt ? a.fmt(s.raw?.[i]) : Math.round(s.values[i])}</b>
                      </div>
                    ))}
                  </>
                ))}
                onMouseLeave={hide}>
                {a.label}
              </text>
            </g>
          )
        })}
        {series.map((s) => (
          <g key={s.name}>
            <polygon points={s.values.map((v, i) => pt(i, v).join(',')).join(' ')}
              fill={s.color} fillOpacity="0.10" stroke={s.color} strokeWidth="2"
              strokeLinejoin="round" />
            {s.values.map((v, i) => {
              const [x, y] = pt(i, v)
              return (
                <circle key={i} cx={x} cy={y} r="4" fill={s.color}
                  stroke={SURFACE} strokeWidth="2"
                  onMouseMove={(e) => show(e, (
                    <><b>{s.name}</b>
                      <div className="viz-tip-row"><i style={{ background: s.color }} />
                        {axes[i].label}: <b>{axes[i].fmt ? axes[i].fmt(s.raw?.[i]) : Math.round(v)}</b></div></>
                  ))}
                  onMouseLeave={hide} />
              )
            })}
          </g>
        ))}
      </svg>
      <Legend items={series.map((s) => ({ label: s.name, color: s.color }))} />
      {el}
    </div>
  )
}

/* ----------------------------- H bars (EO) -------------------------------- */
// rows: [{label, sub, cap (0-100), own (0-100), mine, tip}] — stacked:
// captained share (slot 2) inside owned share (slot 1), 2px surface gap.
export function EOBars({ rows, max = 100 }) {
  const { ref, show, hide, el } = useTip()
  return (
    <div className="viz-wrap" ref={ref}>
      <Legend items={[{ label: 'owned', color: VIZ[0] }, { label: 'captained', color: VIZ[1] }]} />
      <div>
        {rows.map((r) => {
          const w = Math.max(1.5, (r.own / max) * 100)
          const wc = (r.cap / max) * 100
          return (
            <div key={r.label} className="eo-row"
              onMouseMove={(e) => show(e, r.tip)} onMouseLeave={hide}>
              <span className="eo-name">
                {r.mine && <i className="eo-mine" title="in your squad" />}
                {r.label} <em>{r.sub}</em>
              </span>
              <span className="eo-track">
                <span className="eo-bar" style={{ width: `${w}%`, background: VIZ[0] }} />
                {wc > 0.5 && (
                  <span className="eo-bar cap" style={{ width: `${Math.max(0, wc)}%`, background: VIZ[1] }} />
                )}
              </span>
              <span className="eo-val num">{r.own.toFixed(0)}%</span>
            </div>
          )
        })}
      </div>
      {el}
    </div>
  )
}

/* ------------------------------- donut ------------------------------------ */
// slices: [{label, value, color}] — pass ≤3 colored + neutral "Other".
export function Donut({ slices, size = 170, centre }) {
  const { ref, show, hide, el } = useTip()
  const total = slices.reduce((a, s) => a + s.value, 0) || 1
  const R = size / 2 - 6, r = R - 22, cx = size / 2, cy = size / 2
  let a0 = -Math.PI / 2
  const arcs = slices.map((s) => {
    const a1 = a0 + (s.value / total) * Math.PI * 2
    const arc = { ...s, a0, a1 }
    a0 = a1
    return arc
  })
  const xy = (a, rad) => [cx + Math.cos(a) * rad, cy + Math.sin(a) * rad]
  return (
    <div className="viz-wrap viz-donut" ref={ref}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
        {arcs.map((s, i) => {
          const [x0, y0] = xy(s.a0, R), [x1, y1] = xy(s.a1, R)
          const [x2, y2] = xy(s.a1, r), [x3, y3] = xy(s.a0, r)
          const big = s.a1 - s.a0 > Math.PI ? 1 : 0
          return (
            <path key={i}
              d={`M${x0},${y0} A${R},${R} 0 ${big} 1 ${x1},${y1} L${x2},${y2} A${r},${r} 0 ${big} 0 ${x3},${y3} Z`}
              fill={s.color} stroke={SURFACE} strokeWidth="2"
              onMouseMove={(e) => show(e, (
                <><b>{s.label}</b>
                  <div className="viz-tip-row"><i style={{ background: s.color }} />
                    {s.value} of {total} ({((s.value / total) * 100).toFixed(0)}%)</div></>
              ))}
              onMouseLeave={hide} />
          )
        })}
        {centre && (
          <>
            <text x={cx} y={cy - 6} textAnchor="middle" fontSize="17" fontWeight="800"
              fill="#eeeefa" fontFamily="var(--mono)">{centre[0]}</text>
            <text x={cx} y={cy + 12} textAnchor="middle" fontSize="9.5" fontWeight="700"
              fill={INK_MUTED} style={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              {centre[1]}
            </text>
          </>
        )}
      </svg>
      <div className="viz-legend col">
        {slices.map((s) => (
          <span key={s.label}><i style={{ background: s.color }} />
            {s.label} <b className="num" style={{ marginLeft: 4 }}>
              {((s.value / total) * 100).toFixed(0)}%</b>
          </span>
        ))}
      </div>
      {el}
    </div>
  )
}

/* ----------------------------- line chart --------------------------------- */
// series: [{name, color, points: [{x, y}]}] — x = gw number.
export function LineChart({ series, height = 240, yLabel, fmt = (v) => v }) {
  const { ref, show, hide, el } = useTip()
  const [hoverX, setHoverX] = useState(null)
  const W = 760, H = height, padL = 44, padR = 110, padT = 14, padB = 26
  const xs = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort((a, b) => a - b)
  const ys = series.flatMap((s) => s.points.map((p) => p.y))
  const yMax = Math.max(1, ...ys)
  const yMin = Math.min(0, ...ys)
  const nice = Math.ceil(yMax / 4)
  const xPos = (x) => xs.length < 2
    ? padL + (W - padL - padR) / 2
    : padL + ((x - xs[0]) / (xs[xs.length - 1] - xs[0])) * (W - padL - padR)
  const yPos = (y) => padT + (1 - (y - yMin) / (yMax - yMin || 1)) * (H - padT - padB)

  const hover = (e) => {
    const r = ref.current.getBoundingClientRect()
    const fx = ((e.clientX - r.left) / r.width) * W
    let best = null
    for (const x of xs) if (best == null || Math.abs(xPos(x) - fx) < Math.abs(xPos(best) - fx)) best = x
    setHoverX(best)
    if (best != null) {
      const at = series
        .map((s) => ({ s, p: s.points.find((p) => p.x === best) }))
        .filter((d) => d.p).sort((a, b) => b.p.y - a.p.y)
      show(e, (
        <><b>GW{best}</b>
          {at.map(({ s, p }) => (
            <div key={s.name} className="viz-tip-row">
              <i style={{ background: s.color }} />{s.name}: <b>{fmt(p.y)}</b>
            </div>
          ))}</>
      ))
    }
  }

  // direct-label the top 3 + any series flagged `me` at line end
  const labelled = useMemo(() => {
    const ends = series.map((s) => ({ s, end: s.points[s.points.length - 1] }))
      .filter((d) => d.end)
    ends.sort((a, b) => b.end.y - a.end.y)
    const keep = new Set(ends.slice(0, 3).map((d) => d.s.name))
    for (const d of ends) if (d.s.me) keep.add(d.s.name)
    return keep
  }, [series])

  return (
    <div className="viz-wrap" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', display: 'block' }}
        onMouseMove={hover} onMouseLeave={() => { setHoverX(null); hide() }}>
        {[0, 1, 2, 3, 4].map((i) => {
          const v = yMin + i * nice
          if (v > yMax + nice / 2) return null
          return (
            <g key={i}>
              <line x1={padL} x2={W - padR} y1={yPos(v)} y2={yPos(v)} stroke={GRID} strokeWidth="1" />
              <text x={padL - 8} y={yPos(v) + 3} textAnchor="end" fontSize="10"
                fill={INK_MUTED} fontFamily="var(--mono)">{fmt(v)}</text>
            </g>
          )
        })}
        {xs.map((x) => (
          <text key={x} x={xPos(x)} y={H - 8} textAnchor="middle" fontSize="10"
            fill={INK_MUTED} fontFamily="var(--mono)">GW{x}</text>
        ))}
        {hoverX != null && (
          <line x1={xPos(hoverX)} x2={xPos(hoverX)} y1={padT} y2={H - padB}
            stroke={INK_MUTED} strokeWidth="1" opacity="0.5" />
        )}
        {series.map((s) => (
          <g key={s.name}>
            <polyline points={s.points.map((p) => `${xPos(p.x)},${yPos(p.y)}`).join(' ')}
              fill="none" stroke={s.color} strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" opacity={s.dim ? 0.45 : 1} />
            {s.points.map((p) => (
              <circle key={p.x} cx={xPos(p.x)} cy={yPos(p.y)}
                r={hoverX === p.x ? 4.5 : 3.5} fill={s.color}
                stroke={SURFACE} strokeWidth="2" />
            ))}
            {labelled.has(s.name) && s.points.length > 0 && (
              <text x={xPos(s.points[s.points.length - 1].x) + 10}
                y={yPos(s.points[s.points.length - 1].y) + 3.5}
                fontSize="10.5" fontWeight="700" fill="#c9c9de">
                {s.name.length > 14 ? `${s.name.slice(0, 13)}…` : s.name}
              </text>
            )}
          </g>
        ))}
        {yLabel && (
          <text x={padL} y={padT - 3} fontSize="9.5" fill={INK_MUTED}
            style={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>{yLabel}</text>
        )}
      </svg>
      <Legend items={series.map((s) => ({ label: s.name, color: s.color }))} />
      {el}
    </div>
  )
}

/* ------------------------- overlap heatmap -------------------------------- */
// labels: [names] · get(i,j) -> shared-player count (0..15)
export function OverlapMatrix({ labels, get, max = 15 }) {
  const { ref, show, hide, el } = useTip()
  const n = labels.length
  const cell = 30, padL = 120, padT = 84
  const W = padL + n * cell + 8, H = padT + n * cell + 8
  return (
    <div className="viz-wrap" ref={ref} style={{ overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', minWidth: 420, display: 'block' }}>
        {labels.map((l, j) => (
          <text key={j} x={padL + j * cell + cell / 2} y={padT - 8}
            transform={`rotate(-42 ${padL + j * cell + cell / 2} ${padT - 8})`}
            fontSize="9.5" fontWeight="600" fill={INK_MUTED}>
            {l.length > 13 ? `${l.slice(0, 12)}…` : l}
          </text>
        ))}
        {labels.map((l, i) => (
          <text key={i} x={padL - 8} y={padT + i * cell + cell / 2 + 3.5}
            textAnchor="end" fontSize="9.5" fontWeight="600" fill={INK_MUTED}>
            {l.length > 15 ? `${l.slice(0, 14)}…` : l}
          </text>
        ))}
        {labels.map((_, i) => labels.map((_, j) => {
          if (i === j) {
            return <rect key={`${i}-${j}`} x={padL + j * cell} y={padT + i * cell}
              width={cell - 2} height={cell - 2} rx="3" fill={GRID} opacity="0.35" />
          }
          const v = get(i, j)
          const t = v / max
          return (
            <g key={`${i}-${j}`}
              onMouseMove={(e) => show(e, (
                <><b>{labels[i]} × {labels[j]}</b>
                  <div className="viz-tip-row">{v} shared players of {max}</div></>
              ))}
              onMouseLeave={hide}>
              <rect x={padL + j * cell} y={padT + i * cell}
                width={cell - 2} height={cell - 2} rx="3" fill={seqColor(t)} />
              <text x={padL + j * cell + (cell - 2) / 2} y={padT + i * cell + cell / 2 + 2.5}
                textAnchor="middle" fontSize="9.5" fontWeight="700"
                fill={seqInk(t)} fontFamily="var(--mono)">{v}</text>
            </g>
          )
        }))}
      </svg>
      {el}
    </div>
  )
}

/* ------------------------------ stat tile --------------------------------- */
export function StatTile({ label, value, delta, deltaGood, sub }) {
  return (
    <div className="stat-tile">
      <div className="st-label">{label}</div>
      <div className="st-value">{value}</div>
      {(delta != null || sub) && (
        <div className="st-sub">
          {delta != null && (
            <span style={{ color: deltaGood ? '#2fd680' : '#ff5063', fontWeight: 700 }}>
              {delta}
            </span>
          )}
          {sub && <span style={{ color: 'var(--muted-2)' }}> {sub}</span>}
        </div>
      )}
    </div>
  )
}
