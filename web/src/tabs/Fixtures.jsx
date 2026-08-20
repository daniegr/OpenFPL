import React, { useMemo, useState } from 'react'
import { useStore } from '../store'
import { badgeUrl } from '../util'

export default function Fixtures() {
  const { fixtures, teams, status } = useStore()
  const [span, setSpan] = useState(10)
  const [sortByEase, setSortByEase] = useState(true)

  const from = status?.next_gw || 1
  const gws = useMemo(() =>
    (status?.scheduled_gws || []).filter((g) => g >= from).slice(0, span),
    [status, from, span])

  const rows = useMemo(() => {
    if (!fixtures?.grid) return []
    const list = Object.entries(fixtures.grid).map(([tid, byGw]) => {
      let sum = 0, n = 0
      for (const g of gws) {
        for (const f of byGw[String(g)] || []) { sum += f.fdr || 3; n++ }
        if (!(byGw[String(g)] || []).length) { sum += 3.5; n++ }   // blank ≈ hard
      }
      return { tid: Number(tid), team: teams[tid], byGw, avg: n ? sum / n : 3 }
    })
    list.sort(sortByEase
      ? (a, b) => a.avg - b.avg
      : (a, b) => (a.team?.name || '').localeCompare(b.team?.name || ''))
    return list
  }, [fixtures, teams, gws, sortByEase])

  return (
    <div className="panel">
      <div className="filterbar">
        <span className="section-label">Fixture difficulty (FPL FDR)</span>
        <span style={{ flex: 1 }} />
        <label style={{ fontSize: 12, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 8 }}>
          Next
          <select className="pill-btn" value={span} style={{ background: 'var(--panel)', appearance: 'auto' }}
            onChange={(e) => setSpan(Number(e.target.value))}>
            {[5, 8, 10, 15, 20, 38].map((n) => <option key={n} value={n}>{n} GWs</option>)}
          </select>
        </label>
        <button className={`pill-btn ${sortByEase ? 'accent' : ''}`}
          onClick={() => setSortByEase((s) => !s)}>
          {sortByEase ? 'Sorted by ease' : 'Sort A–Z'}
        </button>
      </div>
      <div className="fdr-table-wrap" style={{ padding: '0 10px 12px' }}>
        <table className="fdr">
          <thead>
            <tr>
              <th style={{ textAlign: 'left', paddingLeft: 10 }}>Team</th>
              {gws.map((g) => <th key={g} className="num">GW{g}</th>)}
              <th className="num" title="average difficulty over the window">AVG</th>
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
                    <td key={g}>
                      {fs.map((f, i) => {
                        const opp = teams[String(f.opp)]?.short || '?'
                        return (
                          <div key={i} className={`fdr-cell fdr-${f.fdr || 3}`}
                            style={i ? { marginTop: 3 } : undefined}
                            title={`${f.home ? 'Home vs' : 'Away at'} ${teams[String(f.opp)]?.name}`}>
                            {f.home ? opp : <span className="away">{opp.toLowerCase()}</span>}
                          </div>
                        )
                      })}
                    </td>
                  )
                })}
                <td><div className="fdr-cell" style={{
                  background: 'var(--panel-2)',
                  color: r.avg <= 2.6 ? 'var(--green)' : r.avg >= 3.4 ? 'var(--red)' : 'var(--muted)',
                }}>{r.avg.toFixed(2)}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
