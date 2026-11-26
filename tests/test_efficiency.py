from ukeire_dojo.efficiency import (
    best_discards,
    chiitoitsu_shanten,
    counts_of,
    discard_efficiencies,
    kokushi_shanten,
    shanten,
    standard_shanten,
    tile_type,
    type_code,
)


def hand(text: str) -> list[int]:
    return counts_of(text.split())


def test_tile_codes_round_trip():
    for t in range(34):
        assert tile_type(type_code(t)) == t
    assert tile_type("5mr".rstrip("r")) == 4


def test_complete_and_tenpai_standard():
    assert standard_shanten(hand("1m 2m 3m 4p 5p 6p 7s 8s 9s E E E 5s 5s"), 4) == -1
    assert standard_shanten(hand("1m 2m 3m 4p 5p 6p 7s 8s 9s E E 5s 6s"), 4) == 0


def test_open_hand_target():
    # 울기 2개: 7장 + 1장 쯔모로 면자 2개와 머리
    assert standard_shanten(hand("2p 3p 4p 6s 7s 8s C C"), 2) == -1
    assert standard_shanten(hand("2p 3p 4p 6s 7s C C"), 2) == 0


def test_chiitoitsu_and_kokushi():
    pairs = hand("1m 1m 3m 3m 5p 5p 7p 7p 9s 9s E E C")
    assert chiitoitsu_shanten(pairs) == 0
    assert shanten(pairs) == 0
    thirteen = hand("1m 9m 1p 9p 1s 9s E S W N P F C")
    assert kokushi_shanten(thirteen) == 0
    assert shanten(thirteen) == 0
    # 울기가 있으면 치또이·국사를 보지 않는다
    assert shanten(pairs, melds=1) == standard_shanten(pairs, 3)


def test_ukeire_counts_unseen_copies():
    fourteen = hand("1m 2m 3m 4p 5p 6p 7s 8s 9s E E 5s 6s C")
    none_seen = [0] * 34
    effs = {type_code(e.discard): e for e in discard_efficiencies(fourteen, 0, none_seen)}
    assert effs["C"].shanten == 0
    assert set(map(type_code, effs["C"].tiles)) == {"4s", "7s"}
    assert effs["C"].ukeire == 7  # 4s 4장 + 7s 3장(한 장은 789s에 있음)
    assert {type_code(t) for t in best_discards(list(effs.values()))} == {"C"}

    seen = [0] * 34
    seen[tile_type("4s")] = 2
    effs = {type_code(e.discard): e for e in discard_efficiencies(fourteen, 0, seen)}
    assert effs["C"].ukeire == 5
