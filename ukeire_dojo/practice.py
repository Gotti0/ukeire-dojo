"""브라우저별 풀이와 출제 가중치를 관리한다."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MISTAKE_LOSS = 0.01
KINDS = ("opp_riichi", "open_hand", "closed_hand")


def problem_kind(opp_riichi: bool, menzen: bool) -> str:
    if opp_riichi:
        return "opp_riichi"
    return "closed_hand" if menzen else "open_hand"


class Practice:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS attempts (
                    profile TEXT NOT NULL,
                    attempt TEXT NOT NULL,
                    problem_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    tile TEXT NOT NULL,
                    loss REAL NOT NULL,
                    answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (profile, attempt)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS visitors (
                    profile TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("""
                INSERT OR IGNORE INTO visitors (profile, first_seen, last_seen)
                SELECT profile, MIN(answered_at), MAX(answered_at) FROM attempts GROUP BY profile
            """)

    def touch_visitor(self, profile: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """INSERT INTO visitors (profile) VALUES (?)
                   ON CONFLICT (profile) DO UPDATE SET last_seen = CURRENT_TIMESTAMP""",
                (profile,),
            )

    def global_stats(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.path)) as db:
            visitors = db.execute("SELECT COUNT(*) FROM visitors").fetchone()[0]
            answered = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        return {"visitors": visitors, "answered": answered}

    def daily_history(self, today: date | None = None) -> list[dict[str, int | str]]:
        """날짜별 누적 방문자·풀이 수를 반환한다."""
        with closing(sqlite3.connect(self.path)) as db:
            visitor_rows = db.execute(
                "SELECT substr(first_seen, 1, 10) AS d, COUNT(*) FROM visitors GROUP BY d ORDER BY d"
            ).fetchall()
            attempt_rows = db.execute(
                "SELECT substr(answered_at, 1, 10) AS d, COUNT(*) FROM attempts GROUP BY d ORDER BY d"
            ).fetchall()

        visitors_by_day: dict[date, int] = {}
        for day_str, count in visitor_rows:
            if not day_str:
                continue
            try:
                visitors_by_day[date.fromisoformat(day_str)] = int(count)
            except ValueError:
                continue

        answered_by_day: dict[date, int] = {}
        for day_str, count in attempt_rows:
            if not day_str:
                continue
            try:
                answered_by_day[date.fromisoformat(day_str)] = int(count)
            except ValueError:
                continue

        end_day = today or datetime.now(timezone.utc).date()
        active_days = visitors_by_day.keys() | answered_by_day.keys()
        if not active_days:
            start_day = end_day - timedelta(days=6)
            return [
                {
                    "date": (start_day + timedelta(days=offset)).isoformat(),
                    "visitors": 0,
                    "answered": 0,
                }
                for offset in range(7)
            ]

        first_day = min(active_days)
        last_day = max(max(active_days), end_day)
        # 첫날에도 0에서 올라가는 추이선을 그릴 수 있게 하루 전을 시작점으로 둔다.
        start_day = first_day - timedelta(days=1)

        series: list[dict[str, int | str]] = []
        cum_visitors = 0
        cum_answered = 0
        day = start_day
        while day <= last_day:
            cum_visitors += visitors_by_day.get(day, 0)
            cum_answered += answered_by_day.get(day, 0)
            series.append(
                {
                    "date": day.isoformat(),
                    "visitors": cum_visitors,
                    "answered": cum_answered,
                }
            )
            day += timedelta(days=1)
        return series

    def summary(self, profile: str | None) -> dict:
        kinds = {kind: {"answered": 0, "mistakes": 0} for kind in KINDS}
        if profile is not None:
            with closing(sqlite3.connect(self.path)) as db:
                for kind, answered, mistakes in db.execute(
                    """SELECT kind, COUNT(*), SUM(loss >= ?) FROM attempts
                       WHERE profile = ? GROUP BY kind""",
                    (MISTAKE_LOSS, profile),
                ):
                    kinds[kind] = {"answered": answered, "mistakes": mistakes}
        return {"mistake_loss": MISTAKE_LOSS, "kinds": kinds}

    def record(
        self, profile: str, attempt: str, problem_id: int, kind: str, tile: str, loss: float,
    ) -> tuple[int, str]:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """INSERT INTO visitors (profile) VALUES (?)
                   ON CONFLICT (profile) DO UPDATE SET last_seen = CURRENT_TIMESTAMP""",
                (profile,),
            )
            # 첫 응답을 보존해 응답 유실이나 동시 제출로 통계가 두 번 올라가지 않게 한다.
            db.execute(
                """INSERT INTO attempts (profile, attempt, problem_id, kind, tile, loss)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (profile, attempt) DO NOTHING""",
                (profile, attempt, problem_id, kind, tile, loss),
            )
            return db.execute(
                "SELECT problem_id, tile FROM attempts WHERE profile = ? AND attempt = ?",
                (profile, attempt),
            ).fetchone()


def practice_weights(summary: dict) -> dict[str, float]:
    # 첫 몇 번 틀린 유형에 출제가 몰리지 않도록 분모에 5회를 더한다.
    return {
        kind: 1 + 4 * counts["mistakes"] / (counts["answered"] + 5)
        for kind, counts in summary["kinds"].items()
    }
