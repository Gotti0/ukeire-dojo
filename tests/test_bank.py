from pathlib import Path

import pytest

from ukeire_dojo.bank import connect, has_source, problems_from, store
from ukeire_dojo.epsilon import EngineConfig, EpsilonEngine

ROOT = Path(__file__).resolve().parents[1]
SELFPLAY = ROOT / "tests" / "data" / "selfplay-1.mjson"  # tools/selfplay(v760)가 쓴 mjai


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    with EpsilonEngine(EngineConfig(work_root=tmp_path_factory.mktemp("engine"))) as engine:
        return engine.analyze(SELFPLAY.read_bytes())


@pytest.mark.engine
def test_problems_are_consistent(analysis):
    problems = problems_from(analysis, "1")
    assert len(problems) == 500
    for p in problems:
        assert len(p.hand) + 3 * len(p.state["players"][p.seat]["melds"]) == 14
        assert set(p.efficiency) == set(p.ai), "효율은 합법 타패(AI 후보)에 대해서만 계산한다"
        assert set(p.eff_best) <= set(p.efficiency)
        assert p.disagree == (p.ai_best not in p.eff_best)
        losses = [v for entry in p.ai.values() for v in entry.values() if v is not None]
        assert min(losses) == 0.0
        assert p.eff_loss >= 0.0
        if not p.disagree:
            assert p.eff_loss == 0.0, "AI 최선이 효율 최선에 들어 있으면 그 손실 0이 최소다"
        assert not p.state["players"][p.seat]["riichi"]


@pytest.mark.engine
def test_store_round_trip(analysis, tmp_path):
    db = connect(tmp_path / "bank.sqlite")
    problems = problems_from(analysis, "1")
    store(db, "1", analysis, problems)
    assert has_source(db, "1")
    assert db.execute("SELECT COUNT(*) FROM problems").fetchone()[0] == len(problems)
    # 연장국(같은 국 번호, 다른 본장)이 섞여도 키가 겹치지 않아야 한다.
    keys = {(p.round_index, p.honba, p.step) for p in problems}
    assert len(keys) == len(problems)
