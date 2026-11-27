import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ukeire_dojo import rooms
from ukeire_dojo.bank import SCHEMA
from ukeire_dojo.rooms import _CHECKPOINT, LiveMatchClient, RoomManager
from ukeire_dojo.server import Handler, ProblemPicker, RateLimiter, Server
from tests.test_server import insert


def req_json(url, *, method="GET", token=None, body=None):
    headers = {}
    if token:
        headers["X-Player-Token"] = token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


@pytest.fixture
def live_server(tmp_path):
    bank_path = tmp_path / "bank.sqlite"
    db = sqlite3.connect(bank_path)
    db.executescript(SCHEMA)
    insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.02)
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    Handler.db_path = bank_path
    Handler.static_root = static
    Handler.picker = ProblemPicker(db)
    Handler.practice = None
    Handler.password = None
    Handler.limiter = RateLimiter(rate=1000, burst=1000)
    manager = RoomManager(
        LiveMatchClient(bot_mode="scripted", work_root=tmp_path / "live-work"),
        disconnect_grace_seconds=0.25,
        ping_interval=1.0,
    )
    Handler.rooms = manager
    httpd = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", manager
    manager.close()
    httpd.shutdown()
    httpd.server_close()
    db.close()
    Handler.rooms = None
    Handler.limiter = RateLimiter()


def read_ndjson_event(resp):
    line = resp.readline()
    assert line, "stream closed unexpectedly"
    return json.loads(line.decode("utf-8"))


def test_lobby_validation_seats_leave_and_host_succession(live_server):
    base, _ = live_server
    code, host = req_json(f"{base}/api/rooms", method="POST", body={"name": "방장"})
    assert code == 201
    room_code = host["roomCode"]

    # 1인 상태에서는 별도 준비 없이도 시작 가능
    assert host["state"]["canStart"] is True
    st, _ = req_json(f"{base}/api/rooms/{room_code}/ready", method="POST", token=host["token"], body={"ready": True})
    assert st == 200

    # 2번째 참가자 입장 및 좌석 이동 (2인 이상이면 미준비 시 시작 불가)
    st, p2 = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": "손님"})
    assert st == 200 and p2["seat"] == 1
    st, moved = req_json(f"{base}/api/rooms/{room_code}/seat", method="POST", token=p2["token"], body={"seat": 3})
    assert st == 200 and moved["mySeat"] == 3
    # 이미 찬 좌석으로 이동 거부
    st, _ = req_json(f"{base}/api/rooms/{room_code}/seat", method="POST", token=p2["token"], body={"seat": 0})
    assert st == 409

    # 방장이 아니면 시작 불가, 미준비 시 시작 불가
    st, _ = req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=p2["token"], body={})
    assert st == 403
    st, _ = req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=host["token"], body={})
    assert st == 409

    # 방장이 나가면 남은 참가자가 방장 승계
    st, _ = req_json(f"{base}/api/rooms/{room_code}/leave", method="POST", token=host["token"], body={})
    assert st == 200
    st, p2_state = req_json(f"{base}/api/rooms/{room_code}", token=p2["token"])
    assert st == 200 and p2_state["isHost"] is True
    # 나간 방장 토큰은 거부
    st, _ = req_json(f"{base}/api/rooms/{room_code}", token=host["token"])
    assert st == 403

    # 마지막 참가자가 나가면 방 삭제
    st, _ = req_json(f"{base}/api/rooms/{room_code}/leave", method="POST", token=p2["token"], body={})
    assert st == 200
    st, _ = req_json(f"{base}/api/rooms/{room_code}", token=p2["token"])
    assert st == 404


def test_player_counts_1_to_4_and_seat_isolation(live_server):
    base, _ = live_server
    for human_count in (1, 2, 3, 4):
        _, host = req_json(f"{base}/api/rooms", method="POST", body={"name": "P0"})
        room_code = host["roomCode"]
        players = [host]
        for idx in range(1, human_count):
            _, joined = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": f"P{idx}"})
            players.append(joined)
        if human_count == 4:
            st, _ = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": "P4"})
            assert st == 409
        if human_count >= 2:
            for p in players:
                req_json(f"{base}/api/rooms/{room_code}/ready", method="POST", token=p["token"], body={"ready": True})
        st, started = req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=host["token"], body={})
        assert st == 200
        kinds = [s["kind"] for s in started["seats"]]
        assert kinds.count("human") == human_count and kinds.count("ai") == 4 - human_count

        # 토큰 누락/오류 거부 및 좌석별 비밀 정보 분리 확인
        assert req_json(f"{base}/api/rooms/{room_code}")[0] == 401
        assert req_json(f"{base}/api/rooms/{room_code}", token="0" * 32)[0] == 403

        tokens = {p["token"] for p in players}
        for p in players:
            st, state = req_json(f"{base}/api/rooms/{room_code}", token=p["token"])
            assert st == 200
            raw = json.dumps(state, ensure_ascii=False)
            for other_token in tokens:
                assert other_token not in raw
            for forbidden in ("wall", "seed", "pendingSeats", "pending_submissions"):
                assert forbidden not in raw
            for slot_summary in state["seats"]:
                assert "token" not in slot_summary
            for tbl_player in state["match"]["table"]["players"]:
                assert "hand" not in tbl_player and "draw" not in tbl_player
            if state["mySeat"] != 0:
                assert state["match"]["table"]["draw"] is None
                assert state["match"]["decision"] is None
                assert state["match"]["table"]["lastEvent"]["tile"] is None
            else:
                assert state["match"]["table"]["draw"] is not None
                assert state["match"]["decision"] is not None


def _play_match_over_http(base, room_code, players, target_statuses=("MATCH_END", "SERIES_END")):
    steps = 0
    while True:
        states = {seat: req_json(f"{base}/api/rooms/{room_code}", token=tok)[1] for seat, tok in players.items()}
        if any(st_obj["status"] in target_statuses for st_obj in states.values()):
            return states
        progressed = False
        for seat, st_obj in states.items():
            m = st_obj["match"]
            if m["phase"] == "DECISIONS" and m["decision"] is not None:
                d = m["decision"]
                chosen = next((a for a in d["actions"] if a["type"] in ("TSUMO_AGARI", "RON_AGARI")), None)
                if chosen is None:
                    chosen = next((a for a in d["actions"] if a["type"] in ("DAHAI", "PASS")), d["actions"][0])
                code, _ = req_json(
                    f"{base}/api/rooms/{room_code}/action",
                    method="POST",
                    token=players[seat],
                    body={"revision": d["revision"], "action": chosen["code"]},
                )
                assert code == 200
                progressed = True
        if not progressed:
            first_seat = next(iter(players))
            m0 = states[first_seat]["match"]
            if m0["phase"] == "ROUND_END":
                rnd = m0["round"]
                for seat in players:
                    code, _ = req_json(
                        f"{base}/api/rooms/{room_code}/next-round",
                        method="POST",
                        token=players[seat],
                        body={"kyoku": rnd["kyoku"], "honba": rnd["honba"]},
                    )
                    assert code == 200
                assert req_json(
                    f"{base}/api/rooms/{room_code}/next-round",
                    method="POST",
                    token=players[first_seat],
                    body={"kyoku": rnd["kyoku"], "honba": rnd["honba"]},
                )[0] == 409
                progressed = True
        assert progressed, "match stalled with no actionable human"
        steps += 1
        assert steps < 4000


def test_rejects_stale_duplicate_and_illegal_actions_and_finishes_match(live_server):
    base, _ = live_server
    _, p0 = req_json(f"{base}/api/rooms", method="POST", body={"name": "동가"})
    room_code = p0["roomCode"]
    _, p1 = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": "남가"})
    players = {0: p0["token"], 1: p1["token"]}
    for tok in players.values():
        req_json(f"{base}/api/rooms/{room_code}/ready", method="POST", token=tok, body={"ready": True})
    st, s0 = req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=p0["token"], body={})
    assert st == 200

    # 잘못된 행동, 차례 아닌 좌석, 지난 revision 검증
    dec = s0["match"]["decision"]
    rev0 = dec["revision"]
    legal0 = dec["actions"][0]["code"]
    assert req_json(f"{base}/api/rooms/{room_code}/action", method="POST", token=players[1], body={"revision": rev0, "action": legal0})[0] == 409
    assert req_json(f"{base}/api/rooms/{room_code}/action", method="POST", token=players[0], body={"revision": rev0, "action": -999})[0] == 400
    assert req_json(f"{base}/api/rooms/{room_code}/action", method="POST", token=players[0], body={"revision": rev0 - 1, "action": legal0})[0] == 409

    # 1경기에서는 타패 추천 요청을 거부해야 한다.
    assert s0["match"]["hintAllowed"] is False
    assert req_json(f"{base}/api/rooms/{room_code}/hint", method="POST", token=players[0], body={"revision": rev0})[0] == 403
    assert req_json(f"{base}/api/rooms/{room_code}/replay", method="POST", token=players[0], body={})[0] == 409

    # 정상 제출 후 이전 revision 재전송 거부
    st, _ = req_json(f"{base}/api/rooms/{room_code}/action", method="POST", token=players[0], body={"revision": rev0, "action": legal0})
    assert st == 200
    assert req_json(f"{base}/api/rooms/{room_code}/action", method="POST", token=players[0], body={"revision": rev0, "action": legal0})[0] == 409

    # 사람 2명 + AI 2명으로 동풍전 1경기 완주
    states = _play_match_over_http(base, room_code, players, target_statuses=("MATCH_END",))
    final = states[0]["match"]["settlement"]
    assert sum(final["scores"]) == 100000
    assert sorted(final["ranks"]) == [1, 2, 3, 4]


def test_disconnect_grace_ai_takeover_and_reconnect(live_server):
    base, _ = live_server
    _, p0 = req_json(f"{base}/api/rooms", method="POST", body={"name": "방장"})
    room_code = p0["roomCode"]
    _, p1 = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": "도전자"})
    for tok in (p0["token"], p1["token"]):
        req_json(f"{base}/api/rooms/{room_code}/ready", method="POST", token=tok, body={"ready": True})

    # 두 플레이어 실시간 스트림 열기
    s0_req = urllib.request.Request(f"{base}/api/rooms/{room_code}/events", headers={"X-Player-Token": p0["token"]})
    s1_req = urllib.request.Request(f"{base}/api/rooms/{room_code}/events", headers={"X-Player-Token": p1["token"]})
    s0 = urllib.request.urlopen(s0_req, timeout=10)
    s1 = urllib.request.urlopen(s1_req, timeout=10)
    try:
        assert read_ndjson_event(s0)["type"] == "state"
        assert read_ndjson_event(s1)["type"] == "state"

        # 유예 시간 내 재접속 시 대타 없이 연결 유지
        s0.close()
        time.sleep(0.08)
        s0 = urllib.request.urlopen(s0_req, timeout=10)
        ev = read_ndjson_event(s0)
        assert ev["state"]["seats"][0]["connected"] is True
        assert ev["state"]["seats"][0]["aiTakeover"] is False

        req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=p0["token"], body={})

        # 방장(동가) 차례에서 연결을 끊고 유예 시간(0.25s)을 넘기면 AI 대타가 대신 두고 p1에게 전파된다.
        s0.close()
        deadline = time.monotonic() + 3.0
        takeover_seen = False
        while time.monotonic() < deadline:
            _, p1_now = req_json(f"{base}/api/rooms/{room_code}", token=p1["token"])
            if p1_now["seats"][0]["aiTakeover"] and p1_now["match"]["revision"] > 1:
                takeover_seen = True
                break
            time.sleep(0.05)
        assert takeover_seen, "disconnect grace expiry did not trigger AI takeover and advance turn"

        # 방장이 기존 토큰으로 재접속하면 AI 대타 해제 및 사람 조작 복귀
        s0 = urllib.request.urlopen(s0_req, timeout=10)
        reconnected = read_ndjson_event(s0)["state"]
        assert reconnected["seats"][0]["connected"] is True
        assert reconnected["seats"][0]["aiTakeover"] is False
    finally:
        s0.close()
        s1.close()


def test_replay_same_wall_hints_and_series_comparison(live_server):
    base, _ = live_server
    _, p0 = req_json(f"{base}/api/rooms", method="POST", body={"name": "동가"})
    room_code = p0["roomCode"]
    _, p1 = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": "남가"})
    players = {0: p0["token"], 1: p1["token"]}
    for tok in players.values():
        req_json(f"{base}/api/rooms/{room_code}/ready", method="POST", token=tok, body={"ready": True})

    st, first_start = req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=p0["token"], body={})
    assert st == 200
    first_hand0 = list(first_start["match"]["table"]["hand"])
    first_draw0 = first_start["match"]["table"]["draw"]
    first_dora = list(first_start["match"]["table"]["dora"])

    # 1경기 완주
    match1_end = _play_match_over_http(base, room_code, players, target_statuses=("MATCH_END",))
    assert match1_end[0]["canStartReplay"] is True
    assert match1_end[1]["canStartReplay"] is False
    assert req_json(f"{base}/api/rooms/{room_code}/hint", method="POST", token=players[0], body={})[0] == 403
    assert req_json(f"{base}/api/rooms/{room_code}/replay", method="POST", token=players[1], body={})[0] == 403

    # 2경기 시작 (중복 호출 시 멱등)
    st, replay_start = req_json(f"{base}/api/rooms/{room_code}/replay", method="POST", token=players[0], body={})
    assert st == 200 and replay_start["matchNumber"] == 2 and replay_start["status"] == "PLAYING"
    st, replay_dup = req_json(f"{base}/api/rooms/{room_code}/replay", method="POST", token=players[0], body={})
    assert st == 200 and replay_dup["match"]["revision"] == replay_start["match"]["revision"]

    # 1경기와 2경기의 동1국 0본장 배패·도라·초기 점수 일치
    t2 = replay_start["match"]["table"]
    assert t2["hand"] == first_hand0
    assert t2["draw"] == first_draw0
    assert t2["dora"] == first_dora
    assert all(p["score"] == 25000 for p in t2["players"])
    assert replay_start["match"]["hintAllowed"] is True

    # 2경기 요청형 타패 추천 및 거부 사례 검증
    rev2 = replay_start["match"]["decision"]["revision"]
    legal_dahai = {a["tile"] for a in replay_start["match"]["decision"]["actions"] if a["type"] == "DAHAI"}
    st, hint = req_json(f"{base}/api/rooms/{room_code}/hint", method="POST", token=players[0], body={"revision": rev2})
    assert st == 200 and hint["revision"] == rev2
    assert hint["bestTile"] in legal_dahai
    assert hint["candidates"][0]["loss"] == 0.0
    assert {c["tile"] for c in hint["candidates"]} == legal_dahai
    # 추천 호출이 방 버전이나 상대 상태를 바꾸지 않는다.
    _, p1_state = req_json(f"{base}/api/rooms/{room_code}", token=players[1])
    assert p1_state["version"] == replay_start["version"]
    assert p1_state["match"]["hintAllowed"] is False
    # 지난 revision 및 대기 좌석 추천 거부
    assert req_json(f"{base}/api/rooms/{room_code}/hint", method="POST", token=players[0], body={"revision": rev2 - 1})[0] == 409
    assert req_json(f"{base}/api/rooms/{room_code}/hint", method="POST", token=players[1], body={"revision": rev2})[0] == 409

    # 2경기 완주 및 두 경기 비교 데이터 검증
    series_end = _play_match_over_http(base, room_code, players, target_statuses=("SERIES_END",))
    r1 = series_end[0]["firstMatchResult"]
    r2 = series_end[0]["secondMatchResult"]
    assert r1 is not None and r2 is not None
    assert sum(r1["scores"]) == 100000 and sum(r2["scores"]) == 100000
    assert sorted(r1["ranks"]) == [1, 2, 3, 4] and sorted(r2["ranks"]) == [1, 2, 3, 4]


def test_solo_gets_hints_from_first_turn_and_finishes_after_one_match(live_server):
    base, _ = live_server
    _, host = req_json(f"{base}/api/rooms", method="POST", body={"name": "솔로"})
    room_url = f"{base}/api/rooms/{host['roomCode']}"
    token = host["token"]

    st, started = req_json(f"{room_url}/start", method="POST", token=token, body={})
    assert st == 200 and started["solo"] is True
    assert [s["kind"] for s in started["seats"]] == ["human", "ai", "ai", "ai"]
    assert started["match"]["hintAllowed"] is True
    revision = started["match"]["decision"]["revision"]
    st, hint = req_json(f"{room_url}/hint", method="POST", token=token, body={"revision": revision})
    assert st == 200 and hint["matchNumber"] == 1 and hint["revision"] == revision
    assert hint["candidates"][0]["loss"] == 0.0

    ended = _play_match_over_http(base, host["roomCode"], {0: token})[0]
    assert ended["status"] == "MATCH_END" and ended["canStartReplay"] is False
    assert sum(ended["firstMatchResult"]["scores"]) == 100000
    assert ended["secondMatchResult"] is None
    st, _ = req_json(f"{room_url}/replay", method="POST", token=token, body={})
    assert st == 403
    st, _ = req_json(f"{room_url}/hint", method="POST", token=token, body={"revision": revision})
    assert st == 409


def test_replay_starts_with_ai_takeover_for_disconnected_player(live_server):
    base, _ = live_server
    _, p0 = req_json(f"{base}/api/rooms", method="POST", body={"name": "방장"})
    room_code = p0["roomCode"]
    _, p1 = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": "이탈자"})
    for tok in (p0["token"], p1["token"]):
        req_json(f"{base}/api/rooms/{room_code}/ready", method="POST", token=tok, body={"ready": True})

    s0 = urllib.request.urlopen(urllib.request.Request(f"{base}/api/rooms/{room_code}/events", headers={"X-Player-Token": p0["token"]}), timeout=10)
    s1 = urllib.request.urlopen(urllib.request.Request(f"{base}/api/rooms/{room_code}/events", headers={"X-Player-Token": p1["token"]}), timeout=10)
    try:
        assert read_ndjson_event(s0)["type"] == "state"
        assert read_ndjson_event(s1)["type"] == "state"
        req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=p0["token"], body={})
        _play_match_over_http(base, room_code, {0: p0["token"], 1: p1["token"]}, target_statuses=("MATCH_END",))

        # 1경기 종료 상태에서 p1이 연결 끊긴 채로 2경기를 시작하면 좌석을 유지한 채 AI 대타로 진행된다.
        s1.close()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            _, st0 = req_json(f"{base}/api/rooms/{room_code}", token=p0["token"])
            if not st0["seats"][1]["connected"]:
                break
            time.sleep(0.05)
        st, replay = req_json(f"{base}/api/rooms/{room_code}/replay", method="POST", token=p0["token"], body={})
        assert st == 200 and replay["seats"][1]["aiTakeover"] is True

        # 이후 p1이 재접속하면 즉시 복귀한다.
        s1 = urllib.request.urlopen(urllib.request.Request(f"{base}/api/rooms/{room_code}/events", headers={"X-Player-Token": p1["token"]}), timeout=10)
        restored = read_ndjson_event(s1)["state"]
        assert restored["seats"][1]["connected"] is True and restored["seats"][1]["aiTakeover"] is False
    finally:
        s0.close()
        s1.close()


def test_real_prism_gpu_room_play(tmp_path):
    if not _CHECKPOINT.exists():
        pytest.skip("Prism checkpoint not found")
    manager = RoomManager(LiveMatchClient(bot_mode="prism", device="gpu0", work_root=tmp_path / "gpu-work"))
    try:
        p0 = manager.create_room("테스터1")
        code = p0["roomCode"]
        p1 = manager.join_room(code, "테스터2")
        manager.set_ready(code, p0["token"], True)
        manager.set_ready(code, p1["token"], True)
        state = manager.start_match(code, p0["token"])
        # 1경기에서 GPU Prism AI 2명과 함께 완주
        players = {0: p0["token"], 1: p1["token"]}
        steps = 0
        while state["status"] != "MATCH_END":
            progressed = False
            for seat, tok in players.items():
                st_obj = manager.get_state(code, tok)
                m = st_obj["match"]
                if m["phase"] == "DECISIONS" and m["decision"] is not None:
                    d = m["decision"]
                    chosen = next((a for a in d["actions"] if a["type"] in ("TSUMO_AGARI", "RON_AGARI")), None)
                    if chosen is None:
                        chosen = next((a for a in d["actions"] if a["type"] in ("DAHAI", "PASS")), d["actions"][0])
                    state = manager.submit_action(code, tok, d["revision"], chosen["code"])
                    progressed = True
            if not progressed:
                st0 = manager.get_state(code, p0["token"])
                if st0["match"]["phase"] == "ROUND_END":
                    rnd = st0["match"]["round"]
                    for tok in players.values():
                        state = manager.confirm_next_round(code, tok, rnd["kyoku"], rnd["honba"])
                    progressed = True
            assert progressed
            steps += 1
            assert steps < 4000

        # 2경기 시작 후 실제 Prism GPU 모델로 타패 추천 후보 가치·손실 검증
        replay = manager.start_replay(code, p0["token"])
        assert replay["matchNumber"] == 2 and replay["match"]["hintAllowed"] is True
        hint = manager.get_discard_hint(code, p0["token"], revision=replay["match"]["decision"]["revision"])
        assert hint["candidates"] and hint["candidates"][0]["loss"] == 0.0
        assert all(c["loss"] >= 0.0 for c in hint["candidates"])
    finally:
        manager.close()


def test_jdk_tool_falls_back_to_java_home_and_path(tmp_path, monkeypatch):
    import os
    import shutil

    monkeypatch.setattr(rooms, "VENDOR", tmp_path / "no-vendor")
    exe = "java.exe" if os.name == "nt" else "java"

    # JAVA_HOME에 실행 파일이 있으면 우선 사용한다.
    home = tmp_path / "jdk21"
    (home / "bin").mkdir(parents=True)
    java_in_home = home / "bin" / exe
    java_in_home.write_text("", encoding="utf-8")
    monkeypatch.setenv("JAVA_HOME", str(home))
    assert rooms._jdk_tool("java") == java_in_home

    # JAVA_HOME이 없으면 PATH(shutil.which)를 사용한다.
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert str(rooms._jdk_tool("java")).endswith(f"usr{os.sep}bin{os.sep}java") or rooms._jdk_tool("java").as_posix() == "/usr/bin/java"

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert rooms._jdk_tool("java") is None
