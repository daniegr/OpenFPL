import React, { useMemo, useRef, useState } from 'react'
import { api, pollJob } from '../api'
import { useStore } from '../store'
import { CHIP_SHORT, fmt1, money, planToDraft, withBaseline } from '../util'

const CHIP_DEFS = [
  ['wildcard', 'WC'], ['freehit', 'FH'], ['bench_boost', 'BB'], ['triple_captain', 'TC'],
]

export default function Solver({ goPlanner }) {
  const { status, entryId, entry, players, teams, byId, setDrafts,
          setActiveDraftId, setToast, refreshProjections } = useStore()

  const [horizon, setHorizon] = useState(5)
  const [solveFrom, setSolveFrom] = useState(null)
  const [ftValue, setFtValue] = useState(1.5)
  const [decay, setDecay] = useState(0.85)
  const [nPlans, setNPlans] = useState(3)
  const [advanced, setAdvanced] = useState(false)
  const [hitCost, setHitCost] = useState(4)
  const [benchWeight, setBenchWeight] = useState(0.1)
  const [maxTransfers, setMaxTransfers] = useState(3)
  const [timeLimit, setTimeLimit] = useState(60)
  const [keepPerPos, setKeepPerPos] = useState(30)
  const [ftOverride, setFtOverride] = useState('')
  const [useEntry, setUseEntry] = useState(true)

  const [chips, setChips] = useState({})       // name -> {enabled, force}
  const [locked, setLocked] = useState([])
  const [avoid, setAvoid] = useState([])
  const [banned, setBanned] = useState([])
  const [minFt, setMinFt] = useState([])       // [{gw, n}]

  const [running, setRunning] = useState(false)
  const [job, setJob] = useState(null)
  const [result, setResult] = useState(null)
  const logRef = useRef(null)

  const from = solveFrom || status?.next_gw || 1
  const gws = useMemo(() =>
    (status?.scheduled_gws || []).filter((g) => g >= from).slice(0, horizon),
    [status, from, horizon])

  const start = async () => {
    if (running) return
    setRunning(true)
    setResult(null)
    setJob({ progress: [], pct: 0 })
    const chipParams = {}
    for (const [name] of CHIP_DEFS) {
      const c = chips[name]
      if (c?.enabled) chipParams[name] = { enabled: true, force: c.force || null }
    }
    try {
      const { job_id } = await api.solve({
        entry: useEntry ? entryId : null,
        solve_from: from, horizon,
        decay, ft_value: ftValue, hit_cost: hitCost, bench_weight: benchWeight,
        max_transfers: maxTransfers, time_limit: timeLimit,
        keep_per_position: keepPerPos, n_plans: nPlans,
        free_transfers: ftOverride === '' ? null : Number(ftOverride),
        chips: chipParams,
        locked, avoid, banned_teams: banned,
        min_ft: Object.fromEntries(minFt.map((m) => [m.gw, m.n])),
      })
      const res = await pollJob(job_id, (j) => {
        setJob(j)
        requestAnimationFrame(() => {
          logRef.current?.scrollTo(0, logRef.current.scrollHeight)
        })
      })
      setResult(res)
      refreshProjections()
      setToast({ kind: 'ok', msg: `Solve finished — ${res.plans.length} plan(s).` })
    } catch (e) {
      setToast({ kind: 'err', msg: `Solve failed: ${e.message}` })
    } finally {
      setRunning(false)
    }
  }

  const addDraft = (plan, i) => {
    setDrafts((ds) => {
      const label = `Solve ${String.fromCharCode(65 + (ds.length % 26))}`
      const d = withBaseline(planToDraft(plan, result, label, `plan ${i + 1}`))
      setActiveDraftId(d.id)
      return [...ds, d]
    })
    setToast({ kind: 'ok', msg: 'Added to Planner as a draft.' })
    goPlanner()
  }

  return (
    <div className="solver-grid">
      {/* ---------------- parameters column ---------------- */}
      <div>
        <div className="panel" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
            <span className="section-label">Parameters &amp; decisions</span>
            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--muted)' }}>
              solve from{' '}
              <select className="pill-btn" value={from} style={{ background: 'var(--panel)', appearance: 'auto' }}
                onChange={(e) => setSolveFrom(Number(e.target.value))}>
                {(status?.scheduled_gws || []).slice(0, 20).map((g) =>
                  <option key={g} value={g}>GW {g}</option>)}
              </select>
            </span>
          </div>

          <div className="field">
            <div className="lbl">
              <span className="section-label">Transfer depth</span>
              <span className="hintdot" title="How many gameweeks the optimiser plans over">i</span>
              <span className="big-num" style={{ marginLeft: 'auto' }}>{horizon} <span style={{ fontSize: 12 }}>GWs</span></span>
            </div>
            <div className="slider-row">
              <input type="range" min={1} max={8} value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))} />
            </div>
            <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 4 }}>
              up to GW {gws[gws.length - 1] ?? '–'} · deeper = slower solve
            </div>
          </div>

          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
            <NumField label="FT value" hint="Bonus points for each banked free transfer at horizon end"
              value={ftValue} step={0.25} onChange={setFtValue} />
            <NumField label="Time decay" hint="Weight per future gameweek (uncertainty discount)"
              value={decay} step={0.05} onChange={(v) => setDecay(Math.min(1, Math.max(0.5, v)))} />
            <NumField label="Transfer plans" hint="How many alternative plans to generate"
              value={nPlans} step={1} onChange={(v) => setNPlans(Math.min(5, Math.max(1, Math.round(v))))} />
          </div>

          <button className="pill-btn" style={{ marginTop: 6 }}
            onClick={() => setAdvanced((a) => !a)}>
            Advanced settings {advanced ? '▴' : '▾'}
          </button>
          {advanced && (
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 14 }}>
              <NumField label="Hit cost" value={hitCost} step={1} onChange={setHitCost} />
              <NumField label="Bench weight" hint="How much bench points matter (autosub proxy)"
                value={benchWeight} step={0.05} onChange={setBenchWeight} />
              <NumField label="Max transfers / GW" value={maxTransfers} step={1}
                onChange={(v) => setMaxTransfers(Math.max(1, Math.round(v)))} />
              <NumField label="Solver seconds" value={timeLimit} step={15}
                onChange={(v) => setTimeLimit(Math.max(10, Math.round(v)))} />
              <NumField label="Pool / position" hint="Players kept per position in the MILP"
                value={keepPerPos} step={5} onChange={(v) => setKeepPerPos(Math.max(10, Math.round(v)))} />
              <div className="field">
                <div className="lbl"><span className="section-label">FTs override</span></div>
                <input className="num" placeholder="auto" value={ftOverride}
                  onChange={(e) => setFtOverride(e.target.value.replace(/\D/g, ''))}
                  style={{ width: 90, background: 'var(--bg-deep)', border: '1px solid var(--line)',
                           borderRadius: 7, padding: '9px 10px', outline: 'none' }} />
              </div>
              <div className="field">
                <div className="lbl"><span className="section-label">Use entry squad</span></div>
                <button className={`pill-btn ${useEntry ? 'accent' : ''}`}
                  onClick={() => setUseEntry((u) => !u)}>
                  {useEntry ? (entry?.team_name || `#${entryId}`) : 'fresh £100m squad'}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="panel" style={{ padding: 16 }}>
          <div className="section-label" style={{ color: 'var(--accent)', marginBottom: 12 }}>Chip plan</div>
          <div className="chipplan">
            {CHIP_DEFS.map(([name, short]) => {
              const c = chips[name] || {}
              return (
                <span key={name} className={`chip-btn ${c.enabled ? 'on' : ''}`}>
                  <button onClick={() => setChips((s) => ({ ...s, [name]: { ...c, enabled: !c.enabled } }))}>
                    ⚡ {short}
                  </button>
                  {c.enabled && (
                    <select value={c.force || ''}
                      onChange={(e) => setChips((s) => ({ ...s, [name]: { ...c, force: e.target.value ? Number(e.target.value) : null } }))}>
                      <option value="">free</option>
                      {gws.map((g) => <option key={g} value={g}>GW{g}</option>)}
                    </select>
                  )}
                </span>
              )
            })}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 10 }}>
            Enabled chips are available to the optimiser within the horizon;
            “free” lets it pick the week, or pin one.
          </div>
        </div>
      </div>

      {/* ---------------- constraints + run column ---------------- */}
      <div>
        <div className="panel" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <PlayerPicker label="Target / hold players" color="green" list={locked}
              setList={setLocked} players={players} byId={byId} exclude={avoid} />
            <PlayerPicker label="Avoid / sell players" color="red" list={avoid}
              setList={setAvoid} players={players} byId={byId} exclude={locked} />
          </div>
          <div style={{ marginTop: 16 }}>
            <div className="lbl" style={{ marginBottom: 8 }}>
              <span className="section-label">🚫 Do not buy from teams</span>
            </div>
            <div className="tagbox">
              {!banned.length && <span className="empty">No teams excluded.</span>}
              {banned.map((tid) => (
                <span className="tag red" key={tid}>
                  {teams[String(tid)]?.name || tid}
                  <button onClick={() => setBanned((b) => b.filter((x) => x !== tid))}>×</button>
                </span>
              ))}
              <select className="pill-btn" value="" style={{ background: 'var(--panel)', appearance: 'auto' }}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  if (v && !banned.includes(v)) setBanned((b) => [...b, v])
                }}>
                <option value="">+ add team</option>
                {Object.values(teams).sort((a, b) => a.name.localeCompare(b.name))
                  .map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <div className="lbl" style={{ marginBottom: 8 }}>
              <span className="section-label">→ Minimum available FTs</span>
            </div>
            <div className="tagbox">
              {!minFt.length && <span className="empty">No FT targets configured.</span>}
              {minFt.map((m, i) => (
                <span className="tag" key={i}>
                  GW{m.gw}: ≥{m.n} FT
                  <button onClick={() => setMinFt((l) => l.filter((_, k) => k !== i))}>×</button>
                </span>
              ))}
              <button className="pill-btn" onClick={() => {
                const gw = Number(prompt(`Gameweek (${gws[0]}–${gws[gws.length - 1]})`, gws[0]))
                const n = Number(prompt('Minimum FTs to hold after that GW', '2'))
                if (gw && n) setMinFt((l) => [...l, { gw, n }])
              }}>+ add target</button>
            </div>
          </div>
        </div>

        <div className="panel" style={{ padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{ fontSize: 12, color: 'var(--muted)' }}>
              {useEntry
                ? (entry?.squad
                  ? <>Optimising <b>{entry.team_name}</b> · {money(entry.bank)} itb · {entry.free_transfers} FT</>
                  : <>Pre-season: building a fresh £100m squad for <b>{entry?.team_name || `#${entryId}`}</b></>)
                : 'Building a fresh £100m squad'}
              {' '}· GW{gws[0]}–{gws[gws.length - 1]}
            </div>
            <button className="pill-btn accent" style={{ marginLeft: 'auto', padding: '12px 22px', fontSize: 13 }}
              onClick={start} disabled={running}>
              {running ? <span className="spinner" /> : '⚡'} START SOLVE
            </button>
          </div>

          {(running || job?.progress?.length > 0) && (
            <div style={{ marginTop: 14 }}>
              <div className="progressbar" style={{ marginBottom: 8 }}>
                <div style={{ width: `${Math.round((job?.pct || 0) * 100)}%` }} />
              </div>
              <div className="solve-log" ref={logRef}>
                {(job?.progress || []).map((p, i, arr) => (
                  <div key={i} className={i === arr.length - 1 ? 'last' : ''}>{p.msg}</div>
                ))}
              </div>
            </div>
          )}

          {result && (
            <div style={{ marginTop: 16 }}>
              {result.plans.map((plan, i) => (
                <PlanCard freeFirst={!!result?.state?.unlimited_transfers} key={i} plan={plan} i={i} addDraft={addDraft} byId={byId} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */

function NumField({ label, hint, value, step, onChange }) {
  return (
    <div className="field">
      <div className="lbl">
        <span className="section-label">{label}</span>
        {hint && <span className="hintdot" title={hint}>i</span>}
      </div>
      <div className="numctl">
        <button onClick={() => onChange(Math.round((value - step) * 100) / 100)}>−</button>
        <input className="num" value={value}
          onChange={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v)) onChange(v) }} />
        <button onClick={() => onChange(Math.round((value + step) * 100) / 100)}>+</button>
      </div>
    </div>
  )
}

function PlayerPicker({ label, color, list, setList, players, byId, exclude }) {
  const [q, setQ] = useState('')
  const opts = useMemo(() => {
    if (!q) return []
    const lq = q.toLowerCase()
    return players
      .filter((p) => !list.includes(p.id) && !exclude.includes(p.id))
      .filter((p) => p.web_name.toLowerCase().includes(lq) || p.name.toLowerCase().includes(lq))
      .sort((a, b) => b.own - a.own)
      .slice(0, 8)
  }, [q, players, list, exclude])

  return (
    <div>
      <div className="lbl" style={{ marginBottom: 8 }}>
        <span className="section-label" style={{ color: color === 'green' ? 'var(--green)' : 'var(--red)' }}>
          {color === 'green' ? '🔒' : '⛔'} {label}
        </span>
      </div>
      <div className="typeahead">
        <div className="search" style={{ minWidth: 0, marginBottom: 8 }}>
          🔍<input placeholder="Add player…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {opts.length > 0 && (
          <div className="ta-list">
            {opts.map((p) => (
              <div key={p.id} className="ta-item"
                onClick={() => { setList((l) => [...l, p.id]); setQ('') }}>
                <div>
                  <div style={{ fontWeight: 700 }}>{p.web_name}</div>
                  <div className="sub">{p.position} · own {p.own}%</div>
                </div>
                <span className="pr num">£{p.price.toFixed(1)}m</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="tagbox">
        {!list.length && <span className="empty">No players selected.</span>}
        {list.map((pid) => (
          <span className={`tag ${color}`} key={pid}>
            {byId.get(pid)?.web_name || pid}
            <button onClick={() => setList((l) => l.filter((x) => x !== pid))}>×</button>
          </span>
        ))}
      </div>
    </div>
  )
}

function PlanCard({ plan, i, addDraft, byId, freeFirst }) {
  const [open, setOpen] = useState(i === 0)
  const nm = (t) => t.name || byId.get(t.player_id)?.web_name || t.player_id
  return (
    <div className="plan-card">
      <div className="head" onClick={() => setOpen((o) => !o)} style={{ cursor: 'pointer' }}>
        <span className="chip pink num">#{i + 1}</span>
        Plan {i + 1}
        <span className="num" style={{ color: 'var(--muted)', fontSize: 12 }}>
          obj {fmt1(plan.objective)}
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="pill-btn accent"
            onClick={(e) => { e.stopPropagation(); addDraft(plan, i) }}>
            + Add to Planner
          </button>
          <span style={{ color: 'var(--muted-2)' }}>{open ? '▴' : '▾'}</span>
        </span>
      </div>
      {open && freeFirst && (
        <div className="plan-note">
          Pre-deadline: GW{plan.per_gw[0]?.gw} moves are free (FPL's unlimited transfers
          before the first deadline) — hits and free-transfer limits apply from the next GW.
        </div>
      )}
      {open && plan.per_gw.map((g, gi) => (
        <div className="plan-gw" key={g.gw}>
          <span className="g">GW{g.gw}</span>
          <div>
            {g.chip && <span className="chip gold" style={{ marginRight: 8 }}>{CHIP_SHORT[g.chip]}</span>}
            {freeFirst && gi === 0 && g.transfers_in.length > 0 && (
              <span className="chip green" style={{ marginRight: 8 }}>free rebuild</span>
            )}
            {g.transfers_out.length === 0 && !g.chip && (
              <span style={{ color: 'var(--muted-2)' }}>roll</span>
            )}
            {g.transfers_out.map((o, k) => (
              <div key={k} style={{ marginBottom: 2 }}>
                <span style={{ color: 'var(--muted)' }}>{nm(o)}</span>
                <span style={{ color: 'var(--accent)', margin: '0 8px' }}>→</span>
                <span style={{ fontWeight: 600 }}>{nm(g.transfers_in[k])}</span>
              </div>
            ))}
            <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 3 }}>
              XI {fmt1(g.xi_points)} pts · C {g.captain}
            </div>
          </div>
          <span className="meta">
            £{fmt1(g.bank)}m · {g.free_after}FT
            {g.hits ? <span style={{ color: 'var(--red)' }}> · −{g.hits * 4} hit</span> : ''}
          </span>
        </div>
      ))}
    </div>
  )
}
