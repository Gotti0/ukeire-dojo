import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

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

    # 1인 상태에서는 시작 불가
    st, _ = req_json(f"{base}/api/rooms/{room_code}/ready", method="POST", token=host["token"], body={"ready": True})
    assert st == 200
    st, err = req_json(f"{base}/api/rooms/{room_code}/start", method="POST", token=host["token"], body={})
    assert st == 409 and "2명" in err["error"]

    # 2번째 참가자 입장 및 좌석 이동
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


def test_player_counts_2_3_4_and_seat_isolation(live_server):
    base, _ = live_server
    for human_count in (2, 3, 4):
        _, host = req_json(f"{base}/api/rooms", method="POST", body={"name": "P0"})
        room_code = host["roomCode"]
        players = [host]
        for idx in range(1, human_count):
            _, joined = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": f"P{idx}"})
            players.append(joined)
        if human_count == 4:
            st, _ = req_json(f"{base}/api/rooms/{room_code}/join", method="POST", body={"name": "P4"})
            assert st == 409
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

    # 정상 제출 후 이전 revision 재전송 거부
    st, after = req_json(f"{base}/api/rooms/{room_code}/action", method="POST", token=players[0], body={"revision": rev0, "action": legal0})
    assert st == 200
    assert req_json(f"{base}/api/rooms/{room_code}/action", method="POST", token=players[0], body={"revision": rev0, "action": legal0})[0] == 409

    # 사람 2명 + AI 2명으로 동풍전 1경기 완주
    steps = 0
    while True:
        states = {seat: req_json(f"{base}/api/rooms/{room_code}", token=tok)[1] for seat, tok in players.items()}
        if any(st_obj["status"] == "MATCH_END" for st_obj in states.values()):
            final = states[0]["match"]["settlement"]
            assert sum(final["scores"]) == 100000
            assert sorted(final["ranks"]) == [1, 2, 3, 4]
            break
        progressed = False
        for seat, st_obj in states.items():
            m = st_obj["match"]
            if m["phase"] == "DECISIONS" and m["decision"] is not None:
                d = m["decision"]
                # 화료/패스/타패 순으로 단순 선택
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
            m0 = states[0]["match"]
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
                # 지나간 국 정산 재확인 거부
                assert req_json(
                    f"{base}/api/rooms/{room_code}/next-round",
                    method="POST",
                    token=players[0],
                    body={"kyoku": rnd["kyoku"], "honba": rnd["honba"]},
                )[0] == 409
                progressed = True
        assert progressed, "match stalled with no actionable human"
        steps += 1
        assert steps < 4000


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
        started = manager.start_match(code, p0["token"])
        # 좌석 0(사람)이 첫 타패를 두면 좌석 1(사람)의 차례까지 좌석 2, 3의 Prism GPU AI가 자동으로 진행해야 한다.
        dec0 = started["match"]["decision"]
        assert dec0 is not None
        manager.submit_action(code, p0["token"], dec0["revision"], dec0["actions"][0]["code"])
        s1 = manager.get_state(code, p1["token"])
        assert s1["match"]["phase"] in ("DECISIONS", "ROUND_END")
        if s1["match"]["phase"] == "DECISIONS":
            assert s1["match"]["decision"] is not None
    finally:
        manager.close()
