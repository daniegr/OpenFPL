"""fpl_engine: automatic, free data pipeline feeding OpenFPL.

Pulls Fantasy Premier League data (and free advanced stats) into a local
SQLite database, builds the OpenFPL feature samples point-in-time, and runs
the pre-trained OpenFPL ensemble end-to-end.

Everything is free and requires no API keys:
  * Official FPL API   (https://fantasy.premierleague.com/api/)
  * Understat          (https://understat.com/)
  * vaastav historical (https://github.com/vaastav/Fantasy-Premier-League)

See README.md ("Automatic data pipeline") for usage.
"""

__all__ = ["config", "db"]

__version__ = "0.1.0"
