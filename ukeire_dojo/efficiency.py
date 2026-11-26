"""샨텐과 유효패(우케이레) 계산. 패 종류 번호는 0..33(만·통·삭 1..9, 동남서북백발중)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

HONORS = "ESWNPFC"
TERMINALS = (0, 8, 9, 17, 18, 26, *range(27, 34))


def tile_type(code: str) -> int:
    if code[0] in HONORS and len(code) == 1:
        return 27 + HONORS.index(code)
    return "mps".index(code[1]) * 9 + int(code[0]) - 1


def type_code(tile: int) -> str:
    return HONORS[tile - 27] if tile >= 27 else f"{tile % 9 + 1}{'mps'[tile // 9]}"


def counts_of(codes: Iterable[str]) -> list[int]:
    counts = [0] * 34
    for code in codes:
        counts[tile_type(code)] += 1
    return counts


def _pareto(options: set[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """(면자, 탑쯔) 쌍 중 다른 쌍에 둘 다 지배되는 것을 버린다."""
    return tuple(o for o in options if not any(p != o and p[0] >= o[0] and p[1] >= o[1] for p in options))


@lru_cache(maxsize=None)
def _suit_options(counts: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    """수패 한 색(9칸)을 면자와 탑쯔(머리 후보 대자 포함)로 나누는 방법들."""
    first = next((i for i, c in enumerate(counts) if c), None)
    if first is None:
        return ((0, 0),)
    results: set[tuple[int, int]] = set()

    def take(removed: dict[int, int], meld: int, partial: int) -> None:
        rest = list(counts)
        for i, n in removed.items():
            rest[i] -= n
        for m, t in _suit_options(tuple(rest)):
            results.add((m + meld, t + partial))

    c = counts
    i = first
    if c[i] >= 3:
        take({i: 3}, 1, 0)
    if i <= 6 and c[i + 1] and c[i + 2]:
        take({i: 1, i + 1: 1, i + 2: 1}, 1, 0)
    if c[i] >= 2:
        take({i: 2}, 0, 1)
    if i <= 7 and c[i + 1]:
        take({i: 1, i + 1: 1}, 0, 1)
    if i <= 6 and c[i + 2]:
        take({i: 1, i + 2: 1}, 0, 1)
    take({i: 1}, 0, 0)  # 고립패로 버린다
    return _pareto(results)


@lru_cache(maxsize=None)
def _group_options(counts: tuple[int, ...], honors: bool) -> tuple[tuple[int, int, int], ...]:
    """(면자, 탑쯔, 머리) 선택지. 머리는 이 그룹에서 대자 하나를 머리로 쓴 경우 1."""
    options: set[tuple[int, int, int]] = set()

    def decompose(cs: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
        if not honors:
            return _suit_options(cs)
        m = sum(1 for x in cs if x >= 3)
        t = sum(1 for x in cs if x == 2)
        # 자패 4장은 면자 1 + 고립 1이다. 3장을 대자+고립으로 쪼개는 것은 면자보다 나을 일이 없다.
        return ((m, t),)

    for m, t in decompose(counts):
        options.add((m, t, 0))
    for i, x in enumerate(counts):
        if x >= 2:
            rest = list(counts)
            rest[i] -= 2
            for m, t in decompose(tuple(rest)):
                options.add((m, t, 1))
    return tuple(options)


def standard_shanten(counts: Sequence[int], meld_target: int) -> int:
    """일반형 샨텐. meld_target은 손 안에서 더 만들어야 할 면자 수(4 − 울기 수)."""
    groups = [
        _group_options(tuple(counts[0:9]), False),
        _group_options(tuple(counts[9:18]), False),
        _group_options(tuple(counts[18:27]), False),
        _group_options(tuple(counts[27:34]), True),
    ]
    # 샨텐 = 2·목표 − 2·면자 − 탑쯔 − 머리. 완성형(면자=목표, 머리 1)이 −1이 된다.
    best = 2 * meld_target
    combos = [(0, 0, 0)]
    for group in groups:
        combos = {(m + gm, t + gt, h + gh) for m, t, h in combos for gm, gt, gh in group if h + gh <= 1}
    for m, t, h in combos:
        m = min(m, meld_target)
        t = min(t, meld_target - m)
        best = min(best, 2 * meld_target - 2 * m - t - h)
    return best


def chiitoitsu_shanten(counts: Sequence[int]) -> int:
    pairs = sum(1 for c in counts if c >= 2)
    kinds = sum(1 for c in counts if c >= 1)
    return 6 - pairs + max(0, 7 - kinds)


def kokushi_shanten(counts: Sequence[int]) -> int:
    kinds = sum(1 for t in TERMINALS if counts[t])
    pair = any(counts[t] >= 2 for t in TERMINALS)
    return 13 - kinds - (1 if pair else 0)


def shanten(counts: Sequence[int], melds: int = 0) -> int:
    """울기가 없을 때만 치또이·국사를 함께 본다."""
    value = standard_shanten(counts, 4 - melds)
    if melds == 0:
        value = min(value, chiitoitsu_shanten(counts), kokushi_shanten(counts))
    return value


@dataclass(frozen=True)
class DiscardEfficiency:
    discard: int
    shanten: int  # 이 패를 버린 뒤의 샨텐
    ukeire: int  # 샨텐을 줄이는 패의 보이지 않은 남은 장수 합
    tiles: tuple[int, ...]  # 샨텐을 줄이는 패 종류


def discard_efficiencies(hand: Sequence[int], melds: int, visible: Sequence[int]) -> list[DiscardEfficiency]:
    """hand는 3n+2장 손패의 종류별 장수, visible은 손패 밖에서 보이는 종류별 장수(버림패·울기·도라 표시패)."""
    results = []
    for discard in range(34):
        if not hand[discard]:
            continue
        after = list(hand)
        after[discard] -= 1
        base = shanten(after, melds)
        tiles, ukeire = [], 0
        for draw in range(34):
            remaining = 4 - after[draw] - visible[draw]
            if remaining <= 0:
                continue
            after[draw] += 1
            if shanten(after, melds) < base:
                tiles.append(draw)
                ukeire += remaining
            after[draw] -= 1
        results.append(DiscardEfficiency(discard, base, ukeire, tuple(tiles)))
    return results


def best_discards(efficiencies: Sequence[DiscardEfficiency]) -> set[int]:
    """샨텐을 가장 적게 남기고, 그중 유효패가 가장 많은 버림패들."""
    low = min(e.shanten for e in efficiencies)
    top = max(e.ukeire for e in efficiencies if e.shanten == low)
    return {e.discard for e in efficiencies if e.shanten == low and e.ukeire == top}
