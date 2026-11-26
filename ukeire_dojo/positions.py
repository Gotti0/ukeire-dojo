"""리뷰어 결과 문서(protocol/result.ts)에서 판단 직전의 국면을 복원한다.

문서는 국마다 시작 국면(start)과 단계(steps)를 갖는다. 이벤트 단계는 직전 국면에 대한 차분(delta)을 갖고, 판단 단계는 그 판단이 일으킨
이벤트보다 앞에 놓인다. 그래서 이벤트 차분을 순서대로 적용하다가 판단을 만나면 그때의 국면이 곧 판단 직전 국면이다.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass

from ukeire_dojo.epsilon import Analysis, Decision


@dataclass(frozen=True)
class Position:
    decision: Decision
    state: dict  # TableState. 네 명의 손패가 모두 들어 있으므로 사용자에게는 자기 손패만 보여야 한다.

    @property
    def player(self) -> dict:
        return self.state["players"][self.decision.seat]

    @property
    def concealed(self) -> list[str]:
        """손패에 쯔모 패를 더한 것. 판단 시점의 숨긴 패 전부."""
        tiles = list(self.player["hand"])
        if self.player["draw"] is not None:
            tiles.append(self.player["draw"])
        return tiles

    @property
    def junme(self) -> int:
        return len(self.player["river"]) + 1


def _splice(values: list, patch: list) -> None:
    index, remove, *inserted = patch
    values[index : index + remove] = inserted


def apply_delta(state: dict, delta: dict) -> None:
    for player_delta in delta.get("players", ()):
        player = state["players"][player_delta["seat"]]
        for key in ("score", "riichi", "draw"):
            if key in player_delta:
                player[key] = player_delta[key]
        for key in ("hand", "river", "melds"):
            if key in player_delta:
                _splice(player[key], player_delta[key])
    if "dora" in delta:
        _splice(state["dora"], delta["dora"])
    for key in ("honba", "kyotaku", "remaining", "activeSeat"):
        if key in delta:
            state[key] = delta[key]


def iter_positions(analysis: Analysis) -> Iterator[Position]:
    decisions = iter(analysis.decisions)
    for rnd in analysis.document["rounds"]:
        state = copy.deepcopy(rnd["start"])
        for step_index, step in enumerate(rnd["steps"]):
            if "seat" in step:
                decision = next(decisions)
                if (decision.round_index, decision.step) != (rnd["roundIndex"], step_index):
                    raise ValueError(f"decision order mismatch at round {rnd['roundIndex']} step {step_index}")
                yield Position(decision, copy.deepcopy(state))
            elif "delta" in step:
                apply_delta(state, step["delta"])
