import React, { useMemo, useState } from 'react'
import { api, pollJob } from '../api'
import { useStore } from '../store'
import Flag from '../components/Flag'
import { availPct, badgeUrl, downloadCSV, epColor, fmt1 } from '../util'

const POS_FILTERS = [
  ['ALL', 'All players'], ['GK', 'Goalkeepers'], ['DEF', 'Defenders'],
  ['MID', 'Midfielders'], ['FWD', 'Forwards'],
]

// Sortable metrics: key -> [label, ascending?, format]
const METRICS = {
  total:    ['Expected points', false, (v) => v.toFixed(1)],
  pts95:    ['Pts/95', false, (v) => v.toFixed(1)],
  fixtures: ['Fixtures', true, (v) => v.toFixed(2)],
  xmins:    ['xMins', false, (v) => String(Math.round(v))],
  mins:     ['Minutes', false, (v) => String(Math.round(v))],
  goals:    ['Goals /90', false, (v) => v.toFixed(2)],
  pk:       ['PK share', false, (v) => `${Math.round(v)}%`],
  assists:  ['Assists /90', false, (v) => v.toFixed(2)],
  cbit:     ['CBIT(R)/90', false, (v) => v.toFixed(2)],
  cs:       ['Clean sheets /90', false, (v) => v.toFixed(2)],
  avail:    ['Availability', false, (v) => `${Math.round(v)}%`],
  own:      ['Own%', false, (v) => `${v.toFixed(1)}%`],
  trend:    ['Trend vs last build', false, (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}`],
  ppm:      ['PPM', false, (v) => v.toFixed(2)],
}
// metrics that get their own extra column when sorted on
const EXTRA_COL = ['pts95', 'fixtures', 'xmins', 'mins', 'goals', 'pk',
                   'assists', 'cbit', 'cs', 'avail']

export default function Projections() {
  const { proj, teams, byId, fixtures, status, projHistory, refreshProjections, setToast } = useStore()
  const [q, setQ] = useState('')
  const [pos, setPos] = useState('ALL')
  const [gwSel, setGwSel] = useState(null)      // null = all projected gws
  const [gwOpen, setGwOpen] = useState(false)
  const [sort, setSort] = useState({ key: 'total', dir: -1 })
  const [sub, setSub] = useState('opp')         // secondary line in gw cells
  const [col3, setCol3] = useState('own')       // trailing column: own | ppm | none
  const [priceMax, setPriceMax] = useState(null)
  const [building, setBuilding] = useState(false)

  const projGws = (status?.projected_gws?.length
    ? status.projected_gws
    : Object.keys(proj?.gws || {}).map(Number)).sort((a, b) => a - b)
  const shown = (gwSel && gwSel.length ? gwSel : projGws)

  const fixCells = (teamId, g) => fixtures?.grid?.[String(teamId)]?.[String(g)] || []

  // the newest snapshot mirrors the current cache; compare with the one before
  const prevSnap = projHistory?.length >= 2 ? projHistory[projHistory.length - 2] : null

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
      let diffSum = 0
      for (const g of shown) {
        const v = rec.ep[String(g)]
        eps[g] = v
        total += v || 0
        const fs = fixCells(rec.team_id, g)
        diffSum += fs.length
          ? fs.reduce((a, f) => a + (f.diff ?? f.fdr ?? 3), 0) / fs.length
          : 4.5   // blank counts as hard
      }
      const n = shown.length || 1
      let prevTotal = null
      if (prevSnap) {
        for (const g of shown) {
          const v = prevSnap.gws?.[String(g)]?.[String(rec.player_id)]
          if (v != null) prevTotal = (prevTotal || 0) + v
        }
      }
      const ava = availPct(stat)
      const xm = (stat?.xmins ?? 0) * ava / 100
      out.push({
        id: rec.player_id,
        name: stat?.web_name || rec.player,
        team_id: rec.team_id,
        teamShort: teams[String(rec.team_id)]?.short || rec.team,
        teamCode: teams[String(rec.team_id)]?.code,
        position: rec.position === 'GK' ? 'GKP' : rec.position,
        price: rec.price,
        status: stat?.status,
        news: stat?.news,
        chance: stat?.chance,
        eps, total,
        hasTrend: prevTotal != null,
        m: {
          total,
          trend: prevTotal == null ? 0 : total - prevTotal,
          pts95: xm > 0 ? (total / n) / xm * 95 : 0,
          fixtures: diffSum / n,
          xmins: xm,
          mins: stat?.mins ?? 0,
          goals: stat?.g90 ?? 0,
          pk: (stat?.pk_share ?? 0) * 100,
          assists: stat?.a90 ?? 0,
          cbit: stat?.dc90 ?? 0,
          cs: stat?.cs90 ?? 0,
          avail: ava,
          own: stat?.own ?? 0,
          ppm: rec.price > 0 ? (total / n) / rec.price : 0,
        },
      })
    }
    const dir = sort.dir
    out.sort((a, b) => {
      const va = sort.key === 'price' ? a.price
        : sort.key === 'name' ? a.name
        : METRICS[sort.key] ? a.m[sort.key]
        : a.eps[sort.key] || 0
      const vb = sort.key === 'price' ? b.price
        : sort.key === 'name' ? b.name
        : METRICS[sort.key] ? b.m[sort.key]
        : b.eps[sort.key] || 0
      return (va < vb ? -1 : va > vb ? 1 : 0) * dir
    })
    return out.slice(0, 400)
  }, [proj, byId, teams, fixtures, prevSnap, q, pos, priceMax, shown, sort])

  const clickSort = (key) => setSort((s) => {
    if (s.key === key) return { key, dir: -s.dir }
    const asc = key === 'name' || (METRICS[key] && METRICS[key][1])
    return { key, dir: asc ? 1 : -1 }
  })

  const extraKey = EXTRA_COL.includes(sort.key) ? sort.key : null

  const build = async () => {
    if (building) return
    const from = status?.next_gw || 1
    const gws = (status?.scheduled_gws || []).filter((g) => g >= from).slice(0, 6)
    if (!gws.length) {
      setToast({ kind: 'err', msg: 'No upcoming gameweeks known — run a data pull (⟳ Data, top right) first.' })
      return
    }
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
      rows.map((r) => [r.name, r.teamShort, r.position, r.price, r.m.own,
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
        <select className="pill-btn" style={{ background: 'var(--panel)', appearance: 'auto' }}
          value={METRICS[sort.key] ? sort.key : 'total'}
          onChange={(e) => clickSort(e.target.value)}
          title="Sort players by…">
          {Object.entries(METRICS).map(([k, [l]]) => (
            <option key={k} value={k}>Sort: {l}</option>
          ))}
        </select>
        <select className="pill-btn" style={{ background: 'var(--panel)', appearance: 'auto' }}
          value={sub} onChange={(e) => setSub(e.target.value)}
          title="Secondary info shown in each GW cell">
          <option value="none">Secondary: none</option>
          <option value="opp">Secondary: opponent</option>
          <option value="xmins">Secondary: xMins</option>
        </select>
        <select className="pill-btn" style={{ background: 'var(--panel)', appearance: 'auto' }}
          value={col3} onChange={(e) => setCol3(e.target.value)}
          title="Trailing column">
          <option value="own">Own%</option>
          <option value="ppm">PPM</option>
          <option value="none">None</option>
        </select>
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
              {extraKey && (
                <th className="sorted" onClick={() => clickSort(extraKey)}>
                  {METRICS[extraKey][0]} {sort.dir < 0 ? '▾' : '▴'}
                </th>
              )}
              <th className={sort.key === 'total' ? 'sorted' : ''} onClick={() => clickSort('total')}>Total {sort.key === 'total' ? (sort.dir < 0 ? '▾' : '▴') : ''}</th>
              <th className={sort.key === 'trend' ? 'sorted' : ''} onClick={() => clickSort('trend')}
                title="change in the total since the previous projection build">Δ build {sort.key === 'trend' ? (sort.dir < 0 ? '▾' : '▴') : ''}</th>
              {col3 !== 'none' && (
                <th className={sort.key === col3 ? 'sorted' : ''} onClick={() => clickSort(col3)}>
                  {METRICS[col3][0]} {sort.key === col3 ? (sort.dir < 0 ? '▾' : '▴') : ''}
                </th>
              )}
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
                        <Flag p={{ status: r.status, news: r.news, chance: r.chance }} />
                      </div>
                      <div className="sub">{r.teamShort} · {r.position}</div>
                    </div>
                  </div>
                </td>
                <td className="num" style={{ color: 'var(--muted)' }}>£{r.price.toFixed(1)}m</td>
                {shown.map((g) => {
                  const fs = fixCells(r.team_id, g)
                  const opp = fs.map((f) => {
                    const s = teams[String(f.opp)]?.short || '?'
                    return f.home ? s : s.toLowerCase()
                  }).join(',')
                  return (
                    <td key={g}>
                      {r.eps[g] != null ? (
                        <span className="ep-cell" style={{ background: epColor(r.eps[g]) }}>
                          {fmt1(r.eps[g])}
                          {sub === 'opp' && <span className="ep-sub">{opp || 'blank'}</span>}
                          {sub === 'xmins' && (
                            <span className="ep-sub">
                              {Math.round(r.m.xmins * Math.max(1, fs.length))}′
                            </span>
                          )}
                        </span>
                      ) : <span style={{ color: 'var(--muted-2)' }}>–</span>}
                    </td>
                  )
                })}
                {extraKey && (
                  <td className="num" style={{ color: 'var(--text)', fontWeight: 700 }}>
                    {METRICS[extraKey][2](r.m[extraKey])}
                  </td>
                )}
                <td className="tot-cell">{r.total.toFixed(1)}</td>
                <td>
                  {r.hasTrend && Math.abs(r.m.trend) >= 0.05 ? (
                    <span className={`dv ${r.m.trend >= 0 ? 'up' : 'down'}`} style={{ marginLeft: 0 }}>
                      {r.m.trend >= 0 ? '▲' : '▼'} {Math.abs(r.m.trend).toFixed(1)}
                    </span>
                  ) : <span style={{ color: 'var(--muted-2)' }}>–</span>}
                </td>
                {col3 !== 'none' && (
                  <td className="own-cell">{METRICS[col3][2](r.m[col3])}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
