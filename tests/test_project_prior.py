"""Pre-season prior blend in the projection layer."""
import os
import tempfile

import pytest

from fpl_engine import db
from fpl_engine.optimise import project


def test_preseason_weight_fades_out():
    assert project.preseason_weight(0) == pytest.approx(0.5)
    assert project.preseason_weight(1) == pytest.approx(0.5 * 2 / 3)
    assert project.preseason_weight(3) == 0.0
    assert project.preseason_weight(10) == 0.0


def test_preseason_priors_shrink_toward_position_mean():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    db.init_db(path)
    c = db.connect(path)
    db.upsert(c, "player", [
        {"season": "2025-26", "player_id": 1, "code": 100, "full_name": "Star",
         "team_id": 1, "position": "FWD"},
        {"season": "2025-26", "player_id": 2, "code": 200, "full_name": "Fringe",
         "team_id": 1, "position": "FWD"},
        {"season": "2025-26", "player_id": 3, "code": 300, "full_name": "New",
         "team_id": 1, "position": "FWD"},
    ])
    rows = []
    for gw in range(1, 11):                        # Star: 10 x 90', 8 pts each
        rows.append({"season": "2024-25", "gw": gw, "source": "vaastav", "player_id": 1,
                     "fixture_id": gw, "player_code": 100, "minutes": 90, "total_points": 8,
                     "kickoff_utc": f"2024-08-{gw:02d}T14:00:00Z"})
    rows.append({"season": "2024-25", "gw": 1, "source": "vaastav", "player_id": 2,
                 "fixture_id": 1, "player_code": 200, "minutes": 10, "total_points": 5,
                 "kickoff_utc": "2024-08-01T14:00:00Z"})   # Fringe: 45 pts/90 on 10'
    db.upsert(c, "player_gw", rows)
    c.commit()
    profiles = {1: {"xmins": 90.0}, 2: {"xmins": 90.0}, 3: {"xmins": 90.0}}
    pri = project.preseason_priors(c, "2025-26", profiles)
    c.close()
    try:
        os.remove(path)
    except PermissionError:
        pass
    # position mean = (80 + 5) / (900 + 10) * 90 = 8.41 pts/90
    assert pri[1] == pytest.approx((80 + 8.4066 * 5) / (900 + 450) * 90, rel=1e-3)
    # Fringe's 45/90 on ten minutes is pulled hard toward the mean
    assert pri[2] < 12
    assert 3 not in pri                            # no last-season minutes -> no prior
