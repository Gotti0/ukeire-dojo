"""브라우저별 풀이와 출제 가중치를 관리한다."""

from __future__ import annotations

import sqlite3
from contextlib import closing
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
