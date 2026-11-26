"""타패 판단 국면을 효율 기준과 AI 기준으로 함께 채점해 SQLite 문제은행에 담는다."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ukeire_dojo.efficiency import (
    best_discards,
    counts_of,
    discard_efficiencies,
    tile_type,
    type_code,
)
from ukeire_dojo.epsilon import Analysis
from ukeire_dojo.positions import Position, iter_positions

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source TEXT PRIMARY KEY,
  rounds INTEGER NOT NULL,
  decisions INTEGER NOT NULL,
  problems INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS problems (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL REFERENCES sources(source),
  round_index INTEGER NOT NULL,
  honba INTEGER NOT NULL,
  step INTEGER NOT NULL,
  seat INTEGER NOT NULL,
  junme INTEGER NOT NULL,
  menzen INTEGER NOT NULL,
  opp_riichi INTEGER NOT NULL,
  shanten INTEGER NOT NULL,       -- 효율 최선을 버린 뒤의 샨텐
  hand TEXT NOT NULL,             -- 숨긴 패 14−3k장(JSON, 정렬)
  state TEXT NOT NULL,            -- TableState(JSON). 상대 손패가 들어 있으므로 화면에 그대로 내보내지 않는다.
  efficiency TEXT NOT NULL,       -- {패: {shanten, ukeire, tiles}} 합법 타패만
  ai TEXT NOT NULL,               -- {패: {dahai, riichi}} 최선수 대비 손실, 없으면 null
  eff_best TEXT NOT NULL,         -- 효율 최선 패 목록(JSON)
  ai_best TEXT NOT NULL,
  ai_best_riichi INTEGER NOT NULL,
  disagree INTEGER NOT NULL,      -- AI 최선이 효율 최선 집합 밖
  eff_loss REAL NOT NULL,         -- 효율 최선 중 AI가 가장 덜 나쁘게 본 수의 손실
  -- 연장하면 같은 국 번호가 본장만 바뀌어 다시 나오므로 본장까지 넣어야 유일하다.
  UNIQUE (source, round_index, honba, step)
);
CREATE INDEX IF NOT EXISTS problems_disagree ON problems (disagree, eff_loss);
"""


@dataclass(frozen=True)
class Problem:
    source: str
    round_index: int
    honba: int
    step: int
    seat: int
    junme: int
    menzen: bool
    opp_riichi: bool
    shanten: int
    hand: list[str]
    state: dict
    efficiency: dict
    ai: dict
    eff_best: list[str]
    ai_best: str
    ai_best_riichi: bool
    disagree: bool
    eff_loss: float


def _visible_outside_hand(position: Position) -> list[int]:
    """자기 숨긴 패 밖에서 이 좌석이 볼 수 있는 패. 상대 손패는 넣지 않는다."""
    codes: list[str] = list(position.state["dora"])
    for player in position.state["players"]:
        # 울어 간 패는 강에 called 표시로 남고 울기 쪽에도 있으므로 한 번만 센다.
        codes.extend(t["tile"] for t in player["river"] if not t.get("called"))
        for meld in player["melds"]:
            codes.extend(meld["tiles"])
    return counts_of(codes)


def problem_from(position: Position, source: str) -> Problem | None:
    """타패 국면이 아니거나 고를 것이 없으면 None."""
    decision = position.decision
    if position.player["riichi"]:
        return None
    if decision.best.action.kind not in ("dahai", "riichi"):
        return None
    ai: dict[int, dict[str, float | None]] = {}
    for candidate in decision.candidates:
        action = candidate.action
        if action.kind not in ("dahai", "riichi"):
            continue
        entry = ai.setdefault(tile_type(action.tile.rstrip("r")), {"dahai": None, "riichi": None})
        # 같은 종류의 쯔모기리·적5 변형 중 가장 좋은 손실을 그 패의 값으로 본다. 후보는 손실 오름차순이라 처음 본 값이 최소다.
        if entry[action.kind] is None:
            entry[action.kind] = candidate.loss
    if len(ai) < 2:
        return None

    melds = len(position.player["melds"])
    hand = counts_of(position.concealed)
    visible = _visible_outside_hand(position)
    # 쿠이카에 금지 등으로 후보에 없는 패는 버릴 수 없으므로 효율 비교에서도 뺀다.
    efficiencies = [e for e in discard_efficiencies(hand, melds, visible) if e.discard in ai]
    eff_best = best_discards(efficiencies)
    ai_best = tile_type(decision.best.action.tile.rstrip("r"))

    def loss(tile: int) -> float:
        values = [v for v in ai[tile].values() if v is not None]
        return min(values)

    others = [p for seat, p in enumerate(position.state["players"]) if seat != decision.seat]
    return Problem(
        source=source,
        round_index=decision.round_index,
        honba=decision.honba,
        step=decision.step,
        seat=decision.seat,
        junme=position.junme,
        menzen=all(m["type"] == "ANKAN" for m in position.player["melds"]),
        opp_riichi=any(p["riichi"] for p in others),
        shanten=min(e.shanten for e in efficiencies),
        hand=sorted(position.concealed, key=lambda c: (tile_type(c.rstrip("r")), c)),
        state=position.state,
        efficiency={
            type_code(e.discard): {"shanten": e.shanten, "ukeire": e.ukeire, "tiles": [type_code(t) for t in e.tiles]}
            for e in efficiencies
        },
        ai={type_code(t): v for t, v in ai.items()},
        eff_best=sorted(type_code(t) for t in eff_best),
        ai_best=type_code(ai_best),
        ai_best_riichi=decision.best.action.kind == "riichi",
        disagree=ai_best not in eff_best,
        eff_loss=min(loss(t) for t in eff_best),
    )


def problems_from(analysis: Analysis, source: str) -> list[Problem]:
    return [p for p in (problem_from(pos, source) for pos in iter_positions(analysis)) if p is not None]


def connect(path: Path) -> sqlite3.Connection:
    """여러 스레드가 하나의 연결을 쓸 수 있게 연다. 쓰기 직렬화는 호출하는 쪽이 맡는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


def has_source(db: sqlite3.Connection, source: str) -> bool:
    return db.execute("SELECT 1 FROM sources WHERE source = ?", (source,)).fetchone() is not None


def store(db: sqlite3.Connection, source: str, analysis: Analysis, problems: list[Problem]) -> None:
    with db:
        db.execute(
            "INSERT INTO sources (source, rounds, decisions, problems) VALUES (?, ?, ?, ?)",
            (source, analysis.record["roundCount"], len(analysis.decisions), len(problems)),
        )
        db.executemany(
            """INSERT INTO problems (source, round_index, honba, step, seat, junme, menzen, opp_riichi, shanten,
                 hand, state, efficiency, ai, eff_best, ai_best, ai_best_riichi, disagree, eff_loss)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    p.source, p.round_index, p.honba, p.step, p.seat, p.junme, int(p.menzen), int(p.opp_riichi),
                    p.shanten, json.dumps(p.hand), json.dumps(p.state, ensure_ascii=False), json.dumps(p.efficiency),
                    json.dumps(p.ai), json.dumps(p.eff_best), p.ai_best, int(p.ai_best_riichi), int(p.disagree),
                    p.eff_loss,
                )
                for p in problems
            ],
        )
