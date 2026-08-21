import React, { useState } from 'react'
import { useStore } from './store'
import Planner from './tabs/Planner'
import Projections from './tabs/Projections'
import Fixtures from './tabs/Fixtures'
import MiniLeague from './tabs/MiniLeague'
import Solver from './tabs/Solver'
import MyTeamModal from './components/MyTeamModal'
import { API_VERSION, api, pollJob } from './api'

const TABS = ['Planner', 'Projections', 'Fixtures', 'Mini League', 'Solver']

export default function App() {
  const [tab, setTab] = useState('Planner')
  const { status, setStatus, entryId, setEntryId, entry, toast, setToast,
          refreshProjections } = useStore()
  const [pulling, setPulling] = useState(false)
  const [teamModal, setTeamModal] = useState(false)

  const doPull = async () => {
    if (pulling) return
    setPulling(true)
    setToast({ kind: 'info', msg: 'Refreshing FPL data…' })
    try {
      const { job_id } = await api.pull()
      await pollJob(job_id, (j) => {
        const last = j.progress[j.progress.length - 1]
        if (last) setToast({ kind: 'info', msg: last.msg })
      })
      setToast({ kind: 'ok', msg: 'Data refreshed. Projections cache cleared — rebuild from the Solver or Projections tab.' })
      api.status().then(setStatus)
      refreshProjections()
    } catch (e) {
      setToast({ kind: 'err', msg: `Pull failed: ${e.message}` })
    } finally {
      setPulling(false)
    }
  }

  const stale = status && status.api_version !== API_VERSION

  return (
    <>
      {stale && (
        <div className="stale-banner">
          ⚠ The backend is running an older build than this page (API {status.api_version || 'unknown'}
          vs {API_VERSION}). Stop and restart <b>python -m app</b>, then reload — otherwise new
          features will fail with 404/405 errors.
        </div>
      )}
      <header className="topnav">
        <div className="brand">
          <div className="brand-mark">⚽</div>
          OpenFPL <span style={{ color: 'var(--muted-2)', fontWeight: 600 }}>planner</span>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t} className={`tab ${tab === t ? 'active' : ''}`}
              onClick={() => setTab(t)}>{t}</button>
          ))}
        </nav>
        <div className="right">
          <button className={`pill-btn ${entry?.squad ? '' : 'accent'}`}
            title="import or enter your current 15" onClick={() => setTeamModal(true)}>
            {entry?.squad ? '✓ squad set' : '⚠ set my team'}
          </button>
          <EntryBox entryId={entryId} setEntryId={setEntryId} entry={entry} />
          <button className="pill-btn" onClick={doPull} disabled={pulling}>
            {pulling ? <span className="spinner" /> : '⟳'} Data
          </button>
          {status && (
            <span className="chip dim num" title="next gameweek">
              GW{status.next_gw}
            </span>
          )}
        </div>
      </header>

      <main className="page">
        {tab === 'Planner' && <Planner />}
        {tab === 'Projections' && <Projections />}
        {tab === 'Fixtures' && <Fixtures />}
        {tab === 'Mini League' && <MiniLeague />}
        {tab === 'Solver' && <Solver goPlanner={() => setTab('Planner')} />}
      </main>

      {teamModal && <MyTeamModal close={() => setTeamModal(false)} />}

      {toast && (
        <div className="toast">
          {toast.kind === 'ok' && <span className="ok">✓ SUCCESS:</span>}
          {toast.kind === 'err' && <span style={{ color: 'var(--red)', fontWeight: 800 }}>✕ ERROR:</span>}
          {toast.kind === 'info' && <span className="spinner" />}
          <span>{toast.msg}</span>
          <button className="close" onClick={() => setToast(null)}>×</button>
        </div>
      )}
    </>
  )
}

function EntryBox({ entryId, setEntryId, entry }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState('')
  if (editing) {
    return (
      <form onSubmit={(e) => {
        e.preventDefault()
        const n = parseInt(val, 10)
        if (n > 0) setEntryId(n)
        setEditing(false)
      }}>
        <input autoFocus className="num" value={val}
          onChange={(e) => setVal(e.target.value.replace(/\D/g, ''))}
          onBlur={() => setEditing(false)}
          placeholder="entry id"
          style={{ width: 110, background: 'var(--bg-deep)', border: '1px solid var(--line)',
                   borderRadius: 7, padding: '7px 10px', outline: 'none', fontSize: 12 }} />
      </form>
    )
  }
  return (
    <button className="pill-btn" title="change FPL entry id"
      onClick={() => { setVal(String(entryId || '')); setEditing(true) }}>
      👤 {entry?.team_name || (entryId ? `#${entryId}` : 'set entry')}
    </button>
  )
}
