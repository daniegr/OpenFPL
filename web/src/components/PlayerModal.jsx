import React, { useMemo, useState } from 'react'
import Flag from './Flag'
import { useFixtureLookup, useStore } from '../store'
import { availPct, epColor, epOf, fdrColor, fmt1, money } from '../util'

/* FPL Review-style player card: horizon projections, availability, per-90
   rates and fixture run for one player in the active draft. The EP column is
   the model output (fixture-aware); the per-90 columns are current season
   rates (xG-based once the season has data, else last-season history). */
export default function PlayerModal({ pid, draft, plan, actions, close }) {
  const { byId, teams, proj, projHistory } = useStore()
  const fixOf = useFixtureLookup()
  const [fdrMode, setFdrMode] = useState('diff_att')
  const p = byId.get(pid)
  const team = teams[String(p?.team_id)]
  const inXi = plan?.xi?.includes(pid)

  const gws = (draft?.gws || []).map((g) => g.gw)
  const ava = availPct(p)
  // engine xmins (from the projection run) already folds availability in;
  // the app-layer history estimate does not, so scale only the latter
  const engineXm = proj?.players?.[String(pid)]?.xmins
  const xminsBase = engineXm != null ? engineXm / Math.max(0.01, ava / 100) : (p?.xmins ?? 0)

  const rows = useMemo(() => gws.map((gw) => {
    const fixes = fixOf(p?.team_id, gw)
    const xm = xminsBase * (ava / 100) * Math.max(1, fixes.length)
    return { gw, fixes, ep: epOf(proj, pid, gw), xm }
  }), [gws.join(','), p, proj, pid, ava, xminsBase, fixOf])

  // model trend: this player's horizon total across projection builds
  const trend = useMemo(() => {
    const pts = []
    for (const s of projHistory || []) {
      let tot = 0, k = 0
      for (const gw of gws) {
        const v = s.gws?.[String(gw)]?.[String(pid)]
        if (v != null) { tot += v; k++ }
      }
      if (k) pts.push(tot)
    }
    return pts
  }, [projHistory, gws.join(','), pid])

  const n = rows.length || 1
  const totEp = rows.reduce((a, r) => a + r.ep, 0)
  const totXm = rows.reduce((a, r) => a + r.xm, 0)
  const goals = (p?.g90 || 0) * totXm / 90
  const assists = (p?.a90 || 0) * totXm / 90
  const cs = (p?.cs90 || 0) * totXm / 90
  const ppm = p?.price ? totEp / n / p.price : 0

  if (!p) return null

  return (
    <div className="pmodal-overlay" onClick={close}>
      <div className="pmodal" onClick={(e) => e.stopPropagation()}>
        <div className="head">
          <div>
            <h2>{p.web_name}</h2>
            <div className="sub">
              {team?.short || '?'} · {p.position} · {money(p.price)}
              <Flag p={p} />
              {p.status && p.status !== 'a' && p.news && (
                <span style={{ color: 'var(--muted)', marginLeft: 6 }}>{p.news}</span>
              )}
            </div>
          </div>
          <button className="close" onClick={close}>✕</button>
        </div>

        {actions && (
          <div className="actions">
            {inXi && <button className="pill-btn" onClick={actions.captain}>Ⓒ Captain</button>}
            {inXi && <button className="pill-btn" onClick={actions.vice}>Ⓥ Vice</button>}
            <button className="pill-btn" onClick={actions.swap}>⇄ Switch</button>
            <button className="pill-btn" style={{ color: 'var(--red)' }}
              onClick={actions.transfer}>✕ Transfer</button>
            {actions.undo && (
              <button className="pill-btn" style={{ color: 'var(--gold)' }}
                onClick={actions.undo}>↶ Undo transfer</button>
            )}
          </div>
        )}

        <div className="pd-grid">
          <div className="statgrid">
            <Tile k={`Points (${n} GW)`} v={fmt1(totEp)} bar={totEp / (6 * n)} />
            <Tile k="Goals" v={goals.toFixed(2)} bar={goals / (0.8 * n)} />
            <Tile k="Assists" v={assists.toFixed(2)} bar={assists / (0.8 * n)} />
            <Tile k="Clean sheets" v={cs.toFixed(2)} bar={cs / (0.6 * n)} />
          </div>
          <div>
            <div className="statgrid" style={{ gridTemplateColumns: '1fr 1fr 1fr', marginBottom: 10 }}>
              <Tile k="Ownership" v={`${(p.own ?? 0).toFixed(1)}%`} />
              <Tile k="Availability" v={`${ava}%`}
                warn={ava < 100} />
              <Tile k="PPM" v={ppm.toFixed(2)} sub="pts / gw / £m" />
            </div>
            <div className="fdr-head">
              <span className="section-label">Fixture difficulty</span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
                {[['diff_att', 'Attacking'], ['diff_def', 'Defensive']].map(([v, l]) => (
                  <button key={v}
                    className={`mode ${fdrMode === v ? 'on' : ''}`}
                    onClick={() => setFdrMode(v)}>{l}</button>
                ))}
              </span>
            </div>
            <div className="fdrbar">
              {rows.map((r) => {
                const d = r.fixes.length
                  ? r.fixes.reduce((a, f) => a + (f[fdrMode] ?? f.fdr ?? 3), 0) / r.fixes.length
                  : null
                const c = fdrColor(d)
                return (
                  <div className="col" key={r.gw}>
                    <div className="bar" style={{
                      height: d == null ? 4 : 8 + (d - 1) * 12,
                      background: c.bg,
                    }} title={d == null ? 'blank' : d.toFixed(1)} />
                    <div className="lbl">{r.gw} {r.fixes.map((f) =>
                      f.home ? f.oppShort : f.oppShort.toLowerCase()).join(',') || '–'}</div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        <div style={{ padding: '0 20px 16px' }}>
          <table className="pd-table">
            <thead>
              <tr>
                <th className="l">GW</th><th className="l">Opp</th><th>Pts</th>
                <th>xMins</th><th>Ava%</th><th>PK%</th><th>G90</th><th>A90</th>
                <th title={p.position === 'DEF' ? 'CBIT/90' : 'CBIRT/90'}>DC90</th>
                <th>CS90</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.gw}>
                  <td className="l">{r.gw}</td>
                  <td className="l">{r.fixes.map((f) =>
                    `${f.oppShort}${f.home ? ' (H)' : ' (A)'}`).join(', ') || '–'}</td>
                  <td><span className="ep-chip" style={{ background: epColor(r.ep) }}>{fmt1(r.ep)}</span></td>
                  <td>{Math.round(r.xm)}</td>
                  <td>{ava}%</td>
                  <td>{Math.round((p.pk_share || 0) * 100)}%</td>
                  <td>{(p.g90 ?? 0).toFixed(2)}</td>
                  <td>{(p.a90 ?? 0).toFixed(2)}</td>
                  <td>{(p.dc90 ?? 0).toFixed(2)}</td>
                  <td>{(p.cs90 ?? 0).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {trend.length >= 2 && (
            <div className="trend-row">
              <span className="section-label">Model trend</span>
              <Spark pts={trend} />
              {(() => {
                const d = trend[trend.length - 1] - trend[trend.length - 2]
                return (
                  <span className={`dv ${d >= 0 ? 'up' : 'down'}`}>
                    {d >= 0 ? '▲ +' : '▼ '}{fmt1(d)} vs previous build
                  </span>
                )
              })()}
              <span style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>
                horizon total over {trend.length} builds
              </span>
            </div>
          )}
          {p.recent_mins?.length > 0 && (
            <div className="recent-mins">
              <span className="section-label">Recent minutes</span>
              {p.recent_mins.map((m, i) => (
                <span key={i} className="minchip" style={{
                  background: m >= 60 ? 'rgba(47,214,128,0.16)' : m > 0 ? 'rgba(255,182,27,0.16)' : 'var(--panel-2)',
                  color: m >= 60 ? 'var(--green)' : m > 0 ? 'var(--gold)' : 'var(--muted-2)',
                }}>{m}′</span>
              ))}
              <span style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>
                newest first{p.start_rate != null ? ` · started ${Math.round(p.start_rate * 100)}% of last 10` : ''}
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Spark({ pts, w = 120, h = 26 }) {
  const lo = Math.min(...pts), hi = Math.max(...pts)
  const span = hi - lo || 1
  const xs = pts.map((v, i) => [
    (i / Math.max(1, pts.length - 1)) * (w - 4) + 2,
    h - 3 - ((v - lo) / span) * (h - 6),
  ])
  const up = pts[pts.length - 1] >= pts[0]
  return (
    <svg width={w} height={h} className="spark">
      <polyline fill="none" stroke={up ? 'var(--green)' : 'var(--red)'} strokeWidth="1.8"
        points={xs.map(([x, y]) => `${x},${y}`).join(' ')} />
      <circle cx={xs[xs.length - 1][0]} cy={xs[xs.length - 1][1]} r="2.5"
        fill={up ? 'var(--green)' : 'var(--red)'} />
    </svg>
  )
}

function Tile({ k, v, bar, sub, warn }) {
  return (
    <div className="stattile">
      <div className="k">{k}</div>
      <div className="v" style={warn ? { color: 'var(--gold)' } : undefined}>{v}</div>
      {sub && <div className="s">{sub}</div>}
      {bar != null && (
        <div className="tilebar">
          <div style={{ width: `${Math.max(3, Math.min(100, bar * 100))}%` }} />
        </div>
      )}
    </div>
  )
}
