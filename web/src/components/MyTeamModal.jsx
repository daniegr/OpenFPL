import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { money } from '../util'

const SLOTS = [['GK', 2], ['DEF', 5], ['MID', 5], ['FWD', 3]]

// Runs inside the logged-in fantasy.premierleague.com tab: fetches the private
// my-team JSON with the browser's own session and copies it to the clipboard.
// No cookie ever leaves the browser or reaches this app.
const BOOKMARKLET = "javascript:(async()=>{try{" +
  "if(location.hostname!=='fantasy.premierleague.com'){alert('OpenFPL: open fantasy.premierleague.com (logged in) first, then click this bookmark.');return}" +
  "const me=await(await fetch('/api/me/',{credentials:'same-origin'})).json();" +
  "const e=me&&me.player&&me.player.entry;" +
  "if(!e){alert('OpenFPL: log in to fantasy.premierleague.com first, then click the bookmark again.');return}" +
  "const t=await(await fetch('/api/my-team/'+e+'/',{credentials:'same-origin'})).json();" +
  "const s=JSON.stringify({entry:e,my_team:t});" +
  "try{await navigator.clipboard.writeText(s);alert('OpenFPL: squad copied to clipboard - paste it into the planner (set my team > Bookmarklet).')}" +
  "catch(x){prompt('OpenFPL: copy this and paste it into the planner:',s)}" +
  "}catch(x){alert('OpenFPL bookmark failed: '+x)}})();"

export default function MyTeamModal({ close }) {
  const { entryId, setEntryId, entry, players, teams, byId, refreshEntry, setToast } = useStore()
  const [mode, setMode] = useState('bookmark')
  const [cookie, setCookie] = useState('')
  const [pasted, setPasted] = useState('')
  const bmRef = useRef(null)
  // React refuses javascript: hrefs in JSX; set the attribute directly so the
  // link can be dragged to the bookmarks bar
  useEffect(() => { bmRef.current?.setAttribute('href', BOOKMARKLET) }, [mode])
  const [busy, setBusy] = useState(false)
  // manual state: {GK: [ids], DEF: [...], ...}
  const [picks, setPicks] = useState(() => {
    const init = { GK: [], DEF: [], MID: [], FWD: [] }
    for (const p of entry?.squad || []) {
      const s = byId.get(p.element)
      if (s && init[s.position]) init[s.position].push(p.element)
    }
    return init
  })
  const [bank, setBank] = useState(entry?.bank ?? '')
  const [fts, setFts] = useState(entry?.free_transfers ?? 1)

  const allIds = Object.values(picks).flat()
  const cost = allIds.reduce((a, id) => a + (byId.get(id)?.price || 0), 0)
  const clubCounts = allIds.reduce((m, id) => {
    const t = byId.get(id)?.team_id
    m[t] = (m[t] || 0) + 1
    return m
  }, {})
  const clubViolation = Object.entries(clubCounts).find(([, n]) => n > 3)
  const complete = SLOTS.every(([pos, n]) => picks[pos].length === n)

  const doImport = async () => {
    setBusy(true)
    try {
      await api.importMyTeam(entryId, cookie)
      refreshEntry()
      setToast({ kind: 'ok', msg: 'Squad imported from your FPL account.' })
      close()
    } catch (e) {
      setToast({ kind: 'err', msg: `Import failed — check the cookie is fresh and complete. (${e.message})` })
    } finally { setBusy(false) }
  }

  const doPaste = async () => {
    setBusy(true)
    try {
      const doc = await api.pasteMyTeam(entryId, pasted.trim())
      if (doc?.entry_id && doc.entry_id !== entryId) setEntryId(doc.entry_id)
      refreshEntry()
      setToast({ kind: 'ok', msg: 'Squad imported via the bookmarklet — planner and solver now start from it.' })
      close()
    } catch (e) {
      setToast({ kind: 'err', msg: `Import failed: ${e.message}` })
    } finally { setBusy(false) }
  }

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(BOOKMARKLET)
      setToast({ kind: 'ok', msg: 'Bookmarklet code copied — create a bookmark and paste it as the URL.' })
    } catch {
      prompt('Copy this as the bookmark URL:', BOOKMARKLET)
    }
  }

  const doSave = async () => {
    setBusy(true)
    try {
      await api.saveMyTeam({
        entry_id: entryId,
        squad: allIds.map((id) => ({
          element: id,
          selling_price: byId.get(id)?.price || 0,
          purchase_price: byId.get(id)?.price || 0,
          is_captain: false, is_vice: false, multiplier: 1,
        })),
        bank: bank === '' ? Math.max(0, 100 - cost) : Number(bank),
        free_transfers: Number(fts) || 1,
        source: 'manual',
      })
      refreshEntry()
      setToast({ kind: 'ok', msg: 'Squad saved — the planner and solver now start from it.' })
      close()
    } catch (e) {
      setToast({ kind: 'err', msg: `Save failed: ${e.message}` })
    } finally { setBusy(false) }
  }

  const doClear = async () => {
    await api.clearMyTeam()
    refreshEntry()
    setToast({ kind: 'ok', msg: 'Saved squad cleared.' })
    close()
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(10,10,22,0.65)', zIndex: 95, overflow: 'auto' }}
      onClick={close}>
      <div className="panel" onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 700, margin: '6vh auto', boxShadow: 'var(--shadow)' }}>
        <div className="panel-head">
          My team — {entry?.team_name || `#${entryId}`}
          {entry?.squad_source && (
            <span className="chip dim">{entry.squad_source === 'public' ? 'from FPL (public)' : entry.squad_source}</span>
          )}
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            <button className={`pill-btn ${mode === 'bookmark' ? 'accent' : ''}`}
              onClick={() => setMode('bookmark')}>Bookmarklet</button>
            <button className={`pill-btn ${mode === 'import' ? 'accent' : ''}`}
              onClick={() => setMode('import')}>Cookie</button>
            <button className={`pill-btn ${mode === 'manual' ? 'accent' : ''}`}
              onClick={() => setMode('manual')}>Enter manually</button>
            <button style={{ color: 'var(--muted)', fontSize: 16, marginLeft: 4 }} onClick={close}>✕</button>
          </span>
        </div>

        {mode === 'bookmark' ? (
          <div style={{ padding: 18 }}>
            <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, marginBottom: 12 }}>
              The easiest way to load your private squad (works pre-deadline), the way
              FPL Review does it — <b>no cookies, nothing stored</b>:
            </p>
            <ol className="steps">
              <li>Drag this button onto your browser's bookmarks bar (or <b>copy the code</b> and
                create a bookmark with it as the URL):&nbsp;
                <a ref={bmRef} className="bookmarklet" onClick={(e) => e.preventDefault()}
                  title="drag me to the bookmarks bar">⚽ OpenFPL squad</a>
                &nbsp;<button className="pill-btn" onClick={copyCode}>⧉ copy code</button>
              </li>
              <li>Go to <b>fantasy.premierleague.com</b> (logged in) and click the bookmark — it
                copies your squad to the clipboard</li>
              <li>Come back and paste it below</li>
            </ol>
            <textarea value={pasted} onChange={(e) => setPasted(e.target.value)}
              placeholder='{"entry": 123456, "my_team": {"picks": [ … ] } }' rows={4} spellCheck={false}
              style={{ width: '100%', background: 'var(--bg-deep)', color: 'var(--text)',
                       border: '1px solid var(--line)', borderRadius: 8, padding: 10,
                       fontFamily: 'var(--mono)', fontSize: 11, resize: 'vertical' }} />
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <button className="pill-btn accent" disabled={!pasted.trim() || busy} onClick={doPaste}>
                {busy ? <span className="spinner" /> : '⇩'} Import squad
              </button>
              {entry?.squad && entry.squad_source !== 'public' && (
                <button className="pill-btn" onClick={doClear}>Clear saved squad</button>
              )}
            </div>
          </div>
        ) : mode === 'import' ? (
          <div style={{ padding: 18 }}>
            <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, marginBottom: 12 }}>
              Before a gameweek deadline, FPL keeps your picks <b>private</b> — the public
              API can't see them. To read your squad the way FPL Review does, paste your
              FPL session cookie (it is used once for this request and <b>never stored</b>):
            </p>
            <ol style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.8, margin: '0 0 14px 18px' }}>
              <li>Log in at <b>fantasy.premierleague.com</b> and open your team page</li>
              <li>Press <b>F12</b> → <b>Network</b> tab → refresh → click any <b>fantasy.premierleague.com</b> request</li>
              <li>Under <b>Request headers</b>, copy the whole <b>cookie:</b> value and paste it here</li>
            </ol>
            <textarea value={cookie} onChange={(e) => setCookie(e.target.value)}
              placeholder="pl_profile=…; datadome=…; …" rows={4} spellCheck={false}
              style={{ width: '100%', background: 'var(--bg-deep)', color: 'var(--text)',
                       border: '1px solid var(--line)', borderRadius: 8, padding: 10,
                       fontFamily: 'var(--mono)', fontSize: 11, resize: 'vertical' }} />
            <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
              <button className="pill-btn accent" disabled={!cookie.trim() || busy} onClick={doImport}>
                {busy ? <span className="spinner" /> : '⇩'} Import squad
              </button>
              {entry?.squad && entry.squad_source !== 'public' && (
                <button className="pill-btn" onClick={doClear}>Clear saved squad</button>
              )}
            </div>
          </div>
        ) : (
          <div style={{ padding: 18 }}>
            <p style={{ fontSize: 12.5, color: 'var(--muted)', marginBottom: 14 }}>
              Pick your 15. Prices are current — selling prices are assumed equal
              (fine pre-season; use the cookie import once the season is running).
            </p>
            {SLOTS.map(([pos, n]) => (
              <SlotRow key={pos} pos={pos} need={n} ids={picks[pos]}
                setIds={(ids) => setPicks((p) => ({ ...p, [pos]: ids }))}
                players={players} byId={byId} teams={teams} taken={allIds} />
            ))}
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
              <span className="num" style={{ fontSize: 13, color: cost > 100 && bank === '' ? 'var(--red)' : 'var(--muted)' }}>
                cost {money(cost)}
              </span>
              <label style={{ fontSize: 12, color: 'var(--muted)' }}>
                bank £<input className="num" value={bank} placeholder={(Math.max(0, 100 - cost)).toFixed(1)}
                  onChange={(e) => setBank(e.target.value)}
                  style={{ width: 56, background: 'var(--bg-deep)', border: '1px solid var(--line)',
                           borderRadius: 6, padding: '5px 7px', outline: 'none' }} />m
              </label>
              <label style={{ fontSize: 12, color: 'var(--muted)' }}>
                free transfers <input className="num" value={fts}
                  onChange={(e) => setFts(e.target.value.replace(/\D/g, ''))}
                  style={{ width: 40, background: 'var(--bg-deep)', border: '1px solid var(--line)',
                           borderRadius: 6, padding: '5px 7px', outline: 'none' }} />
              </label>
              {clubViolation && (
                <span style={{ color: 'var(--red)', fontSize: 12 }}>
                  more than 3 from {teams[clubViolation[0]]?.name}
                </span>
              )}
              <button className="pill-btn accent" style={{ marginLeft: 'auto' }}
                disabled={!complete || !!clubViolation || busy} onClick={doSave}>
                {busy ? <span className="spinner" /> : '✓'} Save squad
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function SlotRow({ pos, need, ids, setIds, players, byId, teams, taken }) {
  const [q, setQ] = useState('')
  const opts = useMemo(() => {
    if (!q) return []
    const lq = q.toLowerCase()
    return players
      .filter((p) => p.position === pos && !taken.includes(p.id))
      .filter((p) => p.web_name.toLowerCase().includes(lq) || p.name.toLowerCase().includes(lq))
      .sort((a, b) => b.own - a.own)
      .slice(0, 8)
  }, [q, players, pos, taken])

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span className="section-label" style={{ width: 34 }}>{pos}</span>
        <span className="chip dim num">{ids.length}/{need}</span>
        {ids.length < need && (
          <div className="typeahead" style={{ flex: 1 }}>
            <div className="search" style={{ minWidth: 0, padding: '5px 10px' }}>
              🔍<input placeholder={`Add ${pos}…`} value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            {opts.length > 0 && (
              <div className="ta-list">
                {opts.map((p) => (
                  <div key={p.id} className="ta-item"
                    onClick={() => { setIds([...ids, p.id]); setQ('') }}>
                    <div>
                      <div style={{ fontWeight: 700 }}>{p.web_name}</div>
                      <div className="sub">{teams[String(p.team_id)]?.short} · own {p.own}%</div>
                    </div>
                    <span className="pr num">£{p.price.toFixed(1)}m</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginLeft: 42 }}>
        {ids.map((id) => (
          <span className="tag" key={id}>
            {byId.get(id)?.web_name || id}
            <span className="num" style={{ color: 'var(--muted-2)', fontSize: 10 }}>
              £{(byId.get(id)?.price || 0).toFixed(1)}
            </span>
            <button onClick={() => setIds(ids.filter((x) => x !== id))}>×</button>
          </span>
        ))}
      </div>
    </div>
  )
}
