const j = async (r) => {
  if (!r.ok) {
    let msg = `${r.status}`
    try { msg = (await r.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return r.json()
}

export const api = {
  status: () => fetch('/api/status').then(j),
  players: () => fetch('/api/players').then(j),
  fixtures: () => fetch('/api/fixtures').then(j),
  projections: () => fetch('/api/projections').then(j),
  buildProjections: (gws, force = false) =>
    fetch('/api/projections/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gws, force }),
    }).then(j),
  pull: () => fetch('/api/pull', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(j),
  entry: (id) => fetch(`/api/entry/${id}`).then(j),
  solve: (params) =>
    fetch('/api/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }).then(j),
  job: (id) => fetch(`/api/jobs/${id}`).then(j),
  myTeam: () => fetch('/api/myteam').then(j),
  saveMyTeam: (doc) =>
    fetch('/api/myteam', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(doc),
    }).then(j),
  clearMyTeam: () => fetch('/api/myteam', { method: 'DELETE' }).then(j),
  importMyTeam: (entry, cookie) =>
    fetch('/api/myteam/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entry, cookie }),
    }).then(j),
  drafts: () => fetch('/api/drafts').then(j),
  saveDrafts: (doc) =>
    fetch('/api/drafts', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(doc),
    }).then(j),
}

export async function pollJob(id, onProgress, intervalMs = 1200) {
  for (;;) {
    const job = await api.job(id)
    if (onProgress) onProgress(job)
    if (job.status === 'done') return job.result
    if (job.status === 'error') throw new Error(job.error || 'job failed')
    await new Promise((res) => setTimeout(res, intervalMs))
  }
}
