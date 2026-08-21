import React from 'react'

/* FPL-style availability flag: amber for doubtful (with the % chance FPL
   publishes), red for injured / suspended / unavailable. Hover for the news. */
const WHY = {
  d: 'Doubtful', i: 'Injured', s: 'Suspended', u: 'Unavailable',
  n: 'Not available', o: 'Out',
}

export function flagInfo(p) {
  if (!p || !p.status || p.status === 'a') return null
  const red = p.status !== 'd'
  const chance = p.chance
  const label = p.status === 'd' && chance != null ? `${chance}%` : '!'
  const title = `${WHY[p.status] || 'Flagged'}` +
    (chance != null ? ` — ${chance}% chance of playing` : '') +
    (p.news ? `: ${p.news}` : '')
  return { red, label, title }
}

export default function Flag({ p, style }) {
  const f = flagInfo(p)
  if (!f) return null
  return (
    <span className={`aflag ${f.red ? 'red' : 'amber'}`} title={f.title} style={style}>
      {f.label}
    </span>
  )
}
