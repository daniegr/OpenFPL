import React, { useEffect, useMemo, useRef, useState } from 'react'
import Flag from '../components/Flag'
import PlayerModal from '../components/PlayerModal'
import { useFixtureLookup, useStore } from '../store'
import {
  CHIP_LONG, CHIP_SHORT, POS_ORDER, baselineDeltas, bestXI, epOf, formationRows,
  fmt1, gwEV, money, shirtUrl, withBaseline, xiLegal,
} from '../util'

export default function Planner() {
  const { drafts, setDrafts, activeDraftId, setActiveDraftId, proj, byId, players,
          entry, status, setToast } = useStore()
  const draft = drafts.find((d) => d.id === activeDraftId) || drafts[0] || null
  const [gwIdx, setGwIdx] = useState(0)
  const [sel, setSel] = useState(null)          // {pid, mode: 'swap'}
  const [statPid, setStatPid] = useState(null)  // player stats modal
  const [xfer, setXfer] = useState(null)        // pid being transferred out (modal)
  const [armed, setArmed] = useState(null)      // player picked from search, to bring in
  const undoRef = useRef([])                    // previous draft states (this session)
  const [undoN, setUndoN] = useState(0)

  const plan = draft?.gws?.[Math.min(gwIdx, (draft?.gws?.length || 1) - 1)] || null
  const posOf = (pid) => byId.get(pid)?.position || 'MID'

  // every edit goes through here: snapshot for undo, then mutate a clone
  const updateDraft = (fn, { record = true } = {}) => {
    setDrafts((ds) => ds.map((d) => {
      if (d.id !== draft.id) return d
      if (record) {
        undoRef.current.push(structuredClone(d))
        if (undoRef.current.length > 60) undoRef.current.shift()
      }
      return fn(structuredClone(d))
    }))
    if (record) setUndoN((n) => n + 1)
  }
  const undo = () => {
    const prev = undoRef.current.pop()
    if (!prev) return
    setDrafts((ds) => ds.map((d) => (d.id === prev.id ? prev : d)))
    setUndoN((n) => Math.max(0, n - 1))
    setToast({ kind: 'ok', msg: 'Undone.' })
  }
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !e.target.closest('input,textarea')) {
        e.preventDefault(); undo()
      }
      if (e.key === 'Escape') { setArmed(null); setSel(null) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // A transfer out->in from the current gw forward (Free Hit gws don't
  // propagate). Records the out player's sell price so it can be undone.
  const applyTransfer = (outId, inP) => {
    const cur = draft.gws[gwIdx]
    const out = cur.squad.find((s) => s.id === outId)
    const budget = (cur.bank || 0) + (out?.sell || 0)
    if (posOf(outId) !== inP.position) {
      setToast({ kind: 'err', msg: `${inP.web_name} is a ${inP.position} — replace a ${inP.position}.` })
      return false
    }
    if (cur.squad.some((s) => s.id === inP.id)) {
      setToast({ kind: 'err', msg: `${inP.web_name} is already in the squad.` }); return false
    }
    if (inP.price > budget + 1e-9) {
      setToast({ kind: 'err', msg: `Can't afford ${inP.web_name} (${money(inP.price)} > ${money(budget)} available).` })
      return false
    }
    const club = cur.squad.filter((s) => s.id !== outId && byId.get(s.id)?.team_id === inP.team_id).length
    if (club >= 3) {
      setToast({ kind: 'err', msg: `Already 3 players from ${inP.team_id ? 'that club' : 'the club'}.` }); return false
    }
    updateDraft((d) => {
      const delta = (out?.sell || 0) - inP.price
      const end = d.gws[gwIdx].chip === 'freehit' ? gwIdx + 1 : d.gws.length
      for (let t = gwIdx; t < end; t++) {
        const g = d.gws[t]
        if (!g.squad.some((s) => s.id === outId)) break
        g.squad = g.squad.filter((s) => s.id !== outId)
        g.squad.push({ id: inP.id, sell: inP.price })
        if (g.xi.includes(outId)) g.xi = g.xi.map((id) => (id === outId ? inP.id : id))
        if (g.captain === outId) g.captain = inP.id
        if (g.vice === outId) g.vice = inP.id
        g.bank = Math.round(((g.bank || 0) + delta) * 10) / 10
        if (t === gwIdx) {
          g.transfers_out = [...g.transfers_out, outId]
          g.transfers_in = [...g.transfers_in, inP.id]
          g.sold = { ...(g.sold || {}), [outId]: out?.sell || 0 }
        }
      }
      return d
    })
    return true
  }

  // Reverse one manual transfer (identified by the incoming player).
  const undoTransfer = (inId) => {
    const t0 = draft.gws.findIndex((g) => g.transfers_in.includes(inId))
    if (t0 < 0) return
    const g0 = draft.gws[t0]
    const k = g0.transfers_in.indexOf(inId)
    const outId = g0.transfers_out[k]
    const outSell = g0.sold?.[outId] ?? byId.get(outId)?.price ?? 0
    const inPrice = g0.squad.find((s) => s.id === inId)?.sell ?? byId.get(inId)?.price ?? 0
    updateDraft((d) => {
      for (let t = t0; t < d.gws.length; t++) {
        const g = d.gws[t]
        if (!g.squad.some((s) => s.id === inId)) break
        g.squad = g.squad.filter((s) => s.id !== inId)
        g.squad.push({ id: outId, sell: outSell })
        if (g.xi.includes(inId)) g.xi = g.xi.map((id) => (id === inId ? outId : id))
        if (g.captain === inId) g.captain = outId
        if (g.vice === inId) g.vice = outId
        g.bank = Math.round(((g.bank || 0) + inPrice - outSell) * 10) / 10
      }
      const g = d.gws[t0]
      g.transfers_in = g.transfers_in.filter((_, i) => i !== k)
      g.transfers_out = g.transfers_out.filter((_, i) => i !== k)
      return d
    })
  }

  const createFromEntry = () => {
    if (!entry?.squad) return
    const horizon = (status?.scheduled_gws || []).filter((g) => g >= status.next_gw).slice(0, 4)
    if (!horizon.length) {
      setToast({ kind: 'err', msg: 'No upcoming gameweeks known yet — run a data pull (⟳ Data, top right) first.' })
      return
    }
    const squad = entry.squad.map((p) => ({ id: p.element, sell: p.selling_price }))
    const ids = squad.map((s) => s.id)
    const picksXi = entry.squad.filter((p) => p.multiplier > 0).map((p) => p.element)
    const gws = horizon.map((gw) => {
      const epFor = (id) => epOf(proj, id, gw)
      const xi = xiLegal(picksXi, posOf) ? [...picksXi] : bestXI(ids, posOf, epFor)
      const cap = entry.squad.find((p) => p.is_captain)?.element
      const vice = entry.squad.find((p) => p.is_vice)?.element
      const sorted = [...xi].sort((a, b) => epFor(b) - epFor(a))
      const captain = cap && xi.includes(cap) ? cap : sorted[0]
      return {
        gw, chip: null, squad: structuredClone(squad), xi,
        captain,
        vice: vice && xi.includes(vice) && vice !== captain
          ? vice : sorted.find((id) => id !== captain) || null,
        transfers_in: [], transfers_out: [],
        bank: entry.bank, free_after: entry.free_transfers, free_used: 0, hits: 0,
      }
    })
    const label = String.fromCharCode(65 + drafts.length)
    const d = withBaseline({ id: `d${Date.now()}`, label: `Draft ${label}`, source: 'entry', gws })
    setDrafts((ds) => [...ds, d])
    setActiveDraftId(d.id)
  }

  if (!draft) {
    return (
      <div className="panel center-note">
        <h3>No drafts yet</h3>
        <p style={{ maxWidth: 560, margin: '0 auto 18px' }}>
          Run the <b>Solver</b> to generate optimised plans (they land here as
          drafts){entry?.squad ? ', or start from your current squad:' : '.'}
          {!entry?.squad && (
            <> The public FPL API can't see your picks before the deadline —
            use <b>⚠ set my team</b> (top right) to import your squad with your
            FPL login, or enter it manually. Otherwise the solver builds a
            fresh 15 from £100m.</>
          )}
        </p>
        {entry?.squad && (
          <button className="pill-btn accent" onClick={createFromEntry}>
            + Draft from current squad
          </button>
        )}
      </div>
    )
  }

  if (!draft.gws?.length) {
    return (
      <div className="panel center-note">
        <h3>{draft.label} has no gameweeks</h3>
        <p style={{ maxWidth: 520, margin: '0 auto 18px' }}>
          It was created before any fixture data was loaded, so there is
          nothing to plan. Delete it and re-create it from your squad.
        </p>
        <button className="pill-btn accent" onClick={() => {
          setDrafts((ds) => ds.filter((x) => x.id !== draft.id))
          setActiveDraftId(drafts.find((x) => x.id !== draft.id)?.id || null)
        }}>
          ✕ Delete this draft
        </button>
      </div>
    )
  }

  const evs = draft.gws.map((p) => gwEV(p, proj))
  const deltas = baselineDeltas(draft, proj)
  const isBuild = plan?.transfers_in.length === 15   // pre-season squad build
  const nMoves = plan && !isBuild ? plan.transfers_in.length : 0

  // manual moves vs baseline for the current gw (solver moves are in the baseline)
  const baseSquad = new Set((draft.baseline?.[gwIdx]?.squad || plan.squad).map((s) => s.id))
  const manualMoves = plan ? plan.transfers_in
    .map((inId, k) => ({ inId, outId: plan.transfers_out[k] }))
    .filter((m) => !draft.baseline || !baseSquad.has(m.inId)) : []

  return (
    <div>
      <GwBar draft={draft} gwIdx={gwIdx} setGwIdx={setGwIdx} evs={evs} deltas={deltas}
        plan={plan} nMoves={nMoves} updateDraft={updateDraft} undo={undo} canUndo={undoN > 0} />
      <div className="planner-grid">
        <div>
          {plan && (
            <PitchView plan={plan} draft={draft} sel={sel} setSel={setSel}
              setStatPid={setStatPid} posOf={posOf} armed={armed} setArmed={setArmed}
              applyTransfer={applyTransfer}
              gwIdx={gwIdx} setToast={setToast} updateDraft={updateDraft} />
          )}
          {plan && (
            <ChangesStrip moves={manualMoves} plan={plan} draft={draft} gwIdx={gwIdx}
              byId={byId} proj={proj} undoTransfer={undoTransfer} />
          )}
        </div>
        <div>
          <AddPlayerPanel plan={plan} players={players} byId={byId} proj={proj}
            posOf={posOf} armed={armed} setArmed={setArmed} />
          <ChipAdvisor draft={draft} proj={proj} byId={byId} players={players}
            posOf={posOf} updateDraft={updateDraft} />
          <DraftsPanel drafts={drafts} setDrafts={setDrafts} proj={proj}
            activeDraftId={draft.id} setActiveDraftId={setActiveDraftId}
            gwIdx={gwIdx} setGwIdx={setGwIdx} createFromEntry={createFromEntry}
            entry={entry} />
          <PathsPanel draft={draft} byId={byId} />
        </div>
      </div>
      {statPid && plan && (
        <PlayerModal pid={statPid} draft={draft} plan={plan}
          close={() => setStatPid(null)}
          actions={{
            captain: () => {
              if (!plan.xi.includes(statPid)) return
              updateDraft((d) => {
                const g = d.gws[gwIdx]
                if (g.vice === statPid) g.vice = g.captain
                g.captain = statPid
                return d
              })
              setStatPid(null)
            },
            vice: () => {
              if (!plan.xi.includes(statPid) || plan.captain === statPid) return
              updateDraft((d) => { d.gws[gwIdx].vice = statPid; return d })
              setStatPid(null)
            },
            swap: () => { setSel({ pid: statPid, mode: 'swap' }); setStatPid(null) },
            transfer: () => { setXfer(statPid); setStatPid(null) },
            undo: manualMoves.some((m) => m.inId === statPid)
              ? () => { undoTransfer(statPid); setStatPid(null) } : null,
          }} />
      )}
      {xfer && plan && (
        <TransferModal draft={draft} gwIdx={gwIdx} outId={xfer} byId={byId}
          proj={proj} posOf={posOf} close={() => setXfer(null)}
          applyTransfer={applyTransfer} />
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

const ALL_CHIPS = ['bench_boost', 'triple_captain', 'wildcard', 'freehit']

function GwBar({ draft, gwIdx, setGwIdx, evs, deltas, plan, nMoves, updateDraft, undo, canUndo }) {
  const total = evs.reduce((a, b) => a + b, 0)
  const dTotal = deltas ? deltas.reduce((a, b) => a + b, 0) : null
  const [openChip, setOpenChip] = useState(null)

  const setChip = (c, gw) => {
    updateDraft((d) => {
      for (const p of d.gws) if (p.chip === c) p.chip = null
      if (gw != null) {
        const t = d.gws.find((p) => p.gw === gw)
        if (t) t.chip = c
      }
      return d
    })
    setOpenChip(null)
  }

  const Delta = ({ v }) => (v == null || Math.abs(v) < 0.05 ? null : (
    <span className={`dv ${v > 0 ? 'up' : 'down'}`}>{v > 0 ? '+' : ''}{fmt1(v)}</span>
  ))

  return (
    <div className="gwbar">
      <div className="pager">
        {draft.gws.map((p, i) => (
          <button key={p.gw} className={`gw-dot ${i === gwIdx ? 'active' : ''}`}
            onClick={() => setGwIdx(i)}>
            {p.gw}
            {p.chip && <span className="chipmark">{CHIP_SHORT[p.chip]}</span>}
          </button>
        ))}
      </div>
      <div className="chipbar">
        {openChip && (
          <div style={{ position: 'fixed', inset: 0, zIndex: 39 }}
            onClick={() => setOpenChip(null)} />
        )}
        {ALL_CHIPS.map((c) => {
          const at = draft.gws.find((p) => p.chip === c)
          return (
            <div className="dd" key={c}>
              <button className={`chip-btn ${at ? 'on' : ''}`}
                title={CHIP_LONG[c].replace(' Played', '')}
                onClick={() => setOpenChip(openChip === c ? null : c)}>
                ⚡ {CHIP_SHORT[c]}{at ? ` GW${at.gw}` : ''} ▾
              </button>
              {openChip === c && (
                <div className="dd-menu" style={{ minWidth: 150 }}>
                  <div className="ttl">{CHIP_LONG[c].replace(' Played', '')}</div>
                  <button className="dd-item" onClick={() => setChip(c, null)}>
                    Don't use
                  </button>
                  {draft.gws.map((p) => (
                    <button key={p.gw}
                      className={`dd-item ${p.chip === c ? 'on' : ''}`}
                      onClick={() => setChip(c, p.gw)}>
                      GW{p.gw}
                      {p.chip && p.chip !== c && (
                        <span style={{ color: 'var(--muted-2)' }}> · has {CHIP_SHORT[p.chip]}</span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <button className="pill-btn" onClick={undo} disabled={!canUndo} title="Undo last change (Ctrl+Z)">
        ↶ Undo
      </button>
      <div className="stats">
        <div className="stat"><span className="k">GW pts</span>
          <span className="v">{fmt1(evs[gwIdx])}</span>
          {deltas && <Delta v={deltas[gwIdx]} />}</div>
        <div className="stat"><span className="k">Total</span>
          <span className="v">{fmt1(total)}</span>
          <Delta v={dTotal} /></div>
        <div className="stat"><span className="k">ITB</span>
          <span className="v">{money(plan?.bank)}</span></div>
        <div className="stat"><span className="k">Moves</span>
          <span className="v" style={{ color: plan?.hits ? 'var(--red)' : undefined }}>
            {nMoves}{plan?.hits ? ` (-${plan.hits * 4})` : ''}
          </span></div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */

function PitchView({ plan, sel, setSel, setStatPid, posOf, updateDraft, gwIdx, setToast,
                     armed, setArmed, applyTransfer }) {
  const { proj } = useStore()
  const rows = formationRows(plan.xi, posOf)
  const bench = plan.squad
    .filter((s) => !plan.xi.includes(s.id))
    .sort((a, b) => (posOf(a.id) === 'GK' ? -1 : posOf(b.id) === 'GK' ? 1 : 0))

  const trySwap = (a, b) => {
    const inXiA = plan.xi.includes(a)
    const inXiB = plan.xi.includes(b)
    if (inXiA === inXiB) return false
    const [xiP, benchP] = inXiA ? [a, b] : [b, a]
    const nextXi = plan.xi.map((id) => (id === xiP ? benchP : id))
    if (!xiLegal(nextXi, posOf)) {
      setToast({ kind: 'err', msg: 'Illegal formation — pick a different swap.' })
      return false
    }
    updateDraft((d) => {
      const p = d.gws[gwIdx]
      p.xi = nextXi
      if (p.captain === xiP) p.captain = benchP
      if (p.vice === xiP) p.vice = benchP
      return d
    })
    return true
  }

  const onCardClick = (pid, e) => {
    e.stopPropagation()
    if (armed) {
      if (applyTransfer(pid, armed)) setArmed(null)
      return
    }
    if (sel?.mode === 'swap') {
      if (sel.pid !== pid && trySwap(sel.pid, pid)) setSel(null)
      return
    }
    setStatPid(pid)
  }

  // when a player is armed, show what swapping him in would do this gw
  const armedDelta = (pid) => (armed && posOf(pid) === armed.position
    ? epOf(proj, armed.id, plan.gw) - epOf(proj, pid, plan.gw) : null)

  return (
    <div className="panel pitch-wrap" onClick={() => { setSel(null) }}>
      {armed && (
        <div className="armed-banner">
          Bringing in <b>{armed.web_name}</b> ({armed.position}, {money(armed.price)}) — click the
          {' '}{armed.position} to replace. Green/red = projected change this GW.
          <button className="pill-btn" style={{ marginLeft: 10 }} onClick={() => setArmed(null)}>cancel</button>
        </div>
      )}
      <div className="pitch">
        {['GK', 'DEF', 'MID', 'FWD'].map((pp) => (
          <div className="pitch-row" key={pp}>
            {rows[pp].map((pid) => (
              <Card key={pid} pid={pid} plan={plan} sel={sel}
                onClick={onCardClick} posOf={posOf}
                dim={armed && posOf(pid) !== armed.position} delta={armedDelta(pid)} />
            ))}
          </div>
        ))}
      </div>
      <div className="bench">
        {bench.map((s) => (
          <Card key={s.id} pid={s.id} plan={plan} sel={sel}
            onClick={onCardClick} posOf={posOf} bench
            dim={armed && posOf(s.id) !== armed.position} delta={armedDelta(s.id)} />
        ))}
      </div>
      {sel?.mode === 'swap' && (
        <p style={{ textAlign: 'center', color: 'var(--gold)', marginTop: 10, fontSize: 12.5 }}>
          Swap mode — click the player to exchange with, or click the pitch to cancel.
        </p>
      )}
    </div>
  )
}

function Card({ pid, plan, sel, onClick, posOf, dim, delta }) {
  const { byId, teams, proj } = useStore()
  const fixOf = useFixtureLookup()
  const p = byId.get(pid)
  const team = teams[String(p?.team_id)]
  const ep = epOf(proj, pid, plan.gw)
  const fixes = fixOf(p?.team_id, plan.gw)
  const isCap = plan.captain === pid
  const isVice = plan.vice === pid
  const isNew = plan.transfers_in.length < 15 && plan.transfers_in.includes(pid)
  const selected = sel?.pid === pid

  return (
    <div className={`pcard ${selected ? 'selected' : ''} ${dim ? 'dim' : ''}`}
      onClick={(e) => onClick(pid, e)}>
      {isCap && <span className="armband">{plan.chip === 'triple_captain' ? 'T' : 'C'}</span>}
      {isVice && <span className="armband vice">V</span>}
      <Flag p={p} />
      {delta != null && (
        <span className={`swapdelta ${delta >= 0 ? 'up' : 'down'}`}>
          {delta >= 0 ? '+' : ''}{fmt1(delta)}
        </span>
      )}
      <img className="shirt" alt=""
        src={shirtUrl(team?.code, posOf(pid) === 'GK')}
        onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
      <div className="pname" style={isNew ? { color: 'var(--green)' } : undefined}>
        {p?.web_name || pid}
      </div>
      <div className="pmeta">
        <span className="ep">{fmt1(ep)}</span>
        <span className="fix">
          {fixes.length ? fixes.map((f) => f.oppShort).join(',').toLowerCase() : '–'}
        </span>
        <span className="pr">{p ? p.price.toFixed(1) : ''}</span>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */

// Manual moves vs the draft's baseline, with the model's verdict per move.
function ChangesStrip({ moves, plan, draft, gwIdx, byId, proj, undoTransfer }) {
  if (!moves.length) return null
  const later = draft.gws.slice(gwIdx).map((g) => g.gw)
  return (
    <div className="panel changes">
      <div className="panel-head">Your changes this GW — model verdict</div>
      {moves.map((m) => {
        const dNow = epOf(proj, m.inId, plan.gw) - epOf(proj, m.outId, plan.gw)
        const dHor = later.reduce((a, g) => a + epOf(proj, m.inId, g) - epOf(proj, m.outId, g), 0)
        return (
          <div key={`${m.outId}-${m.inId}`} className="change-row">
            <span className="out">{byId.get(m.outId)?.web_name || m.outId}</span>
            <span className="arrow">→</span>
            <span className="in">{byId.get(m.inId)?.web_name || m.inId}</span>
            <span className={`dv ${dNow >= 0 ? 'up' : 'down'}`} title="this gameweek">
              {dNow >= 0 ? '+' : ''}{fmt1(dNow)} GW
            </span>
            <span className={`dv ${dHor >= 0 ? 'up' : 'down'}`} title={`GW${later[0]}–${later[later.length - 1]}`}>
              {dHor >= 0 ? '+' : ''}{fmt1(dHor)} horizon
            </span>
            <button className="pill-btn" onClick={() => undoTransfer(m.inId)}>↶ undo</button>
          </div>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------------ */

// Search any player and arm him; then click the squad player to replace.
function AddPlayerPanel({ plan, players, byId, proj, posOf, armed, setArmed }) {
  const [q, setQ] = useState('')
  const [pos, setPos] = useState('ALL')
  const owned = new Set((plan?.squad || []).map((s) => s.id))
  const gw = plan?.gw
  const opts = useMemo(() => {
    if (!gw) return []
    const lq = q.toLowerCase()
    return players
      .filter((p) => !owned.has(p.id) && (pos === 'ALL' || p.position === pos))
      .filter((p) => !lq || p.web_name.toLowerCase().includes(lq) || p.name.toLowerCase().includes(lq))
      .map((p) => ({ ...p, ep: epOf(proj, p.id, gw) }))
      .sort((a, b) => b.ep - a.ep)
      .slice(0, 30)
  }, [players, q, pos, gw, proj, plan])
  if (!plan) return null
  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head">
        Add player
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          {['ALL', 'GK', 'DEF', 'MID', 'FWD'].map((v) => (
            <button key={v} className={`mode-pill ${pos === v ? 'on' : ''}`} onClick={() => setPos(v)}>{v}</button>
          ))}
        </span>
      </div>
      <div style={{ padding: '10px 12px 4px' }}>
        <div className="search" style={{ minWidth: 0 }}>
          🔍<input placeholder="Search players…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <div className="addlist">
        {opts.map((p) => (
          <div key={p.id} className={`ta-item ${armed?.id === p.id ? 'armed' : ''}`}
            onClick={() => setArmed(armed?.id === p.id ? null : p)}>
            <div>
              <div style={{ fontWeight: 700 }}>{p.web_name}<Flag p={p} /></div>
              <div className="sub">{p.position} · own {p.own}%</div>
            </div>
            <span className="chip blue num">{fmt1(p.ep)}</span>
            <span className="pr num">{money(p.price)}</span>
          </div>
        ))}
        {!opts.length && <div className="ml-note">No players match.</div>}
      </div>
      <div style={{ padding: '6px 12px 10px', fontSize: 11, color: 'var(--muted-2)' }}>
        Click a player to arm him, then click who he replaces on the pitch.
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */

// Heuristic chip hints from this draft's own projections. The Solver is the
// authority (it evaluates chips exactly); these flag where a chip looks valuable.
function ChipAdvisor({ draft, proj, byId, players, posOf, updateDraft }) {
  const stats = useMemo(() => draft.gws.map((p) => {
    const xiEp = p.xi.reduce((a, id) => a + epOf(proj, id, p.gw), 0)
    const cap = p.xi.reduce((best, id) => {
      const e = epOf(proj, id, p.gw); return e > best.ep ? { id, ep: e } : best
    }, { id: null, ep: 0 })
    const benchEp = p.squad.filter((s) => !p.xi.includes(s.id))
      .reduce((a, s) => a + epOf(proj, s.id, p.gw), 0)
    // best XI available from the whole pool (ignoring budget) for this gw
    const pool = players.map((x) => x.id)
    const best = bestXI(pool, posOf, (id) => epOf(proj, id, p.gw))
    const bestEp = best.reduce((a, id) => a + epOf(proj, id, p.gw), 0)
    return { gw: p.gw, chip: p.chip, xiEp, cap, benchEp, bestEp, gap: bestEp - xiEp }
  }), [draft, proj, players])

  const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : 0 }
  const hints = []
  if (stats.length) {
    const tc = stats.reduce((b, s) => (s.cap.ep > b.cap.ep ? s : b), stats[0])
    const tcEdge = tc.cap.ep - median(stats.map((s) => s.cap.ep))
    hints.push({ chip: 'triple_captain', gw: tc.gw, score: tcEdge,
      strong: tcEdge >= 1.5, gain: tc.cap.ep,
      text: `${byId.get(tc.cap.id)?.web_name || '?'} ${fmt1(tc.cap.ep)} pts — ${tcEdge >= 0 ? '+' : ''}${fmt1(tcEdge)} vs your typical captain` })
    const bb = stats.reduce((b, s) => (s.benchEp > b.benchEp ? s : b), stats[0])
    hints.push({ chip: 'bench_boost', gw: bb.gw, score: bb.benchEp, strong: bb.benchEp >= 8,
      gain: bb.benchEp, text: `bench projects ${fmt1(bb.benchEp)} pts` })
    const fh = stats.reduce((b, s) => (s.gap > b.gap ? s : b), stats[0])
    hints.push({ chip: 'freehit', gw: fh.gw, score: fh.gap, strong: fh.gap >= 12,
      gain: fh.gap, text: `your XI ${fmt1(fh.xiEp)} vs ${fmt1(fh.bestEp)} best available (${fh.gap >= 0 ? '+' : ''}${fmt1(fh.gap)})` })
    const first = stats[0].gap
    const worse = stats.find((s) => s.gap - first >= 4)
    if (worse) {
      hints.push({ chip: 'wildcard', gw: worse.gw, score: worse.gap - first, strong: worse.gap - first >= 8,
        gain: worse.gap - first, text: `gap to the best XI grows by ${fmt1(worse.gap - first)} from GW${worse.gw}` })
    }
  }
  const apply = (chip, gw) => updateDraft((d) => {
    for (const p of d.gws) if (p.chip === chip) p.chip = null
    const t = d.gws.find((p) => p.gw === gw)
    if (t) t.chip = chip
    return d
  })
  const active = new Set(draft.gws.filter((p) => p.chip).map((p) => `${p.chip}@${p.gw}`))
  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head">Chip advisor
        <span style={{ marginLeft: 'auto', fontSize: 10, letterSpacing: 0, textTransform: 'none', color: 'var(--muted-2)' }}>
          hints from this draft · Solver decides exactly
        </span>
      </div>
      {hints.map((h) => (
        <div key={h.chip} className={`advice ${h.strong ? 'strong' : ''}`}>
          <span className="chip gold">{CHIP_SHORT[h.chip]}</span>
          <span className="num" style={{ fontWeight: 800 }}>GW{h.gw}</span>
          <span className="txt">{h.text}</span>
          {active.has(`${h.chip}@${h.gw}`)
            ? <span className="chip green">set</span>
            : <button className="pill-btn" onClick={() => apply(h.chip, h.gw)}>apply</button>}
        </div>
      ))}
      {!hints.length && <div className="ml-note">No projections yet.</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function TransferModal({ draft, gwIdx, outId, byId, proj, posOf, close, applyTransfer }) {
  const { players } = useStore()
  const [q, setQ] = useState('')
  const plan = draft.gws[gwIdx]
  const out = plan.squad.find((s) => s.id === outId)
  const budget = (plan.bank || 0) + (out?.sell || 0)
  const pos = posOf(outId)
  const owned = new Set(plan.squad.map((s) => s.id))

  const opts = useMemo(() => players
    .filter((p) => p.position === pos && !owned.has(p.id))
    .filter((p) => !q || p.web_name.toLowerCase().includes(q.toLowerCase()) ||
      p.name.toLowerCase().includes(q.toLowerCase()))
    .map((p) => ({ ...p, ep: epOf(proj, p.id, plan.gw), afford: p.price <= budget + 1e-9 }))
    .sort((a, b) => b.ep - a.ep)
    .slice(0, 40), [players, q, pos, plan.gw, proj, budget, owned])

  const outEp = epOf(proj, outId, plan.gw)
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(10,10,22,0.6)', zIndex: 90 }}
      onClick={close}>
      <div className="panel" onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 520, margin: '8vh auto', boxShadow: 'var(--shadow)' }}>
        <div className="panel-head">
          Transfer out {byId.get(outId)?.web_name} ({fmt1(outEp)}) · budget {money(budget)}
          <button style={{ marginLeft: 'auto', color: 'var(--muted)' }} onClick={close}>✕</button>
        </div>
        <div style={{ padding: 12 }}>
          <div className="search" style={{ marginBottom: 10 }}>
            🔍<input autoFocus placeholder={`Search ${pos}s…`} value={q}
              onChange={(e) => setQ(e.target.value)} />
          </div>
          <div style={{ maxHeight: 380, overflow: 'auto' }}>
            {opts.map((p) => {
              const d = p.ep - outEp
              return (
                <div key={p.id} className="ta-item" style={{ opacity: p.afford ? 1 : 0.4 }}
                  onClick={() => { if (applyTransfer(outId, p)) close() }}>
                  <div>
                    <div style={{ fontWeight: 700 }}>{p.web_name}<Flag p={p} /></div>
                    <div className="sub">{POS_ORDER[p.position] != null ? p.position : ''} · own {p.own}%</div>
                  </div>
                  <span className={`dv ${d >= 0 ? 'up' : 'down'}`}>{d >= 0 ? '+' : ''}{fmt1(d)}</span>
                  <span className="chip blue num">{fmt1(p.ep)}</span>
                  <span className="pr num">{money(p.price)}</span>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */

function DraftsPanel({ drafts, setDrafts, proj, activeDraftId, setActiveDraftId,
                       gwIdx, setGwIdx, createFromEntry, entry }) {
  const rename = (d) => {
    const name = prompt('Draft name', d.label)
    if (name) setDrafts((ds) => ds.map((x) => (x.id === d.id ? { ...x, label: name } : x)))
  }
  const remove = (d) => {
    if (!confirm(`Delete ${d.label}?`)) return
    setDrafts((ds) => ds.filter((x) => x.id !== d.id))
    if (activeDraftId === d.id) setActiveDraftId(drafts.find((x) => x.id !== d.id)?.id || null)
  }
  const duplicate = (d) => {
    const copy = structuredClone(d)
    copy.id = `d${Date.now()}`
    copy.label = `${d.label} [copy]`
    setDrafts((ds) => [...ds, copy])
  }

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head">
        Drafts
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {entry?.squad && (
            <button className="pill-btn" onClick={createFromEntry}>+ from squad</button>
          )}
        </span>
      </div>
      {drafts.map((d) => {
        const evs = d.gws.map((p) => gwEV(p, proj))
        const tot = evs.reduce((a, b) => a + b, 0)
        const dl = baselineDeltas(d, proj)
        return (
          <div key={d.id} className={`draft-row ${d.id === activeDraftId ? 'active' : ''}`}>
            <div className="draft-name" onClick={() => setActiveDraftId(d.id)}
              onDoubleClick={() => rename(d)} title="click to select · double-click to rename">
              {d.label}
              <div className="sub">{d.source === 'solver' ? `obj ${fmt1(d.objective)}` : d.source}</div>
            </div>
            <div className="draft-cells">
              {d.gws.map((p, i) => (
                <div key={p.gw}
                  className={`draft-cell ${d.id === activeDraftId && i === gwIdx ? 'cur' : ''}`}
                  onClick={() => { setActiveDraftId(d.id); setGwIdx(i) }}>
                  <div className="ev">{fmt1(evs[i])}
                    {dl && Math.abs(dl[i]) >= 0.05 && (
                      <span className={`dv ${dl[i] > 0 ? 'up' : 'down'}`} style={{ fontSize: 9 }}>
                        {dl[i] > 0 ? '+' : ''}{fmt1(dl[i])}
                      </span>
                    )}
                  </div>
                  <div className="mv">
                    {p.chip ? <span className="chipflag">{CHIP_SHORT[p.chip]} </span> : null}
                    {p.transfers_in.length === 15 ? 'build'
                      : p.transfers_in.length
                        ? `${p.free_used ?? p.transfers_in.length}/${p.transfers_in.length}${p.hits ? ` -${p.hits * 4}` : ''}`
                        : '—'}
                  </div>
                </div>
              ))}
            </div>
            <div className="draft-total">
              <div className="t">{fmt1(tot)}</div>
              <div className="s">{d.gws.length} gws</div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <button title="duplicate" style={{ color: 'var(--muted-2)', fontSize: 12 }}
                onClick={() => duplicate(d)}>⧉</button>
              <button title="delete" style={{ color: 'var(--muted-2)', fontSize: 12 }}
                onClick={() => remove(d)}>✕</button>
            </div>
          </div>
        )
      })}
      <div style={{ display: 'flex', alignItems: 'center', padding: '9px 12px' }}>
        <span style={{ fontSize: 11, color: 'var(--muted-2)' }}>autosave on</span>
        <button className="pill-btn" style={{ marginLeft: 'auto' }}
          onClick={() => { if (confirm('Delete ALL drafts?')) setDrafts([]) }}>
          Reset all
        </button>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */

function PathsPanel({ draft, byId }) {
  const nm = (pid) => byId.get(pid)?.web_name || pid
  return (
    <div className="panel">
      <div className="panel-head">↗ Expand paths — {draft.label}</div>
      {draft.gws.map((p, i) => (
        <div className="path-gw" key={p.gw}>
          <div className="path-step">{i + 1}</div>
          <div className="path-moves">
            {p.chip && <div className="path-chipbanner">{CHIP_LONG[p.chip]}</div>}
            {p.transfers_out.length === 0 && !p.chip && (
              <span style={{ color: 'var(--muted-2)', fontSize: 12 }}>roll — no moves</span>
            )}
            {p.transfers_out.map((o, k) => (
              <div className="path-move" key={o}>
                <span className="out">{nm(o)}</span>
                <span className="arrow">→</span>
                <span className="in">{nm(p.transfers_in[k])}</span>
              </div>
            ))}
          </div>
          <div className="path-side">
            <span>£{fmt1(p.bank)}m itb</span>
            <span>{p.free_after ?? '–'} ft{p.hits ? ` · -${p.hits * 4}` : ''}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
