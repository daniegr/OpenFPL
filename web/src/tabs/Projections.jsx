import React, { useMemo, useState } from 'react'
import { api, pollJob } from '../api'
import { useStore } from '../store'
import { badgeUrl, downloadCSV, epColor, fmt1 } from '../util'

const POS_FILTERS = [
  ['ALL', 'All players'], ['GK', 'Goalkeepers'], ['DEF', 'Defenders'],
  ['MID', 'Midfielders'], ['FWD', 'Forwards'],
]

export default function Projections() {
  const { proj, players, teams, byId, status, refreshProjections, setToast } = useStore()
  const [q, setQ] = useState('')
  const [pos, setPos] = useState('ALL')
  const [gwSel, setGwSel] = useState(null)      // null = all projected gws
  const [gwOpen, setGwOpen] = useState(false)
  const [sort, setSort] = useState({ key: 'total', dir: -1 })
  const [priceMax, setPriceMax] = useState(null)
  const [building, setBuilding] = useState(false)

  const projGws = (status?.projected_gws?.length
    ? status.projected_gws
    : Object.keys(proj?.gws || {}).map(Number)).sort((a, b) => a - b)
  const shown = (gwSel && gwSel.length ? gwSel : projGws)

  const rows = useMemo(() => {
    if (!proj?.players) return []
    const out = []
    for (const rec of Object.values(proj.players)) {
      const stat = byId.get(rec.player_id)
      if (pos !== 'ALL' && rec.position !== pos) continue
      if (q && !rec.player.toLowerCase().includes(q.toLowerCase()) &&
          !(stat?.web_name || '').toLowerCase().includes(q.toLowerCase())) continue
      if (priceMax && rec.price > priceMax) continue
      const eps = {}
      let total = 0
      for (const g of shown) {
        const v = rec.ep[String(g)]
        eps[g] = v
        total += v || 0
      }
      out.push({
        id: rec.player_id,
        name: stat?.web_name || rec.player,
        team_id: rec.team_id,
        teamShort: teams[String(rec.team_id)]?.short || rec.team,
        teamCode: teams[String(rec.team_id)]?.code,
        position: rec.position === 'GK' ? 'GKP' : rec.position,
        price: rec.price,
        own: stat?.own ?? 0,
        status: stat?.status,
        news: stat?.news,
        eps, total,
      })
    }
    const dir = sort.dir
    out.sort((a, b) => {
      const va = sort.key === 'total' ? a.total
        : sort.key === 'price' ? a.price
        : sort.key === 'own' ? a.own
        : sort.key === 'name' ? a.name
        : a.eps[sort.key] || 0
      const vb = sort.key === 'total' ? b.total
        : sort.key === 'price' ? b.price
        : sort.key === 'own' ? b.own
        : sort.key === 'name' ? b.name
        : b.eps[sort.key] || 0
      return (va < vb ? -1 : va > vb ? 1 : 0) * dir
    })
    return out.slice(0, 400)
  }, [proj, byId, teams, q, pos, priceMax, shown, sort])

  const clickSort = (key) => setSort((s) =>
    s.key === key ? { key, dir: -s.dir } : { key, dir: key === 'name' ? 1 : -1 })

  const build = async () => {
    if (building) return
    const from = status?.next_gw || 1
    const gws = (status?.scheduled_gws || []).filter((g) => g >= from).slice(0, 6)
    setBuilding(true)
    setToast({ kind: 'info', msg: `Building projections for GW${gws[0]}–${gws[gws.length - 1]}…` })
    try {
      const { job_id } = await api.buildProjections(gws)
      await pollJob(job_id, (j) => {
        const last = j.progress[j.progress.length - 1]
        if (last) setToast({ kind: 'info', msg: last.msg })
      })
      setToast({ kind: 'ok', msg: 'Projections built.' })
      refreshProjections()
      api.status().then(() => {})
    } catch (e) {
      setToast({ kind: 'err', msg: `Projection build failed: ${e.message}` })
    } finally { setBuilding(false) }
  }

  const exportCsv = () => {
    downloadCSV('openfpl_projections.csv',
      ['player', 'team', 'pos', 'price', 'own%', ...shown.map((g) => `gw${g}`), 'total'],
      rows.map((r) => [r.name, r.teamShort, r.position, r.price, r.own,
        ...shown.map((g) => r.eps[g] ?? ''), r.total.toFixed(2)]))
  }

  if (!projGws.length) {
    return (
      <div className="panel center-note">
        <h3>No projections yet</h3>
        <p style={{ marginBottom: 16 }}>
          Run the OpenFPL models to project the next six gameweeks (a few minutes,
          cached afterwards).
        </p>
        <button className="pill-btn accent" onClick={build} disabled={building}>
          {building ? <span className="spinner" /> : '▶'} Build projections
        </button>
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="filterbar">
        <div className="search">🔍
          <input placeholder="Search players…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <select className="pill-btn" value={pos} onChange={(e) => setPos(e.target.value)}
          style={{ background: 'var(--panel)', appearance: 'auto' }}>
          {POS_FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <div className="dd">
          <button className={`pill-btn ${gwSel?.length ? 'accent' : ''}`}
            onClick={() => setGwOpen((o) => !o)}>
            {gwSel?.length ? `${gwSel.length}/${projGws.length} GWs` : 'All GWs'} ▾
          </button>
          {gwOpen && (
            <div className="dd-menu">
              <div className="ttl">Select gameweeks
                <span>
                  <button className="chip pink" style={{ marginRight: 6 }}
                    onClick={() => setGwSel(null)}>reset</button>
                  <button style={{ color: 'var(--muted)' }} onClick={() => setGwOpen(false)}>✕</button>
                </span>
              </div>
              <div className="gw-grid">
                {projGws.map((g) => {
                  const on = !gwSel || gwSel.includes(g)
                  return (
                    <button key={g} className={`gw-tog ${on ? 'on' : ''}`}
                      onClick={() => {
                        const cur = gwSel ?? [...projGws]
                        const next = cur.includes(g) ? cur.filter((x) => x !== g) : [...cur, g].sort((a, b) => a - b)
                        setGwSel(next.length === projGws.length ? null : next)
                      }}>GW{g}</button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
        <select className="pill-btn" value={priceMax || ''} style={{ background: 'var(--panel)', appearance: 'auto' }}
          onChange={(e) => setPriceMax(e.target.value ? Number(e.target.value) : null)}>
          <option value="">£Min–£Max</option>
          {[5, 6, 7, 8, 9, 10, 12, 15].map((v) => <option key={v} value={v}>≤ £{v}.0m</option>)}
        </select>
        <span style={{ flex: 1 }} />
        <button className="pill-btn" onClick={exportCsv}>⬇ CSV</button>
        <button className="pill-btn" onClick={build} disabled={building}>
          {building ? <span className="spinner" /> : '⟳'} Rebuild
        </button>
      </div>

      <div className="ptable-wrap">
        <table className="ptable">
          <thead>
            <tr>
              <th className={`l ${sort.key === 'name' ? 'sorted' : ''}`} onClick={() => clickSort('name')}>Player</th>
              <th className={sort.key === 'price' ? 'sorted' : ''} onClick={() => clickSort('price')}>Price</th>
              {shown.map((g) => (
                <th key={g} className={sort.key === g ? 'sorted' : ''} onClick={() => clickSort(g)}>GW{g}</th>
              ))}
              <th className={sort.key === 'total' ? 'sorted' : ''} onClick={() => clickSort('total')}>Total {sort.key === 'total' ? (sort.dir < 0 ? '▾' : '▴') : ''}</th>
              <th className={sort.key === 'own' ? 'sorted' : ''} onClick={() => clickSort('own')}>Own%</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="l">
                  <div className="pl-cell">
                    <img src={badgeUrl(r.teamCode)} alt="" loading="lazy"
                      onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                    <div>
                      <div className="nm">{r.name}
                        {r.status && r.status !== 'a' && (
                          <span className="flag" title={r.news}
                            style={{ color: r.status === 'i' || r.status === 'o' ? 'var(--red)' : 'var(--gold)' }}>⬤</span>
                        )}
                      </div>
                      <div className="sub">{r.teamShort} · {r.position}</div>
                    </div>
                  </div>
                </td>
                <td className="num" style={{ color: 'var(--muted)' }}>£{r.price.toFixed(1)}m</td>
                {shown.map((g) => (
                  <td key={g}>
                    {r.eps[g] != null ? (
                      <span className="ep-cell" style={{ background: epColor(r.eps[g]) }}>
                        {fmt1(r.eps[g])}
                      </span>
                    ) : <span style={{ color: 'var(--muted-2)' }}>–</span>}
                  </td>
                ))}
                <td className="tot-cell">{r.total.toFixed(1)}</td>
                <td className="own-cell">{r.own.toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
