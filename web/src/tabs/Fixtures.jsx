import React, { useMemo, useState } from 'react'
import { useStore } from '../store'
import { badgeUrl, fdrColor } from '../util'

const MODES = [
  ['diff', 'Overall'], ['diff_att', 'Attacking'], ['diff_def', 'Defensive'],
]

// Difficulty of one gw cell for sorting/averages: multi-fixture gws average,
// blanks count as a hard 4.5 (no fixture = no points).
const cellDiff = (fs, mode) => {
  if (!fs.length) return 4.5
  return fs.reduce((a, f) => a + (f[mode] ?? f.fdr ?? 3), 0) / fs.length
}

export default function Fixtures() {
  const { fixtures, teams, status } = useStore()
  const [span, setSpan] = useState(10)
  const [mode, setMode] = useState('diff')
  // sort: key = 'team' | 'avg' | <gw number>; dir 1 = ascending (easiest first)
  const [sort, setSort] = useState({ key: 'avg', dir: 1 })

  const from = status?.next_gw || 1
  const gws = useMemo(() => {
    let scheduled = status?.scheduled_gws || []
    if (!scheduled.length && fixtures?.grid) {
      // DB not pulled yet — derive the calendar from the live fixture grid
      const s = new Set()
      for (const byGw of Object.values(fixtures.grid)) {
        for (const g of Object.keys(byGw)) s.add(Number(g))
      }
      scheduled = [...s].sort((a, b) => a - b)
    }
    return scheduled.filter((g) => g >= from).slice(0, span)
  }, [status, fixtures, from, span])

  const rows = useMemo(() => {
    if (!fixtures?.grid) return []
    const list = Object.entries(fixtures.grid).map(([tid, byGw]) => {
      const diffs = {}
      let sum = 0
      for (const g of gws) {
        diffs[g] = cellDiff(byGw[String(g)] || [], mode)
        sum += diffs[g]
      }
      return { tid: Number(tid), team: teams[tid], byGw, diffs,
               avg: gws.length ? sum / gws.length : 3 }
    })
    const { key, dir } = sort
    list.sort((a, b) => {
      if (key === 'team') {
        return (a.team?.name || '').localeCompare(b.team?.name || '') * dir
      }
      const va = key === 'avg' ? a.avg : (a.diffs[key] ?? 4.5)
      const vb = key === 'avg' ? b.avg : (b.diffs[key] ?? 4.5)
      return (va - vb) * dir || (a.team?.name || '').localeCompare(b.team?.name || '')
    })
    return list
  }, [fixtures, teams, gws, mode, sort])

  const clickSort = (key) => setSort((s) =>
    s.key === key ? { key, dir: -s.dir } : { key, dir: 1 })
  const arrow = (key) => (sort.key === key ? (sort.dir > 0 ? ' ▴' : ' ▾') : '')
  const th = (key, label, extra = {}) => (
    <th key={key} className={`num sortable ${sort.key === key ? 'sorted' : ''}`}
      onClick={() => clickSort(key)} style={extra}
      title={key === 'team' ? 'sort A–Z / Z–A'
        : 'click to sort — ▴ easiest first, ▾ hardest first'}>
      {label}{arrow(key)}
    </th>
  )

  return (
    <div className="panel">
      <div className="filterbar">
        <span className="section-label">Fixture difficulty</span>
        <div style={{ display: 'flex', gap: 4 }}>
          {MODES.map(([v, l]) => (
            <button key={v} className={`pill-btn ${mode === v ? 'accent' : ''}`}
              onClick={() => setMode(v)}
              title={v === 'diff_att' ? 'how hard the opponent is to score against'
                : v === 'diff_def' ? 'how hard it is to keep a clean sheet against the opponent'
                : 'opponent overall strength'}>
              {l}
            </button>
          ))}
        </div>
        <span style={{ fontSize: 11.5, color: 'var(--muted-2)' }}>
          1 = easiest · 5 = hardest · click any column to sort
        </span>
        <span style={{ flex: 1 }} />
        <label style={{ fontSize: 12, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 8 }}>
          Next
          <select className="pill-btn" value={span} style={{ background: 'var(--panel)', appearance: 'auto' }}
            onChange={(e) => setSpan(Number(e.target.value))}>
            {[5, 8, 10, 15, 20, 38].map((n) => <option key={n} value={n}>{n} GWs</option>)}
          </select>
        </label>
        <button className={`pill-btn ${sort.key === 'avg' ? 'accent' : ''}`}
          onClick={() => clickSort('avg')}>
          {sort.key === 'avg' && sort.dir < 0 ? 'Hardest first' : 'Easiest first'}
          {sort.key === 'avg' ? (sort.dir > 0 ? ' ▴' : ' ▾') : ''}
        </button>
      </div>
      <div className="fdr-table-wrap" style={{ padding: '0 10px 12px' }}>
        <table className="fdr">
          <thead>
            <tr>
              {th('team', 'Team', { textAlign: 'left', paddingLeft: 10 })}
              {gws.map((g) => th(g, `GW${g}`))}
              {th('avg', 'AVG')}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.tid}>
                <td className="team">
                  <img src={badgeUrl(r.team?.code)} alt=""
                    onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                  {r.team?.name || r.tid}
                </td>
                {gws.map((g) => {
                  const fs = r.byGw[String(g)] || []
                  if (!fs.length) {
                    return <td key={g}><div className="fdr-cell fdr-blank">–</div></td>
                  }
                  return (
                    <td key={g} className={sort.key === g ? 'sortcol' : ''}>
                      {fs.map((f, i) => {
                        const opp = teams[String(f.opp)]?.short || '?'
                        const v = f[mode] ?? f.fdr ?? 3
                        const c = fdrColor(v)
                        return (
                          <div key={i} className="fdr-cell"
                            style={{ background: c.bg, color: c.fg, ...(i ? { marginTop: 3 } : {}) }}
                            title={`${f.home ? 'Home vs' : 'Away at'} ${teams[String(f.opp)]?.name} · FPL FDR ${f.fdr ?? '–'}`}>
                            {f.home ? opp : <span className="away">{opp.toLowerCase()}</span>}
                            <span className="val">{Number(v).toFixed(1)}</span>
                          </div>
                        )
                      })}
                    </td>
                  )
                })}
                <td>
                  {(() => { const c = fdrColor(r.avg); return (
                    <div className="fdr-cell" style={{ background: c.bg, color: c.fg }}>
                      {r.avg.toFixed(2)}
                    </div>
                  ) })()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
