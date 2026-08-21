import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import Flag from '../components/Flag'
import { useStore } from '../store'
import { badgeUrl, epOf, fmt1, money } from '../util'

const CHIP_MAP = { wildcard: 'WC', freehit: 'FH', bboost: 'BB', '3xc': 'TC', manager: 'AM' }

export default function MiniLeague() {
  const { byId, teams, proj, status, entryId, entry, setToast } = useStore()
  const [leagueId, setLeagueId] = useState(() => localStorage.getItem('ofpl_league_id') || '')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const nextGw = status?.next_gw
  const load = async (id) => {
    if (!id || loading) return
    setLoading(true)
    try {
      const d = await api.league(id, { limit: 20 })
      setData(d)
      localStorage.setItem('ofpl_league_id', String(id))
    } catch (e) {
      setToast({ kind: 'err', msg: `League fetch failed: ${e.message}` })
    } finally { setLoading(false) }
  }

  // re-analyse the saved league automatically when the tab opens
  useEffect(() => {
    const saved = localStorage.getItem('ofpl_league_id')
    if (saved) load(parseInt(saved, 10))
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // my squad: my entry inside the league if its picks are public, else the
  // locally saved squad (cookie import / manual entry)
  const myIds = useMemo(() => {
    const inLeague = data?.entries?.find((e) => e.entry === entryId)
    if (inLeague?.picks?.length) return new Set(inLeague.picks.map((p) => p.element))
    return new Set((entry?.squad || []).map((p) => p.element))
  }, [data, entryId, entry])

  const withPicks = useMemo(() =>
    (data?.entries || []).filter((e) => e.picks?.length), [data])

  // league ownership / effective ownership across analysed squads
  const eo = useMemo(() => {
    const n = withPicks.length
    if (!n) return []
    const acc = new Map()   // element -> {owners, caps, multSum}
    for (const e of withPicks) {
      for (const p of e.picks) {
        const a = acc.get(p.element) || { owners: 0, caps: 0, multSum: 0 }
        a.owners += 1
        if (p.is_captain) a.caps += 1
        a.multSum += p.multiplier
        acc.set(p.element, a)
      }
    }
    return [...acc.entries()].map(([el, a]) => {
      const stat = byId.get(el)
      return {
        id: el,
        name: stat?.web_name || el,
        teamShort: teams[String(stat?.team_id)]?.short || '',
        teamCode: teams[String(stat?.team_id)]?.code,
        position: stat?.position === 'GK' ? 'GKP' : stat?.position,
        own: a.owners / n * 100,
        cap: a.caps / n * 100,
        eo: a.multSum / n * 100,
        globalOwn: stat?.own ?? 0,
        mine: myIds.has(el),
        ep: nextGw ? epOf(proj, el, nextGw) : 0,
      }
    }).sort((a, b) => b.eo - a.eo)
  }, [withPicks, byId, teams, myIds, proj, nextGw])

  const edge = eo.filter((r) => r.mine && r.own < 35).sort((a, b) => b.ep - a.ep).slice(0, 8)
  const threats = eo.filter((r) => !r.mine && r.own >= 50).sort((a, b) => b.eo - a.eo).slice(0, 8)

  const projFor = (e) => {
    if (!e.picks?.length || !nextGw) return null
    return e.picks.reduce((a, p) => a + epOf(proj, p.element, nextGw) * p.multiplier, 0)
  }
  const captainOf = (e) => {
    const c = e.picks?.find((p) => p.is_captain)
    return c ? (byId.get(c.element)?.web_name || c.element) : '–'
  }

  return (
    <div>
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="filterbar">
          <span className="section-label">Mini league</span>
          <form onSubmit={(e) => { e.preventDefault(); load(parseInt(leagueId, 10)) }}
            style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div className="search" style={{ minWidth: 170 }}>
              🏆<input placeholder="league id…" value={leagueId}
                onChange={(e) => setLeagueId(e.target.value.replace(/\D/g, ''))} />
            </div>
            <button className="pill-btn accent" disabled={loading || !leagueId}>
              {loading ? <span className="spinner" /> : '▶'} Analyse
            </button>
          </form>
          <span style={{ fontSize: 11.5, color: 'var(--muted-2)' }}>
            the id is in the league's URL: /leagues/<b>12345</b>/standings/c
          </span>
          {data && (
            <>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 12.5, fontWeight: 700 }}>{data.name}</span>
              <span className="chip dim num">
                {data.analysed}/{data.total_entries} squads · GW{data.gw ?? '–'}
              </span>
            </>
          )}
        </div>
      </div>

      {!data && (
        <div className="panel center-note">
          <h3>Analyse your mini league</h3>
          <p style={{ maxWidth: 560, margin: '0 auto' }}>
            Enter a classic league id to pull the standings and every rival's
            public squad: league ownership, captaincy, chips used, projected
            points and — most importantly — where your team actually differs.
          </p>
        </div>
      )}

      {data && !withPicks.length && (
        <div className="panel center-note" style={{ marginBottom: 16 }}>
          <h3>Squads not public yet</h3>
          <p>FPL only exposes picks once a gameweek deadline has passed, so
            pre-season (and pre-deadline) only the standings are available.
            Re-analyse after the GW{data.gw ?? 1} deadline.</p>
        </div>
      )}

      {data && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="panel-head">Standings — top {data.entries.length}</div>
          <div className="ptable-wrap" style={{ maxHeight: 420 }}>
            <table className="ptable">
              <thead>
                <tr>
                  <th>#</th><th className="l">Team</th><th>GW</th><th>Total</th>
                  <th>Captain</th><th>Chips used</th><th>Value</th>
                  <th title="squad EP for the next gameweek from your model">Proj GW{nextGw}</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.map((e) => {
                  const pj = projFor(e)
                  const mine = e.entry === entryId
                  const up = e.last_rank && e.rank ? e.last_rank - e.rank : 0
                  return (
                    <tr key={e.entry} style={mine ? { background: 'var(--accent-soft)' } : undefined}>
                      <td className="num">
                        {e.rank ?? '–'}
                        {up !== 0 && (
                          <span style={{ color: up > 0 ? 'var(--green)' : 'var(--red)', fontSize: 10, marginLeft: 4 }}>
                            {up > 0 ? '▲' : '▼'}{Math.abs(up)}
                          </span>
                        )}
                      </td>
                      <td className="l">
                        <div style={{ fontWeight: 700, fontSize: 13 }}>
                          {e.team}{mine && <span className="chip pink" style={{ marginLeft: 7 }}>you</span>}
                        </div>
                        <div style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>{e.manager}</div>
                      </td>
                      <td className="num">{e.gw_points ?? e.event_total ?? '–'}</td>
                      <td className="tot-cell">{e.total ?? '–'}</td>
                      <td className="num" style={{ fontSize: 12 }}>
                        {captainOf(e)}
                        {e.active_chip && (
                          <span className="chip gold" style={{ marginLeft: 6 }}>
                            {CHIP_MAP[e.active_chip] || e.active_chip}
                          </span>
                        )}
                      </td>
                      <td className="num" style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                        {e.chips_used.length
                          ? e.chips_used.map((c) => CHIP_MAP[c] || c).join(' ')
                          : '—'}
                      </td>
                      <td className="num" style={{ color: 'var(--muted)' }}>
                        {e.value ? `${money(e.value)}` : '–'}
                      </td>
                      <td className="num" style={{ fontWeight: 700 }}>
                        {pj == null ? '–' : fmt1(pj)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {withPicks.length > 0 && (
        <div className="ml-grid">
          <div className="panel">
            <div className="panel-head">
              League ownership — effective ownership across {withPicks.length} squads
            </div>
            <div className="ptable-wrap" style={{ maxHeight: 480 }}>
              <table className="ptable">
                <thead>
                  <tr>
                    <th className="l">Player</th><th>Own%</th><th>Cap%</th>
                    <th title="ownership weighted by captaincy — the % of a haul you can't gain on">EO%</th>
                    <th>Global</th><th title="projected next-gw points">EP</th><th>You</th>
                  </tr>
                </thead>
                <tbody>
                  {eo.slice(0, 18).map((r) => (
                    <tr key={r.id}>
                      <td className="l">
                        <div className="pl-cell">
                          <img src={badgeUrl(r.teamCode)} alt="" loading="lazy"
                            onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                          <div>
                            <div className="nm">{r.name}<Flag p={byId.get(r.id)} /></div>
                            <div className="sub">{r.teamShort} · {r.position}</div>
                          </div>
                        </div>
                      </td>
                      <td className="num">{r.own.toFixed(0)}%</td>
                      <td className="num" style={{ color: r.cap ? 'var(--gold)' : 'var(--muted-2)' }}>
                        {r.cap.toFixed(0)}%
                      </td>
                      <td className="num" style={{ fontWeight: 700 }}>{r.eo.toFixed(0)}%</td>
                      <td className="num" style={{ color: 'var(--muted)' }}>{r.globalOwn.toFixed(1)}%</td>
                      <td className="num">{fmt1(r.ep)}</td>
                      <td style={{ textAlign: 'center' }}>
                        {r.mine ? <span style={{ color: 'var(--green)' }}>✓</span>
                          : <span style={{ color: 'var(--muted-2)' }}>–</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <div className="panel" style={{ marginBottom: 16 }}>
              <div className="panel-head" style={{ color: 'var(--green)' }}>
                ▲ Your edge — differentials you hold
              </div>
              {edge.length === 0 && (
                <p className="ml-note">No low-owned differentials in your squad —
                  you're running with the pack. Gaining rank needs captaincy or
                  transfer differentials.</p>
              )}
              {edge.map((r) => (
                <div key={r.id} className="ml-row">
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 13 }}>{r.name}<Flag p={byId.get(r.id)} /></div>
                    <div style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>
                      {r.teamShort} · league {r.own.toFixed(0)}% vs global {r.globalOwn.toFixed(1)}%
                    </div>
                  </div>
                  <span className="chip green num">{fmt1(r.ep)} ep</span>
                </div>
              ))}
            </div>
            <div className="panel">
              <div className="panel-head" style={{ color: 'var(--red)' }}>
                ▼ Threats — template players you don't own
              </div>
              {threats.length === 0 && (
                <p className="ml-note">You cover every highly-owned player in the
                  league — nothing can swing against you from the template.</p>
              )}
              {threats.map((r) => (
                <div key={r.id} className="ml-row">
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 13 }}>{r.name}<Flag p={byId.get(r.id)} /></div>
                    <div style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>
                      {r.teamShort} · owned by {r.own.toFixed(0)}% · EO {r.eo.toFixed(0)}%
                    </div>
                  </div>
                  <span className="chip pink num">{fmt1(r.ep)} ep</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
