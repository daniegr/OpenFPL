import React, { useMemo, useState } from 'react'
import { useFixtureLookup, useStore } from '../store'
import {
  CHIP_LONG, CHIP_SHORT, POS_ORDER, bestXI, draftTotalEV, epOf, formationRows,
  fmt1, gwEV, money, shirtUrl, xiLegal,
} from '../util'

export default function Planner() {
  const { drafts, setDrafts, activeDraftId, setActiveDraftId, proj, byId,
          entry, status, setToast } = useStore()
  const draft = drafts.find((d) => d.id === activeDraftId) || drafts[0] || null
  const [gwIdx, setGwIdx] = useState(0)
  const [sel, setSel] = useState(null)          // {pid, mode: 'menu'|'swap'}
  const [xfer, setXfer] = useState(null)        // pid being transferred out

  const plan = draft?.gws?.[Math.min(gwIdx, (draft?.gws?.length || 1) - 1)] || null
  const posOf = (pid) => byId.get(pid)?.position || 'MID'

  const updateDraft = (fn) => {
    setDrafts((ds) => ds.map((d) => (d.id === draft.id ? fn(structuredClone(d)) : d)))
  }

  const createFromEntry = () => {
    if (!entry?.squad) return
    const horizon = (status?.scheduled_gws || []).filter((g) => g >= status.next_gw).slice(0, 4)
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
    const d = { id: `d${Date.now()}`, label: `Draft ${label}`, source: 'entry', gws }
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

  const evs = draft.gws.map((p) => gwEV(p, proj))
  const isBuild = plan?.transfers_in.length === 15   // pre-season squad build
  const nMoves = plan && !isBuild ? plan.transfers_in.length : 0

  return (
    <div>
      <GwBar draft={draft} gwIdx={gwIdx} setGwIdx={setGwIdx} evs={evs} plan={plan} nMoves={nMoves} />
      <div className="planner-grid">
        <div>
          {plan && (
            <PitchView plan={plan} draft={draft} sel={sel} setSel={setSel}
              setXfer={setXfer} posOf={posOf} updateDraft={updateDraft}
              gwIdx={gwIdx} setToast={setToast} />
          )}
        </div>
        <div>
          <DraftsPanel drafts={drafts} setDrafts={setDrafts} proj={proj}
            activeDraftId={draft.id} setActiveDraftId={setActiveDraftId}
            gwIdx={gwIdx} setGwIdx={setGwIdx} createFromEntry={createFromEntry}
            entry={entry} />
          <PathsPanel draft={draft} byId={byId} />
        </div>
      </div>
      {xfer && plan && (
        <TransferModal draft={draft} gwIdx={gwIdx} outId={xfer} byId={byId}
          proj={proj} posOf={posOf} close={() => setXfer(null)}
          updateDraft={updateDraft} setToast={setToast} />
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function GwBar({ draft, gwIdx, setGwIdx, evs, plan, nMoves }) {
  const total = evs.reduce((a, b) => a + b, 0)
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
      {plan?.chip && <span className="chip gold">{CHIP_SHORT[plan.chip]}</span>}
      <div className="stats">
        <div className="stat"><span className="k">GW pts</span>
          <span className="v">{fmt1(evs[gwIdx])}</span></div>
        <div className="stat"><span className="k">Total</span>
          <span className="v">{fmt1(total)}</span></div>
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

function PitchView({ plan, sel, setSel, setXfer, posOf, updateDraft, gwIdx, setToast }) {
  const rows = formationRows(plan.xi, posOf)
  const bench = plan.squad
    .filter((s) => !plan.xi.includes(s.id))
    .sort((a, b) => (posOf(a.id) === 'GK' ? -1 : posOf(b.id) === 'GK' ? 1 : 0))

  const trySwap = (a, b) => {
    // a is in XI xor b is in XI -> swap them if legal
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
    if (sel?.mode === 'swap' && sel.pid !== pid) {
      if (trySwap(sel.pid, pid)) setSel(null)
      return
    }
    setSel(sel?.pid === pid && sel?.mode === 'menu' ? null : { pid, mode: 'menu' })
  }

  return (
    <div className="panel pitch-wrap" onClick={() => setSel(null)}>
      <div className="pitch">
        {['GK', 'DEF', 'MID', 'FWD'].map((pp) => (
          <div className="pitch-row" key={pp}>
            {rows[pp].map((pid) => (
              <Card key={pid} pid={pid} plan={plan} sel={sel} setSel={setSel}
                setXfer={setXfer} onClick={onCardClick} posOf={posOf}
                updateDraft={updateDraft} gwIdx={gwIdx} />
            ))}
          </div>
        ))}
      </div>
      <div className="bench">
        {bench.map((s) => (
          <Card key={s.id} pid={s.id} plan={plan} sel={sel} setSel={setSel}
            setXfer={setXfer} onClick={onCardClick} posOf={posOf}
            updateDraft={updateDraft} gwIdx={gwIdx} bench />
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

function Card({ pid, plan, sel, setSel, setXfer, onClick, posOf, updateDraft, gwIdx, bench }) {
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
    <div className={`pcard ${selected ? 'selected' : ''}`} onClick={(e) => onClick(pid, e)}>
      {isCap && <span className="armband">{plan.chip === 'triple_captain' ? 'T' : 'C'}</span>}
      {isVice && <span className="armband vice">V</span>}
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
      {selected && sel.mode === 'menu' && (
        <div className="pmenu" style={{ top: '100%', left: '50%', transform: 'translateX(-50%)' }}
          onClick={(e) => e.stopPropagation()}>
          {plan.xi.includes(pid) && !isCap && (
            <button onClick={() => { updateDraft((d) => { const g = d.gws[gwIdx]; if (g.vice === pid) g.vice = g.captain; g.captain = pid; return d }); setSel(null) }}>
              ⓒ Make captain
            </button>
          )}
          {plan.xi.includes(pid) && !isVice && (
            <button onClick={() => { updateDraft((d) => { const g = d.gws[gwIdx]; if (g.captain === pid) return d; g.vice = pid; return d }); setSel(null) }}>
              ⓥ Make vice
            </button>
          )}
          <button onClick={() => setSel({ pid, mode: 'swap' })}>
            ⇄ Swap {bench ? 'into XI' : 'to bench'}
          </button>
          <button className="danger" onClick={() => { setXfer(pid); setSel(null) }}>
            → Transfer out…
          </button>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function TransferModal({ draft, gwIdx, outId, byId, proj, posOf, close, updateDraft, setToast }) {
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

  const apply = (inP) => {
    if (!inP.afford) {
      setToast({ kind: 'err', msg: `Can't afford ${inP.web_name} (${money(inP.price)} > ${money(budget)} available).` })
      return
    }
    updateDraft((d) => {
      const delta = (out?.sell || 0) - inP.price
      for (let t = gwIdx; t < d.gws.length; t++) {
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
        }
      }
      return d
    })
    close()
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(10,10,22,0.6)', zIndex: 90 }}
      onClick={close}>
      <div className="panel" onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 520, margin: '8vh auto', boxShadow: 'var(--shadow)' }}>
        <div className="panel-head">
          Transfer out {byId.get(outId)?.web_name} · budget {money(budget)}
          <button style={{ marginLeft: 'auto', color: 'var(--muted)' }} onClick={close}>✕</button>
        </div>
        <div style={{ padding: 12 }}>
          <div className="search" style={{ marginBottom: 10 }}>
            🔍<input autoFocus placeholder={`Search ${pos}s…`} value={q}
              onChange={(e) => setQ(e.target.value)} />
          </div>
          <div style={{ maxHeight: 380, overflow: 'auto' }}>
            {opts.map((p) => (
              <div key={p.id} className="ta-item" style={{ opacity: p.afford ? 1 : 0.4 }}
                onClick={() => apply(p)}>
                <div>
                  <div style={{ fontWeight: 700 }}>{p.web_name}</div>
                  <div className="sub">{POS_ORDER[p.position] != null ? p.position : ''} · own {p.own}%</div>
                </div>
                <span className="chip blue num">{fmt1(p.ep)}</span>
                <span className="pr num">{money(p.price)}</span>
              </div>
            ))}
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
                  <div className="ev">{fmt1(evs[i])}</div>
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
