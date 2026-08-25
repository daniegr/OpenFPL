"""Time-decayed Poisson team-strength model (Dixon-Coles style, no correlation
term — we only need marginal goal rates and clean-sheet probabilities).

log E[goals] = mu + home_adv*is_home + attack(team) - defence(opponent)

* Fit on ``team_match`` across seasons (teams matched by stable ``team.code``),
  each match weighted by exp-decay in days — recent form matters, old seasons
  fade smoothly.
* The goals target is blended with team xG where available (xG is a lower-
  variance estimate of chance creation than realised goals).
* Ratings of teams with little data (promoted clubs) are shrunk toward a
  below-average prior — historically promoted sides score less and concede
  more than the league mean.
* Everything is point-in-time: only matches with kickoff < as_of enter the fit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

HALF_LIFE_DAYS = 180.0
XG_BLEND = 0.5             # target = (1-b)*goals + b*xg (where xg present)
SHRINK_MATCHES = 6.0       # effective matches at which ratings are half-trusted
PROMOTED_PRIOR = -0.18     # attack/defence prior for clubs with no PL history
N_ITER = 40
DEFAULT_MU = math.log(1.35)    # league scoring rate when no data at all
DEFAULT_HOME_ADV = 0.20


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class TeamModel:
    mu: float = DEFAULT_MU
    home_adv: float = DEFAULT_HOME_ADV
    attack: dict[int, float] = field(default_factory=dict)     # team code -> rating
    defence: dict[int, float] = field(default_factory=dict)
    weight: dict[int, float] = field(default_factory=dict)     # effective matches
    league_rate: float = 1.4

    def rate_for(self, code: int, opp_code: int, home: bool) -> float:
        """Expected goals for `code` against `opp_code`."""
        a = self.attack.get(code, PROMOTED_PRIOR)
        d = self.defence.get(opp_code, PROMOTED_PRIOR)
        return math.exp(self.mu + (self.home_adv if home else 0.0) + a - d)

    def fixture(self, home_code: int, away_code: int) -> tuple[float, float]:
        return (self.rate_for(home_code, away_code, True),
                self.rate_for(away_code, home_code, False))

    def p_clean_sheet(self, code: int, opp_code: int, home: bool) -> float:
        lam_opp = self.rate_for(opp_code, code, not home)
        return math.exp(-lam_opp)


def fit(conn, as_of: str, *, seasons: list[str] | None = None) -> TeamModel:
    """Fit the model on all team_match rows strictly before ``as_of``."""
    q = ("SELECT tm.season, tm.team_id, tm.opponent_id, tm.was_home, "
         "tm.kickoff_utc, tm.goals_for, tm.xg, t.code code, o.code opp_code "
         "FROM team_match tm "
         "JOIN team t ON t.season=tm.season AND t.team_id=tm.team_id "
         "JOIN team o ON o.season=tm.season AND o.team_id=tm.opponent_id "
         "WHERE tm.kickoff_utc < ? AND tm.goals_for IS NOT NULL")
    args = [as_of]
    if seasons:
        q += f" AND tm.season IN ({','.join('?' * len(seasons))})"
        args += seasons
    rows = conn.execute(q, args).fetchall()
    model = TeamModel()
    if not rows:
        return model

    ref = _parse_ts(as_of)
    codes = sorted({r["code"] for r in rows} | {r["opp_code"] for r in rows})
    idx = {c: i for i, c in enumerate(codes)}
    n = len(codes)

    team = np.array([idx[r["code"]] for r in rows])
    opp = np.array([idx[r["opp_code"]] for r in rows])
    home = np.array([1.0 if r["was_home"] else 0.0 for r in rows])
    goals = np.array([float(r["goals_for"] or 0) for r in rows])
    xg = np.array([float(r["xg"]) if r["xg"] is not None else np.nan for r in rows])
    y = np.where(np.isnan(xg), goals, (1 - XG_BLEND) * goals + XG_BLEND * xg)
    days = np.array([(ref - _parse_ts(r["kickoff_utc"])).days for r in rows],
                    dtype=float).clip(min=0)
    w = 0.5 ** (days / HALF_LIFE_DAYS)

    att = np.zeros(n)
    dfc = np.zeros(n)
    mu = math.log(max(1e-6, float(np.average(y, weights=w))))
    home_adv = 0.15

    for _ in range(N_ITER):
        lam = np.exp(mu + home_adv * home + att[team] - dfc[opp])
        # attack updates: multiplicative Poisson MLE per team
        num = np.bincount(team, weights=w * y, minlength=n)
        den = np.bincount(team, weights=w * lam, minlength=n)
        att += np.log(np.clip(num, 1e-9, None) / np.clip(den, 1e-9, None))
        att -= att.mean()
        lam = np.exp(mu + home_adv * home + att[team] - dfc[opp])
        # defence updates: goals conceded by j are goals scored against j
        num = np.bincount(opp, weights=w * y, minlength=n)
        den = np.bincount(opp, weights=w * lam, minlength=n)
        dfc -= np.log(np.clip(num, 1e-9, None) / np.clip(den, 1e-9, None))
        dfc -= dfc.mean()
        # home advantage + intercept
        lam = np.exp(mu + home_adv * home + att[team] - dfc[opp])
        h = home == 1
        if h.any() and (~h).any():
            home_adv += math.log(max(1e-9, (w[h] * y[h]).sum())
                                 / max(1e-9, (w[h] * lam[h]).sum()))
        mu += math.log(max(1e-9, (w * y).sum()) / max(1e-9, (w * lam).sum()))

    # shrink low-data teams toward the promoted prior
    eff = np.bincount(team, weights=w, minlength=n)
    for c, i in idx.items():
        trust = eff[i] / (eff[i] + SHRINK_MATCHES)
        model.attack[c] = trust * att[i] + (1 - trust) * PROMOTED_PRIOR
        model.defence[c] = trust * dfc[i] + (1 - trust) * PROMOTED_PRIOR
        model.weight[c] = float(eff[i])
    model.mu = mu
    model.home_adv = home_adv
    model.league_rate = math.exp(mu + home_adv / 2)
    return model
