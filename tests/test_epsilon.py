from pathlib import Path

import pytest

from ukeire_dojo.epsilon import (
    Action,
    EngineConfig,
    EngineError,
    EpsilonEngine,
    decode_action,
    parse_document,
)

ROOT = Path(__file__).resolve().parents[1]
SELFPLAY = ROOT / "tests" / "data" / "selfplay-1.mjson"  # tools/selfplay(v760)가 쓴 mjai
GOLDEN = ROOT / "spike" / "sample-double-ron-a.xml.gz"


@pytest.mark.parametrize(
    ("action_id", "expected"),
    [
        (0, Action("dahai", "1m")),
        (33, Action("dahai", "C")),
        (34, Action("dahai", "1m", tsumogiri=True)),
        (68, Action("dahai", "5mr")),
        (73, Action("dahai", "5sr", tsumogiri=True)),
        (74, Action("chi")),
        (155, Action("pon")),
        (192, Action("daiminkan")),
        (226, Action("ankan")),
        (260, Action("kakan")),
        (294, Action("riichi", "1m")),
        (367, Action("riichi", "5sr", tsumogiri=True)),
        (368, Action("tsumo")),
        (369, Action("ron")),
        (370, Action("pass")),
        (371, Action("kyushu")),
    ],
)
def test_decode_action_boundaries(action_id, expected):
    assert decode_action(action_id) == expected


@pytest.mark.parametrize("action_id", [-1, 373])
def test_decode_action_rejects_out_of_range(action_id):
    with pytest.raises(ValueError):
        decode_action(action_id)


def test_parse_document_keeps_only_decisions():
    document = {
        "record": {"source": "mjai"},
        "temperature": 0.002,
        "rounds": [
            {
                "roundIndex": 4,
                "honba": 1,
                "steps": [
                    {"event": "tsumo", "actor": 0},
                    {"seat": 0, "cause": 7, "candidates": [[3, 0.0], [40, 0.12]], "chosen": 1},
                    {"seat": 1, "candidates": [[155, 0.0, ["5m", "5m"]], [370, 0.3]]},
                ],
            }
        ],
    }
    analysis = parse_document(document)
    assert len(analysis.decisions) == 2
    first, second = analysis.decisions
    assert (first.round_index, first.honba, first.step, first.seat, first.cause) == (4, 1, 1, 0, 7)
    assert first.best.action == Action("dahai", "4m")
    assert first.chosen_candidate.action == Action("dahai", "7m", tsumogiri=True)
    assert second.chosen is None and second.chosen_candidate is None
    assert second.best.tiles == ("5m", "5m")


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    with EpsilonEngine(EngineConfig(work_root=tmp_path_factory.mktemp("engine"))) as e:
        yield e


@pytest.mark.engine
def test_analyze_selfplay_record(engine):
    analysis = engine.analyze(SELFPLAY.read_bytes())
    assert analysis.record["roundCount"] == 10
    assert len(analysis.decisions) == 651  # T2b에서 같은 패보를 분석한 값
    for decision in analysis.decisions:
        losses = [c.loss for c in decision.candidates]
        assert losses[0] == 0.0
        assert losses == sorted(losses), "후보는 손실 오름차순(최선수 먼저)이어야 한다"
        assert decision.chosen is None or 0 <= decision.chosen < len(decision.candidates)


@pytest.mark.engine
def test_analyze_gzip_tenhou_record(engine):
    analysis = engine.analyze(GOLDEN.read_bytes())
    assert analysis.record["source"] == "tenhou"
    assert len(analysis.decisions) == 824


@pytest.mark.engine
def test_invalid_record_raises_and_engine_survives(engine):
    with pytest.raises(EngineError) as failure:
        engine.analyze(b'{"type":"start_game"}\n{"type":"tsumo","actor":0,"pai":"1m"}\n')
    assert failure.value.code != "engine_exited"
    assert len(engine.analyze(SELFPLAY.read_bytes()).decisions) == 651


@pytest.mark.engine
def test_close_removes_work_dir(tmp_path):
    engine = EpsilonEngine(EngineConfig(work_root=tmp_path))
    work = engine.log_dir.parent
    assert work.exists()
    engine.close()
    assert not work.exists()


GPU_RECORD = ROOT / "tests" / "data" / "main-records-1001.xml.gz"  # main records 명령(GPU)이 쓴 천봉 XML


@pytest.mark.engine
@pytest.mark.gpu
def test_gpu_main_engine_matches_cpu(tmp_path):
    record = GPU_RECORD.read_bytes()
    with EpsilonEngine(EngineConfig.gpu_main(work_root=tmp_path)) as gpu:
        on_gpu = gpu.analyze(record)
    with EpsilonEngine(EngineConfig.cpu_v760(work_root=tmp_path)) as cpu:
        on_cpu = cpu.analyze(record)
    assert len(on_gpu.decisions) == len(on_cpu.decisions) == 927

    # 같은 버림패에 여러 좌석이 응답할 수 있으면 v760과 main이 그 판단들을 다른 순서로 놓으므로 좌석까지 넣어 짝을 맞춘다.
    # cause는 대부분 생략(None)되므로 같은 키가 여러 번 나온다. 키마다 나온 순서로 짝짓는다.
    def keyed(decisions):
        seen: dict = {}
        out = {}
        for d in decisions:
            k = (d.round_index, d.honba, d.seat, frozenset(c.action_id for c in d.candidates))
            seen[k] = seen.get(k, 0) + 1
            out[(k, seen[k])] = d
        return out

    gpu_keyed, cpu_keyed = keyed(on_gpu.decisions), keyed(on_cpu.decisions)
    paired = [(d, cpu_keyed[k]) for k, d in gpu_keyed.items() if k in cpu_keyed]
    assert len(paired) == len(on_gpu.decisions)
    far = 0
    for g, c in paired:
        cpu_loss = {x.action_id: x.loss for x in c.candidates}
        if max(abs(x.loss - cpu_loss[x.action_id]) for x in g.candidates) >= 0.01:
            far += 1
    # BF16과 FP32의 차이는 동률 근처에서만 순위를 바꾼다. 실측(2026-11-26)은 927개 중 1개.
    assert far <= 0.01 * len(paired)
