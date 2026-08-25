import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { Donut, EOBars, LineChart, OverlapMatrix, Radar, StatTile, VIZ,
         VIZ_NEUTRAL } from '../charts'
import Flag from '../components/Flag'
import { useStore } from '../store'
import { badgeUrl, bestXI, epOf, fmt1, formationRows, money, shirtUrl } from '../util'

const CHIP_MAP = { wildcard: 'WC', freehit: 'FH', bboost: 'BB', '3xc': 'TC', manager: 'AM' }

const scale100 = (v, min, max) =>
  max - min < 1e-9 ? 50 : 5 + ((v - min) / (max - min)) * 95

export default function MiniLeague() {
  const { byId, teams, proj, status, entryId, entry, setToast } = useStore()
  const [leagueId, setLeagueId] = useState(() => localStorage.getItem('ofpl_league_id') || '')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [limit, setLimit] = useState(20)
  const [rivalId, setRivalId] = useState(null)

  const nextGw = status?.next_gw
  const load = async (id, lim = limit) => {
    if (!id || loading) return
    setLoading(true)
    try {
      const d = await api.league(id, { limit: lim })
      setData(d)
      localStorage.setItem('ofpl_league_id', String(id))
    } catch (e) {
      setToast({ kind: 'err', msg: `League fetch failed: ${e.message}` })
    } finally { setLoading(false) }
  }

  useEffect(() => {
    const saved = localStorage.getItem('ofpl_league_id')
    if (saved) load(parseInt(saved, 10))
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const withPicks = useMemo(() =>
    (data?.entries || []).filter((e) => e.picks?.length), [data])

  const me = useMemo(() =>
    data?.entries?.find((e) => e.entry === entryId) || null, [data, entryId])

  const myIds = useMemo(() => {
    if (me?.picks?.length) return new Set(me.picks.map((p) => p.element))
    return new Set((entry?.squad || []).map((p) => p.element))
  }, [me, entry])

  // default comparison rival: nearest rank above me, else leader
  useEffect(() => {
    if (!withPicks.length || rivalId) return
    const above = withPicks.filter((e) => e.entry !== entryId &&
      (me ? (e.rank || 99) < (me.rank || 99) : true))
    setRivalId((above[above.length - 1] || withPicks.find((e) => e.entry !== entryId))?.entry ?? null)
  }, [withPicks, me, entryId, rivalId])

  const rival = withPicks.find((e) => e.entry === rivalId) || null

  /* ---------------- derived analytics ---------------- */

  const projFor = (e) => {
    if (!e?.picks?.length || !nextGw) return null
    return e.picks.reduce((a, p) => a + epOf(proj, p.element, nextGw) * p.multiplier, 0)
  }

  // effective ownership across analysed squads
  const eo = useMemo(() => {
    const n = withPicks.length
    if (!n) return []
    const acc = new Map()
    for (const e of withPicks) {
      for (const p of e.picks) {
        const a = acc.get(p.element) || { owners: 0, caps: 0, multSum: 0, who: [] }
        a.owners += 1
        if (p.is_captain) a.caps += 1
        a.multSum += p.multiplier
        a.who.push(e.team)
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
        own: (a.owners / n) * 100, cap: (a.caps / n) * 100,
        eo: (a.multSum / n) * 100,
        owners: a.owners, who: a.who,
        globalOwn: stat?.own ?? 0,
        mine: myIds.has(el),
        ep: nextGw ? epOf(proj, el, nextGw) : 0,
      }
    }).sort((a, b) => b.eo - a.eo)
  }, [withPicks, byId, teams, myIds, proj, nextGw])

  const edge = eo.filter((r) => r.mine && r.own < 35).sort((a, b) => b.ep - a.ep).slice(0, 7)
  const threats = eo.filter((r) => !r.mine && r.own >= 50).sort((a, b) => b.eo - a.eo).slice(0, 7)
  const gems = eo.filter((r) => !r.mine && r.owners <= 2 && r.ep >= 3.5)
    .sort((a, b) => b.ep - a.ep).slice(0, 7)

  // playstyle metrics (raw), then league-scaled 0-100
  const styleRaw = useMemo(() => {
    const out = new Map()
    for (const e of withPicks) {
      const players = e.picks.map((p) => ({ ...p, s: byId.get(p.element) }))
        .filter((p) => p.s)
      const prices = players.map((p) => p.s.price).sort((a, b) => b - a)
      const totVal = prices.reduce((a, b) => a + b, 0) || 1
      const epAll = players.map((p) => ({
        p, ep: nextGw ? epOf(proj, p.element, nextGw) : 0,
      }))
      const epSum = epAll.reduce((a, d) => a + d.ep, 0) || 1
      const hist = e.history || []
      out.set(e.entry, {
        firepower: projFor(e) ?? 0,
        template: players.reduce((a, p) => a + p.s.own, 0) / (players.length || 1),
        premium: (prices[0] + (prices[1] || 0) + (prices[2] || 0)) / totVal * 100,
        attack: epAll.filter((d) => ['MID', 'FWD'].includes(d.p.s.position))
          .reduce((a, d) => a + d.ep, 0) / epSum * 100,
        bench: epAll.filter((d) => d.p.multiplier === 0)
          .reduce((a, d) => a + d.ep, 0) / epSum * 100,
        aggression: hist.reduce((a, h) => a + h.transfers, 0) +
          hist.reduce((a, h) => a + h.hit_points, 0) / 2,
      })
    }
    return out
  }, [withPicks, byId, proj, nextGw])  // eslint-disable-line react-hooks/exhaustive-deps

  const STYLE_AXES = [
    { key: 'firepower', label: 'Firepower', fmt: (v) => fmt1(v) },
    { key: 'template', label: 'Template', fmt: (v) => `${v?.toFixed(0)}%` },
    { key: 'premium', label: 'Premiums', fmt: (v) => `${v?.toFixed(0)}%` },
    { key: 'attack', label: 'Attack', fmt: (v) => `${v?.toFixed(0)}%` },
    { key: 'bench', label: 'Bench', fmt: (v) => `${v?.toFixed(0)}%` },
    { key: 'aggression', label: 'Transfers', fmt: (v) => v?.toFixed(0) },
  ]

  const styleSeries = useMemo(() => {
    if (!styleRaw.size) return []
    const bounds = {}
    for (const a of STYLE_AXES) {
      const vs = [...styleRaw.values()].map((m) => m[a.key])
      bounds[a.key] = [Math.min(...vs), Math.max(...vs)]
    }
    const scaled = (m) => STYLE_AXES.map((a) => scale100(m[a.key], ...bounds[a.key]))
    const raw = (m) => STYLE_AXES.map((a) => m[a.key])
    const avg = {}
    for (const a of STYLE_AXES) {
      avg[a.key] = [...styleRaw.values()].reduce((s, m) => s + m[a.key], 0) / styleRaw.size
    }
    const series = []
    const mine = me && styleRaw.get(me.entry)
    if (mine) series.push({ name: 'You', color: VIZ[0], values: scaled(mine), raw: raw(mine) })
    const rv = rival && styleRaw.get(rival.entry)
    if (rv) series.push({ name: rival.team, color: VIZ[1], values: scaled(rv), raw: raw(rv) })
    series.push({ name: 'League avg', color: VIZ[2], values: scaled(avg), raw: raw(avg) })
    return series
  }, [styleRaw, me, rival])  // eslint-disable-line react-hooks/exhaustive-deps

  // captaincy distribution (top 3 + other: all-pairs palette cap)
  const captaincy = useMemo(() => {
    const counts = new Map()
    for (const e of withPicks) {
      const c = e.picks.find((p) => p.is_captain)
      if (!c) continue
      const nm = byId.get(c.element)?.web_name || String(c.element)
      counts.set(nm, (counts.get(nm) || 0) + 1)
    }
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1])
    const top = sorted.slice(0, 3).map(([label, value], i) =>
      ({ label, value, color: VIZ[i] }))
    const rest = sorted.slice(3).reduce((a, [, v]) => a + v, 0)
    if (rest) top.push({ label: 'Other', value: rest, color: VIZ_NEUTRAL })
    return top
  }, [withPicks, byId])

  // season progression: top 5 by total + me (≤6 series, fixed slot order)
  const progression = useMemo(() => {
    const ranked = [...withPicks].sort((a, b) => (b.total || 0) - (a.total || 0))
    const chosen = ranked.slice(0, 5)
    if (me && !chosen.some((e) => e.entry === me.entry)) chosen.push(me)
    return chosen.map((e, i) => ({
      name: e.entry === entryId ? `${e.team} (you)` : e.team,
      me: e.entry === entryId,
      color: VIZ[i],
      points: (e.history || []).filter((h) => h.gw != null && h.total != null)
        .map((h) => ({ x: h.gw, y: h.total })),
    })).filter((s) => s.points.length)
  }, [withPicks, me, entryId])

  // squad overlap matrix (top 10 by rank + me)
  const overlap = useMemo(() => {
    const ranked = [...withPicks].sort((a, b) => (a.rank || 99) - (b.rank || 99))
    const chosen = ranked.slice(0, 10)
    if (me && !chosen.some((e) => e.entry === me.entry)) chosen.push(me)
    const sets = chosen.map((e) => new Set(e.picks.map((p) => p.element)))
    return {
      labels: chosen.map((e) => (e.entry === entryId ? `★ ${e.team}` : e.team)),
      get: (i, j) => [...sets[i]].filter((x) => sets[j].has(x)).length,
    }
  }, [withPicks, me, entryId])

  /* ---------------- template XI / best XI / insights ---------------- */

  const eoMap = useMemo(() => new Map(eo.map((r) => [r.id, r])), [eo])
  const posOf = (id) => byId.get(id)?.position || 'MID'

  const templateXI = useMemo(() => {
    if (!eo.length) return []
    return bestXI(eo.map((r) => r.id), posOf, (id) => eoMap.get(id)?.own ?? 0)
  }, [eo, eoMap])  // eslint-disable-line react-hooks/exhaustive-deps

  const bestLeagueXI = useMemo(() => {
    if (!eo.length) return []
    return bestXI(eo.map((r) => r.id), posOf, (id) => eoMap.get(id)?.ep ?? 0)
  }, [eo, eoMap])  // eslint-disable-line react-hooks/exhaustive-deps

  const templateCap = useMemo(() => templateXI.length
    ? templateXI.reduce((a, b) => ((eoMap.get(a)?.cap ?? 0) >= (eoMap.get(b)?.cap ?? 0) ? a : b))
    : null, [templateXI, eoMap])
  const bestCap = useMemo(() => bestLeagueXI.length
    ? bestLeagueXI.reduce((a, b) => ((eoMap.get(a)?.ep ?? 0) >= (eoMap.get(b)?.ep ?? 0) ? a : b))
    : null, [bestLeagueXI, eoMap])

  const xiProj = (xi, capId) => xi.reduce((a, id) => a + (eoMap.get(id)?.ep ?? 0), 0)
    + (capId ? (eoMap.get(capId)?.ep ?? 0) : 0)

  const insights = useMemo(() => {
    if (!withPicks.length) return null
    const myProjV = projFor(me)
    const rivals = withPicks.filter((e) => e.entry !== entryId)

    // captaincy edge: my captain's ep vs the field's average captain ep
    const myCapEl = me?.picks?.find((p) => p.is_captain)?.element
    const myCapEp = myCapEl != null ? (eoMap.get(myCapEl)?.ep ?? 0) : null
    const fieldCaps = rivals
      .map((e) => e.picks.find((p) => p.is_captain)?.element)
      .filter((x) => x != null)
    const fieldCapEp = fieldCaps.length
      ? fieldCaps.reduce((a, el) => a + (eoMap.get(el)?.ep ?? 0), 0) / fieldCaps.length
      : null

    // rank risk: rivals level/behind me (≤6 pts) who out-project me next gw
    const dangers = me == null ? [] : rivals
      .filter((e) => (e.total ?? 0) <= (me.total ?? 0))
      .map((e) => ({ e, gap: (me.total ?? 0) - (e.total ?? 0),
                     pgap: (projFor(e) ?? 0) - (myProjV ?? 0) }))
      .filter((d) => d.gap <= 6 && d.pgap > 0.5)
      .sort((a, b) => b.pgap - a.pgap).slice(0, 4)
    // catchable: rivals ahead within 6 pts I out-project
    const targets = me == null ? [] : rivals
      .filter((e) => (e.total ?? 0) > (me.total ?? 0))
      .map((e) => ({ e, gap: (e.total ?? 0) - (me.total ?? 0),
                     pgap: (myProjV ?? 0) - (projFor(e) ?? 0) }))
      .filter((d) => d.gap <= 6 && d.pgap > 0.5)
      .sort((a, b) => b.pgap - a.pgap).slice(0, 4)

    // chip arsenal still live among rivals
    const chipDefs = [['bboost', 'BB'], ['3xc', 'TC'], ['freehit', 'FH'], ['wildcard', 'WC']]
    const arsenal = chipDefs.map(([key, short]) => ({
      short,
      held: rivals.filter((e) => !e.chips_used.includes(key) && e.active_chip !== key).length,
    }))

    const tplProj = xiProj(templateXI, templateCap)
    return {
      myProjV, myCapEl, myCapEp, fieldCapEp, dangers, targets, arsenal,
      nRivals: rivals.length,
      tplProj,
      tplCoverage: templateXI.filter((id) => myIds.has(id)).length,
      bestCoverage: bestLeagueXI.filter((id) => myIds.has(id)).length,
    }
  }, [withPicks, me, entryId, eoMap, templateXI, templateCap, bestLeagueXI, myIds])  // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------------- header tiles ---------------- */

  const leader = data?.entries?.[0]
  const myProj = projFor(me)
  const aboveMe = me && data ? data.entries.filter((e) =>
    (e.rank || 99) < (me.rank || 99)).slice(-1)[0] : null
  const avgGw = withPicks.length
    ? withPicks.reduce((a, e) => a + (e.gw_points ?? e.event_total ?? 0), 0) / withPicks.length
    : null

  return (
    <div>
      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="filterbar">
          <span className="section-label">Mini league</span>
          <form onSubmit={(e) => { e.preventDefault(); load(parseInt(leagueId, 10)) }}
            style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div className="search" style={{ minWidth: 170 }}>
              🏆<input placeholder="league id…" value={leagueId}
                onChange={(e) => setLeagueId(e.target.value.replace(/\D/g, ''))} />
            </div>
            <select className="pill-btn" value={limit} style={{ background: 'var(--panel)', appearance: 'auto' }}
              onChange={(e) => { setLimit(Number(e.target.value)) }}>
              {[10, 20, 30, 50].map((n) => <option key={n} value={n}>top {n}</option>)}
            </select>
            <button className="pill-btn accent" disabled={loading || !leagueId}>
              {loading ? <span className="spinner" /> : '▶'} Analyse
            </button>
          </form>
          {!data && (
            <span style={{ fontSize: 11.5, color: 'var(--muted-2)' }}>
              the id is in the league's URL: /leagues/<b>12345</b>/standings/c
            </span>
          )}
          {data && (
            <>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 13, fontWeight: 800 }}>{data.name}</span>
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
            Enter a classic league id for the full dashboard: standings with
            projections, playstyle radar, effective ownership, captaincy,
            hidden gems, threats, squad overlap and season progression.
          </p>
        </div>
      )}

      {data && !withPicks.length && (
        <div className="panel center-note" style={{ marginBottom: 16 }}>
          <h3>Squads not public yet</h3>
          <p>FPL exposes picks once a gameweek deadline passes — re-analyse
            after the GW{data.gw ?? 1} deadline.</p>
        </div>
      )}

      {data && withPicks.length > 0 && (
        <>
          {/* ---- hero tiles ---- */}
          <div className="mld-tiles">
            <StatTile label="Your rank" value={me?.rank ?? '–'}
              delta={me?.last_rank && me?.rank
                ? (me.last_rank - me.rank > 0 ? `▲${me.last_rank - me.rank}` :
                   me.last_rank - me.rank < 0 ? `▼${me.rank - me.last_rank}` : '—')
                : null}
              deltaGood={(me?.last_rank ?? 0) - (me?.rank ?? 0) >= 0}
              sub={`of ${data.total_entries}`} />
            <StatTile label="Your points" value={me?.total ?? '–'}
              sub={me ? `${me.gw_points ?? 0} this gw` : ''} />
            <StatTile label="Gap to 1st" value={me && leader ? (leader.total - me.total) : '–'}
              sub={leader ? leader.team : ''} />
            <StatTile label="Gap to next" value={me && aboveMe ? (aboveMe.total - me.total) : '–'}
              sub={aboveMe ? aboveMe.team : me?.rank === 1 ? 'you lead' : ''} />
            <StatTile label={`Proj GW${nextGw ?? ''}`} value={myProj == null ? '–' : fmt1(myProj)}
              delta={myProj != null && avgGw != null && withPicks.length
                ? `${myProj >= avgProj(withPicks, projFor) ? '+' : ''}${fmt1(myProj - avgProj(withPicks, projFor))}`
                : null}
              deltaGood={myProj != null && myProj >= avgProj(withPicks, projFor)}
              sub="vs league avg" />
            <StatTile label="League avg GW" value={avgGw == null ? '–' : fmt1(avgGw)}
              sub={`${withPicks.length} squads`} />
          </div>

          {/* ---- main grid ---- */}
          <div className="mld-grid">
            {/* col 1: standings */}
            <div className="panel mld-standings">
              <div className="panel-head">
                Standings
                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted-2)' }}>
                  click a row to compare on the radar
                </span>
              </div>
              <div className="ptable-wrap" style={{ maxHeight: 640 }}>
                <table className="ptable">
                  <thead>
                    <tr>
                      <th>#</th><th className="l">Team</th><th>GW</th><th>Total</th>
                      <th>Captain</th><th>Chips</th><th>Value</th>
                      <th title="squad EP for the next gameweek from your model">Proj</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.entries.map((e) => {
                      const pj = projFor(e)
                      const mine = e.entry === entryId
                      const sel = e.entry === rivalId
                      const up = e.last_rank && e.rank ? e.last_rank - e.rank : 0
                      return (
                        <tr key={e.entry}
                          onClick={() => !mine && setRivalId(e.entry)}
                          style={{
                            cursor: mine ? 'default' : 'pointer',
                            background: mine ? 'var(--accent-soft)'
                              : sel ? 'rgba(217,89,38,0.10)' : undefined,
                            boxShadow: sel ? 'inset 3px 0 0 #d95926' : undefined,
                          }}>
                          <td className="num">
                            {e.rank ?? '–'}
                            {up !== 0 && (
                              <span style={{ color: up > 0 ? 'var(--green)' : 'var(--red)', fontSize: 10, marginLeft: 4 }}>
                                {up > 0 ? '▲' : '▼'}{Math.abs(up)}
                              </span>
                            )}
                          </td>
                          <td className="l">
                            <div style={{ fontWeight: 700, fontSize: 12.5 }}>
                              {e.team}
                              {mine && <span className="chip pink" style={{ marginLeft: 6 }}>you</span>}
                              {sel && <span className="chip" style={{ marginLeft: 6, background: '#d95926', color: '#fff' }}>vs</span>}
                            </div>
                            <div style={{ fontSize: 10, color: 'var(--muted-2)' }}>{e.manager}</div>
                          </td>
                          <td className="num">{e.gw_points ?? e.event_total ?? '–'}</td>
                          <td className="tot-cell" style={{ fontSize: 13 }}>{e.total ?? '–'}</td>
                          <td className="num" style={{ fontSize: 11.5 }}>
                            {captainOf(e, byId)}
                            {e.active_chip && (
                              <span className="chip gold" style={{ marginLeft: 5 }}>
                                {CHIP_MAP[e.active_chip] || e.active_chip}
                              </span>
                            )}
                          </td>
                          <td className="num" style={{ fontSize: 11, color: 'var(--muted)' }}>
                            {e.chips_used.length
                              ? e.chips_used.map((c) => CHIP_MAP[c] || c).join(' ') : '—'}
                          </td>
                          <td className="num" style={{ fontSize: 11.5, color: 'var(--muted)' }}>
                            {e.value ? money(e.value) : '–'}
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

            {/* col 2: radar + captaincy */}
            <div className="mld-col">
              <div className="panel">
                <div className="panel-head">
                  Playstyle radar
                  <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted-2)' }}>
                    league-scaled · vs {rival?.team || '—'}
                  </span>
                </div>
                <div style={{ padding: '14px 10px 12px' }}>
                  {styleSeries.length > 0 && (
                    <Radar axes={STYLE_AXES} series={styleSeries} size={320} />
                  )}
                  <p className="viz-note">
                    Firepower = projected GW{nextGw} squad points · Template = avg global
                    ownership of the 15 · Premiums = value share of the 3 most expensive ·
                    Attack = share of projection from MID/FWD · Bench = projection share
                    parked on the bench · Transfers = season moves + hit spend.
                  </p>
                </div>
              </div>
              <div className="panel">
                <div className="panel-head">Captaincy this GW</div>
                <div style={{ padding: '12px 10px' }}>
                  {captaincy.length > 0
                    ? <Donut slices={captaincy}
                        centre={[`${Math.round((captaincy[0].value / withPicks.length) * 100)}%`,
                                 captaincy[0].label]} />
                    : <p className="ml-note">No captains visible yet.</p>}
                </div>
              </div>
              <div className="panel">
                <div className="panel-head" style={{ color: 'var(--gold)' }}>
                  💎 Hidden gems in rivals' squads
                </div>
                {gems.length === 0 && (
                  <p className="ml-note">No low-owned, high-projection players hiding
                    in rival squads right now.</p>
                )}
                {gems.map((r) => (
                  <div key={r.id} className="ml-row">
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{r.name}<Flag p={byId.get(r.id)} /></div>
                      <div style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>
                        {r.teamShort} · held by {r.who.slice(0, 2).join(', ')}{r.who.length > 2 ? ` +${r.who.length - 2}` : ''}
                      </div>
                    </div>
                    <span className="chip gold num">{fmt1(r.ep)} ep</span>
                  </div>
                ))}
              </div>
            </div>

            {/* col 3: EO + edge/threats */}
            <div className="mld-col">
              <div className="panel">
                <div className="panel-head">
                  League ownership — {withPicks.length} squads
                  <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted-2)' }}>
                    ● = yours
                  </span>
                </div>
                <div style={{ padding: '10px 14px 12px' }}>
                  <EOBars rows={eo.slice(0, 16).map((r) => ({
                    label: r.name, sub: `${r.teamShort}`, own: r.own, cap: r.cap,
                    mine: r.mine,
                    tip: (
                      <><b>{r.name}</b> · {r.teamShort} {r.position}
                        <div className="viz-tip-row">owned {r.own.toFixed(0)}% · capped {r.cap.toFixed(0)}%</div>
                        <div className="viz-tip-row">EO {r.eo.toFixed(0)}% · global {r.globalOwn.toFixed(1)}%</div>
                        <div className="viz-tip-row">proj GW{nextGw}: <b>{fmt1(r.ep)}</b>{r.mine ? ' · in your squad' : ''}</div></>
                    ),
                  }))} />
                </div>
              </div>
              <div className="panel">
                <div className="panel-head" style={{ color: 'var(--green)' }}>
                  ▲ Your edge — differentials you hold
                </div>
                {edge.length === 0 && (
                  <p className="ml-note">No low-owned differentials in your squad —
                    you're running with the pack.</p>
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
                  ▼ Threats — template you don't own
                </div>
                {threats.length === 0 && (
                  <p className="ml-note">You cover every highly-owned player —
                    nothing swings against you from the template.</p>
                )}
                {threats.map((r) => (
                  <div key={r.id} className="ml-row">
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{r.name}<Flag p={byId.get(r.id)} /></div>
                      <div style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>
                        {r.teamShort} · owned {r.own.toFixed(0)}% · EO {r.eo.toFixed(0)}%
                      </div>
                    </div>
                    <span className="chip pink num">{fmt1(r.ep)} ep</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ---- XIs + insights ---- */}
          <div className="mld-xi">
            <div className="panel">
              <div className="panel-head">
                Template XI — most selected
                {insights && (
                  <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--muted)' }}>
                    you own <b style={{ color: insights.tplCoverage >= 7 ? 'var(--green)' : 'var(--gold)' }}>
                      {insights.tplCoverage}/11</b> · proj {fmt1(insights.tplProj)}
                  </span>
                )}
              </div>
              <XIPitch ids={templateXI} capId={templateCap} myIds={myIds}
                byId={byId} teams={teams}
                badge={(id) => `${(eoMap.get(id)?.own ?? 0).toFixed(0)}%`} />
            </div>
            <div className="panel">
              <div className="panel-head">
                Best XI — league-owned players
                {insights && (
                  <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--muted)' }}>
                    you own <b style={{ color: 'var(--gold)' }}>{insights.bestCoverage}/11</b>
                    {' '}· proj {fmt1(xiProj(bestLeagueXI, bestCap))}
                  </span>
                )}
              </div>
              <XIPitch ids={bestLeagueXI} capId={bestCap} myIds={myIds}
                byId={byId} teams={teams}
                badge={(id) => fmt1(eoMap.get(id)?.ep ?? 0)} />
            </div>
            <div className="panel">
              <div className="panel-head">⚡ Insights</div>
              {insights && (
                <div className="ins-list">
                  {insights.myCapEp != null && insights.fieldCapEp != null && (
                    <div className="ins-card">
                      <div className="ins-title">🎯 Captaincy edge</div>
                      <p>
                        Your captain <b>{byId.get(insights.myCapEl)?.web_name}</b> projects{' '}
                        <b className="num">{fmt1(insights.myCapEp)}</b> vs the field's
                        average captain <b className="num">{fmt1(insights.fieldCapEp)}</b> —{' '}
                        <b style={{ color: insights.myCapEp >= insights.fieldCapEp ? 'var(--green)' : 'var(--red)' }}>
                          {insights.myCapEp >= insights.fieldCapEp ? '+' : ''}
                          {fmt1(insights.myCapEp - insights.fieldCapEp)}
                        </b> per week of chasing.
                      </p>
                    </div>
                  )}
                  {insights.myProjV != null && (
                    <div className="ins-card">
                      <div className="ins-title">🛡 Template exposure</div>
                      <p>
                        The template XI projects <b className="num">{fmt1(insights.tplProj)}</b>{' '}
                        vs your <b className="num">{fmt1(insights.myProjV)}</b>
                        {insights.myProjV >= insights.tplProj
                          ? <> — you're <b style={{ color: 'var(--green)' }}>beating the pack</b> on paper.</>
                          : <> — the pack out-projects you by{' '}
                            <b style={{ color: 'var(--red)' }}>{fmt1(insights.tplProj - insights.myProjV)}</b>;
                            differentials need to land.</>}
                      </p>
                    </div>
                  )}
                  <div className="ins-card">
                    <div className="ins-title">⚔ Rank risk next GW</div>
                    {insights.dangers.length === 0
                      ? <p>Nobody level or just behind you out-projects you — your rank is safe on projections.</p>
                      : insights.dangers.map(({ e, gap, pgap }) => (
                        <p key={e.entry} className="ins-row">
                          <b>{e.team}</b> · {gap === 0 ? 'level' : `${gap} pt behind`} ·
                          projects <b style={{ color: 'var(--red)' }} className="num">+{fmt1(pgap)}</b> on you
                        </p>
                      ))}
                  </div>
                  <div className="ins-card">
                    <div className="ins-title">🏹 Catchable next GW</div>
                    {insights.targets.length === 0
                      ? <p>No one ahead within 6 points that you currently out-project.</p>
                      : insights.targets.map(({ e, gap, pgap }) => (
                        <p key={e.entry} className="ins-row">
                          <b>{e.team}</b> · {gap} pt ahead · you project{' '}
                          <b style={{ color: 'var(--green)' }} className="num">+{fmt1(pgap)}</b>
                        </p>
                      ))}
                  </div>
                  <div className="ins-card">
                    <div className="ins-title">♟ Chips still live ({insights.nRivals} rivals)</div>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
                      {insights.arsenal.map((a) => (
                        <span key={a.short} className="chip dim num"
                          title={`${a.held} rivals can still play ${a.short}`}>
                          {a.short} {a.held}/{insights.nRivals}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* ---- wide bottom row ---- */}
          <div className="mld-bottom">
            <div className="panel">
              <div className="panel-head">Season progression — total points</div>
              <div style={{ padding: '12px 14px' }}>
                {progression.length
                  ? <LineChart series={progression} yLabel="total pts" height={250} />
                  : <p className="ml-note">Charts grow as gameweeks complete.</p>}
              </div>
            </div>
            <div className="panel">
              <div className="panel-head">
                Squad overlap — shared players (of 15)
              </div>
              <div style={{ padding: '8px 12px 12px' }}>
                <OverlapMatrix labels={overlap.labels} get={overlap.get} />
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function XIPitch({ ids, capId, myIds, byId, teams, badge }) {
  const posOf = (id) => byId.get(id)?.position || 'MID'
  const rows = formationRows(ids, posOf)
  return (
    <div className="xi-pitch">
      {['GK', 'DEF', 'MID', 'FWD'].map((pp) => (
        <div className="xi-row" key={pp}>
          {rows[pp].map((id) => {
            const p = byId.get(id)
            const team = teams[String(p?.team_id)]
            return (
              <div key={id} className={`xi-card ${myIds.has(id) ? 'mine' : ''}`}
                title={myIds.has(id) ? 'in your squad' : undefined}>
                {capId === id && <span className="xi-arm">C</span>}
                <img alt="" src={shirtUrl(team?.code, posOf(id) === 'GK')}
                  onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                <div className="xi-name">{p?.web_name || id}</div>
                <div className="xi-badge num">{badge(id)}</div>
              </div>
            )
          })}
        </div>
      ))}
      <div className="xi-foot">
        <span><i className="eo-mine" /> in your squad</span>
      </div>
    </div>
  )
}

const captainOf = (e, byId) => {
  const c = e.picks?.find((p) => p.is_captain)
  return c ? (byId.get(c.element)?.web_name || c.element) : '–'
}

const avgProj = (entries, projFor) => {
  const vs = entries.map(projFor).filter((v) => v != null)
  return vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : 0
}
