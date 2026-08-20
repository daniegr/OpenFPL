// Shared helpers: images, EV maths, squad legality, CSV.

export const POS_ORDER = { GK: 0, DEF: 1, MID: 2, FWD: 3 }
export const POS_LABEL = { GK: 'GKP', DEF: 'DEF', MID: 'MID', FWD: 'FWD' }
export const CHIP_SHORT = {
  wildcard: 'WC', freehit: 'FH', bench_boost: 'BB', triple_captain: 'TC',
}
export const CHIP_LONG = {
  wildcard: 'Wildcard Played', freehit: 'Free Hit Played',
  bench_boost: 'Bench Boost Played', triple_captain: 'Triple Captain Played',
}

export const shirtUrl = (teamCode, isGk = false) =>
  `https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_${teamCode}${isGk ? '_1' : ''}-66.webp`

export const badgeUrl = (teamCode) =>
  `https://resources.premierleague.com/premierleague/badges/70/t${teamCode}.png`

export const fmt1 = (x) => (x == null || Number.isNaN(x) ? '–' : Number(x).toFixed(1))
export const money = (x) => (x == null ? '–' : `£${Number(x).toFixed(1)}m`)

// Interpolated blue for projection cells: low -> deep panel blue, high -> bright.
export function epColor(v, max = 8) {
  const t = Math.max(0, Math.min(1, (v ?? 0) / max))
  const from = [45, 55, 100]
  const to = [90, 130, 235]
  const c = from.map((f, i) => Math.round(f + (to[i] - f) * t))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

// ---------------- draft model ----------------
// draft = { id, label, note, source, gws: [gwPlan...] }
// gwPlan = { gw, chip, squad: [{id, sell}], xi: [ids], captain, vice,
//            transfers_in: [ids], transfers_out: [ids], bank, free_after, hits }

export function epOf(proj, pid, gw) {
  const rec = proj?.players?.[String(pid)]
  return rec ? rec.ep?.[String(gw)] ?? 0 : 0
}

export function gwEV(plan, proj) {
  if (!plan) return 0
  let ev = 0
  for (const pid of plan.xi) ev += epOf(proj, pid, plan.gw)
  const capMult = plan.chip === 'triple_captain' ? 2 : 1
  if (plan.captain) ev += (1 + (capMult - 1)) * epOf(proj, plan.captain, plan.gw)
  if (plan.chip === 'bench_boost') {
    for (const s of plan.squad) {
      if (!plan.xi.includes(s.id)) ev += epOf(proj, s.id, plan.gw)
    }
  }
  ev -= 4 * (plan.hits || 0)
  return ev
}

export function draftTotalEV(draft, proj) {
  return (draft?.gws || []).reduce((a, p) => a + gwEV(p, proj), 0)
}

// A legal XI: 1 GK, >=3 DEF, >=2 MID, >=1 FWD, 11 total.
export function xiLegal(xiIds, posOf) {
  if (xiIds.length !== 11) return false
  const c = { GK: 0, DEF: 0, MID: 0, FWD: 0 }
  for (const id of xiIds) c[posOf(id)]++
  return c.GK === 1 && c.DEF >= 3 && c.DEF <= 5 && c.MID >= 2 && c.MID <= 5 &&
    c.FWD >= 1 && c.FWD <= 3
}

// Best legal XI from a 15-man squad by projected points for a gw:
// 1 GK, then minimum quotas (3 DEF / 2 MID / 1 FWD), then best of the rest
// within maxima (5 DEF / 5 MID / 3 FWD).
export function bestXI(squadIds, posOf, epFor) {
  const byPos = { GK: [], DEF: [], MID: [], FWD: [] }
  for (const id of squadIds) byPos[posOf(id)]?.push(id)
  for (const pos of Object.keys(byPos)) byPos[pos].sort((a, b) => epFor(b) - epFor(a))
  const xi = []
  const take = (pos, n) => { xi.push(...byPos[pos].splice(0, n)) }
  take('GK', 1); take('DEF', 3); take('MID', 2); take('FWD', 1)
  const max = { DEF: 2, MID: 3, FWD: 2 }   // remaining headroom vs maxima
  const rest = [...byPos.DEF.map((id) => ['DEF', id]), ...byPos.MID.map((id) => ['MID', id]),
                ...byPos.FWD.map((id) => ['FWD', id])].sort((a, b) => epFor(b[1]) - epFor(a[1]))
  for (const [pos, id] of rest) {
    if (xi.length >= 11) break
    if (max[pos] > 0) { xi.push(id); max[pos]-- }
  }
  return xi
}

export function formationRows(xiIds, posOf) {
  const rows = { GK: [], DEF: [], MID: [], FWD: [] }
  for (const id of xiIds) rows[posOf(id)]?.push(id)
  return rows
}

// Convert a solver plan (backend per_gw) into a client draft.
export function planToDraft(plan, meta, label, note) {
  return {
    id: `d${Date.now()}${Math.floor(Math.random() * 1e4)}`,
    label,
    note: note || '',
    source: 'solver',
    entry: meta?.entry_id || null,
    objective: plan.objective,
    gws: plan.per_gw.map((g) => ({
      gw: g.gw,
      chip: g.chip,
      squad: g.squad.map((s) => ({ id: s.player_id, sell: s.sell })),
      xi: g.squad.filter((s) => s.in_xi).map((s) => s.player_id),
      captain: g.captain_id,
      vice: g.vice_id,
      transfers_in: g.transfers_in.map((t) => t.player_id),
      transfers_out: g.transfers_out.map((t) => t.player_id),
      bank: g.bank,
      free_after: g.free_after,
      free_used: g.free_used,
      hits: g.hits,
    })),
  }
}

export function downloadCSV(filename, header, rows) {
  const esc = (v) => {
    const s = String(v ?? '')
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const text = [header, ...rows].map((r) => r.map(esc).join(',')).join('\n')
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
