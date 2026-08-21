"""Point-in-time expected-minutes model (minutes-risk adjustment).

OpenFPL's expected points implicitly assume a player keeps playing the way his
trailing feature windows say he has. That prices settled rotation reasonably
well but reacts slowly to the situations that actually lose FPL managers
points: injury flags, a starter being benched (or a bench player promoted),
and returning players. This module builds an explicit expected-minutes profile
per player and turns it into a multiplicative correction *relative to the
baseline the features already encode*, so settled patterns are left untouched
and only the delta between "what the model assumed" and "what we now expect"
is applied.

Structural decomposition (decay-weighted over the recent match log, using only
matches with ``kickoff_utc`` strictly before the target gameweek's first
kickoff — the same point-in-time boundary as the feature builder):

    p_start        = decayed share of recent matches started
    mins_per_start = decayed average minutes in started matches
    p_cameo        = decayed share of non-starts that got minutes
    cameo_mins     = decayed average minutes in those cameos

    xmins  = avail * (p_start*mins_per_start + (1-p_start)*p_cameo*cameo_mins)
    factor = clip(xmins / baseline, 0, MAX_UPLIFT)

where ``avail`` is FPL's chance-of-playing flag and ``baseline`` is the plain
(unweighted) average minutes over the same window — a proxy for the trailing
minutes assumption embedded in the features. Players with no usable history
(new signings) get no profile; callers fall back to the plain availability
multiplier, which is the pre-existing behaviour.

Season breaks: when the newest match is more than ``GAP_DAYS`` before
``as_of`` (pre-season, or a long layoff), recency ordering inside the stale
window carries no information — an injury in May says nothing about August.
For a player FPL lists as fit, absences in *either* window were injury/rest
rather than selection, so expected minutes take the better of the recent and
long-run (uniform, ``LONG_WINDOW``) rates: a nailed starter whose season
ended with a knock is not punished twice (once by the model's short windows,
again here), and one who missed mid-season but finished as a starter is not
punished either. The factor is therefore never below ``avail`` pre-season.
"""
from __future__ import annotations

from datetime import datetime, timezone

DECAY = 0.65           # per-match weight decay, newest first
WINDOW = 6             # matches considered (recent, decayed)
LONG_WINDOW = 38       # matches considered across a season break (uniform)
GAP_DAYS = 21          # newest match older than this -> season break / layoff
PRIOR_START_MINS = 84.0   # E[min | start] fallback when no starts observed
PRIOR_CAMEO_MINS = 20.0   # E[min | cameo] fallback
MIN_BASELINE = 10.0    # below this the ratio is meaningless -> avail only
MAX_UPLIFT = 1.15      # never inflate EP by more than 15% on minutes alone


def _availability(status, chance_next) -> float:
    if chance_next is not None:
        return float(chance_next)
    return 1.0 if status in (None, "a") else 0.0


def minutes_profiles(conn, season: str, as_of: str | None) -> dict[int, dict]:
    """Expected-minutes profile per current-season player_id.

    Only players with at least one match strictly before ``as_of`` appear.
    Each profile has: p_start, xmins, baseline, factor, avail.
    """
    if not as_of:
        return {}
    players = conn.execute(
        "SELECT player_id, code, status, chance_next FROM player "
        "WHERE season=?", (season,)).fetchall()
    as_of_dt = _parse_utc(as_of)
    rows = conn.execute(
        """
        WITH m AS (
            SELECT player_code, MAX(minutes) AS minutes, MAX(starts) AS starts,
                   MAX(kickoff_utc) AS kickoff_utc
            FROM player_gw
            WHERE player_code IS NOT NULL AND kickoff_utc IS NOT NULL
              AND kickoff_utc < ?
            GROUP BY player_code, season, gw, fixture_id
        ), r AS (
            SELECT m.*, ROW_NUMBER() OVER (
                PARTITION BY player_code ORDER BY kickoff_utc DESC) AS rn
            FROM m
        )
        SELECT player_code, minutes, starts, kickoff_utc FROM r
        WHERE rn <= ? ORDER BY player_code, rn
        """, (as_of, LONG_WINDOW)).fetchall()
    log: dict[int, list] = {}
    for r in rows:
        log.setdefault(int(r["player_code"]), []).append(r)

    out: dict[int, dict] = {}
    for p in players:
        full = log.get(p["code"] or -1)
        if not full:
            continue
        newest = _parse_utc(full[0]["kickoff_utc"])
        stale = (as_of_dt is not None and newest is not None and
                 (as_of_dt - newest).days > GAP_DAYS)
        # recent window, decayed (in-season) / long window, uniform (after a
        # break); the baseline always reflects the recent window the model's
        # short-horizon features are built from
        ms = full[:WINDOW]
        rate_rows = full if stale else ms
        decay = 1.0 if stale else DECAY
        w_all = s_start = s_start_min = s_nostart = s_cameo = s_cameo_min = 0.0
        base = 0.0
        for i, r in enumerate(rate_rows):
            w = decay ** i
            mins = float(r["minutes"] or 0)
            started = (float(r["starts"]) if r["starts"] is not None
                       else (1.0 if mins >= 60 else 0.0))
            w_all += w
            if started:
                s_start += w
                s_start_min += w * mins
            else:
                s_nostart += w
                if mins > 0:
                    s_cameo += w
                    s_cameo_min += w * mins
        p_start = s_start / w_all
        mins_per_start = (s_start_min / s_start) if s_start else PRIOR_START_MINS
        p_cameo = (s_cameo / s_nostart) if s_nostart else 0.0
        cameo_mins = (s_cameo_min / s_cameo) if s_cameo else PRIOR_CAMEO_MINS
        avail = _availability(p["status"], p["chance_next"])
        rate = p_start * mins_per_start + (1 - p_start) * p_cameo * cameo_mins
        baseline = sum(float(r["minutes"] or 0) for r in ms) / len(ms)
        if stale:
            rate = max(rate, baseline)   # fit player: stale absences don't count
        xmins = avail * rate
        if baseline < MIN_BASELINE:
            factor = avail
        else:
            factor = max(0.0, min(MAX_UPLIFT, xmins / baseline))
        out[int(p["player_id"])] = {
            "p_start": round(p_start, 3),
            "xmins": round(xmins, 1),
            "baseline": round(baseline, 1),
            "factor": round(factor, 4),
            "avail": avail,
            "stale": stale,
        }
    return out


def _parse_utc(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
