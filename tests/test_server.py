import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ukeire_dojo.bank import SCHEMA
from ukeire_dojo.server import Handler, ProblemPicker, RateLimiter, grade, public_problem


def player(hand, draw=None, riichi=False, river=(), melds=()):
    return {"score": 25000, "riichi": riichi, "hand": list(hand), "draw": draw, "river": list(river), "melds": list(melds)}


STATE = {
    "players": [
        player(["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "E", "E", "5s", "6s"], draw="C"),
        player(["9m"] * 13, riichi=True, river=[{"tile": "N"}, {"tile": "1p", "riichi": True}]),
        player(["9p"] * 13),
        player(["9s"] * 13),
    ],
    "dora": ["4m"],
    "honba": 1,
    "kyotaku": 1,
    "remaining": 50,
    "activeSeat": 0,
}
EFFICIENCY = {
    "C": {"shanten": 0, "ukeire": 7, "tiles": ["4s", "7s"]},
    "E": {"shanten": 1, "ukeire": 20, "tiles": ["4s"]},
    "5s": {"shanten": 1, "ukeire": 30, "tiles": ["E"]},
}
AI = {
    "C": {"dahai": 0.02, "riichi": 0.03},
    "E": {"dahai": 0.0, "riichi": None},
    "5s": {"dahai": 0.1, "riichi": None},
}


def insert(db, *, ai_best, eff_best, disagree, eff_loss, opp_riichi=1, menzen=1):
    db.execute("INSERT OR IGNORE INTO sources VALUES ('t', 1, 1, 1)")
    cursor = db.execute(
        """INSERT INTO problems (source, round_index, honba, step, seat, junme, menzen, opp_riichi, shanten, hand, state,
             efficiency, ai, eff_best, ai_best, ai_best_riichi, disagree, eff_loss)
           VALUES ('t', 4, 1, (SELECT COUNT(*) FROM problems), 0, 7, ?, ?, 0, '[]', ?, ?, ?, ?, ?, 0, ?, ?)""",
        (menzen, opp_riichi, json.dumps(STATE), json.dumps(EFFICIENCY), json.dumps(AI), json.dumps(eff_best),
         ai_best, disagree, eff_loss),
    )
    db.commit()
    return cursor.lastrowid


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "bank.sqlite"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.row_factory = sqlite3.Row
    yield db
    db.close()


def test_public_problem_hides_hands_and_answers(db):
    problem_id = insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.02)
    row = db.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
    public = public_problem(row)
    text = json.dumps(public)
    assert public["hand"] == STATE["players"][0]["hand"] and public["draw"] == "C"
    assert public["round"] == "S1" and public["dealer"] == 0
    assert all("hand" not in p for p in public["players"]), "상대 손패가 새면 안 된다"
    assert "9m" not in text, "리치한 상대의 손패(9m)가 응답 어디에도 없어야 한다"
    for leaked in ("ai_best", "eff_best", "eff_loss", "efficiency", "ai"):
        assert leaked not in public
    assert public["players"][1]["concealed"] == 13
    assert public["legal"] == ["5s", "C", "E"]


def test_grade_ranks_by_ai_loss(db):
    problem_id = insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.02)
    row = db.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
    result = grade(row, "C")
    assert [r["tile"] for r in result["table"]] == ["E", "C", "5s"]
    assert result["chosen_ai_loss"] == 0.02  # 타패 0.02와 리치 0.03 중 작은 쪽
    assert result["matches_efficiency"] and not result["matches_ai"]
    assert grade(row, "E")["matches_ai"]
    with pytest.raises(ValueError):
        grade(row, "9m")


def test_grade_accepts_red_five(db):
    problem_id = insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.02)
    row = db.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
    assert grade(row, "5sr")["tile"] == "5s"


def test_picker_filters(db):
    agree = insert(db, ai_best="C", eff_best=["C"], disagree=0, eff_loss=0.0, opp_riichi=0)
    hard = insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.05, opp_riichi=1, menzen=0)
    picker = ProblemPicker(db)
    assert picker.pick(disagree=False) == agree
    assert picker.pick(min_loss=0.01) == hard
    assert picker.pick(opp_riichi=True, menzen=False) == hard
    assert picker.pick(min_loss=0.05) == hard, "min_loss는 이상(>=)이다"
    assert picker.pick(min_loss=0.5) is None


def test_picker_is_uniform_over_matches(db):
    import random

    ids = [insert(db, ai_best="E", eff_best=["C"], disagree=d, eff_loss=0.01 * i, opp_riichi=d)
           for i in range(1, 7) for d in (0, 1)]
    picker = ProblemPicker(db, rng=random.Random(0))
    counts = {i: 0 for i in ids}
    for _ in range(12000):
        counts[picker.pick(min_loss=0.0)] += 1
    # 칸(분류)이 달라도 문제마다 같은 확률로 뽑혀야 한다. 기대 1000회, 허용 ±15%.
    assert all(850 <= c <= 1150 for c in counts.values()), counts


def test_rate_limiter_refills():
    now = [0.0]
    limiter = RateLimiter(rate=2.0, burst=3, clock=lambda: now[0])
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b"), "IP마다 버킷이 따로다"
    now[0] += 0.5  # 0.5초에 1개 찬다
    assert limiter.allow("a") and not limiter.allow("a")


@pytest.fixture
def server(db, tmp_path):
    insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.02)
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    Handler.db_path = tmp_path / "bank.sqlite"
    Handler.static_root = static
    Handler.picker = ProblemPicker(db)
    Handler.password = None
    Handler.limiter = RateLimiter(rate=1000, burst=1000)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    Handler.password = None
    Handler.limiter = RateLimiter()


def get(url):
    with urllib.request.urlopen(url) as response:
        return response.status, response.read()


def test_http_round_trip(server):
    status, body = get(f"{server}/api/problem?min_loss=0.01&disagree=1")
    problem = json.loads(body)
    assert status == 200 and "ai_best" not in problem
    request = urllib.request.Request(
        f"{server}/api/answer", data=json.dumps({"id": problem["id"], "tile": "E"}).encode(), method="POST"
    )
    with urllib.request.urlopen(request) as response:
        assert json.loads(response.read())["matches_ai"] is True
    with pytest.raises(urllib.error.HTTPError) as missing:
        get(f"{server}/api/problem?min_loss=0.9")
    assert missing.value.code == 404


def test_static_cannot_escape_root(server):
    status, body = get(f"{server}/../secret.txt")
    assert status == 200 and b"secret" not in body
    status, body = get(f"{server}/%2e%2e/secret.txt")
    assert b"secret" not in body


def auth_header(password):
    import base64

    return {"Authorization": "Basic " + base64.b64encode(f"anyone:{password}".encode()).decode()}


def status_of(url, headers=None, data=None, method=None):
    request = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def test_password_required_when_set(server):
    Handler.password = "correct horse battery"
    code, headers = status_of(f"{server}/api/problem")
    assert code == 401 and headers["WWW-Authenticate"].startswith("Basic")
    assert status_of(f"{server}/", auth_header("wrong password!!"))[0] == 401
    assert status_of(f"{server}/", {"Authorization": "Basic !!!notbase64"})[0] == 401
    assert status_of(f"{server}/api/problem", auth_header("correct horse battery"))[0] == 200
    assert status_of(f"{server}/", auth_header("correct horse battery"))[0] == 200


def test_rate_limit_returns_429(server):
    Handler.limiter = RateLimiter(rate=0.001, burst=2)
    codes = [status_of(f"{server}/api/problem")[0] for _ in range(3)]
    assert codes == [200, 200, 429]


def test_body_size_limit(server):
    big = json.dumps({"id": 1, "tile": "E", "pad": "x" * 5000}).encode()
    assert status_of(f"{server}/api/answer", data=big, method="POST")[0] == 413
    assert status_of(f"{server}/api/answer", data=b"[1, 2]", method="POST")[0] == 400


def test_security_headers(server):
    code, headers = status_of(f"{server}/api/problem")
    assert code == 200 and headers["X-Content-Type-Options"] == "nosniff" and headers["Cache-Control"] == "no-store"


def test_refuses_public_bind_without_password(tmp_path, monkeypatch):
    import sys

    from ukeire_dojo import server as module

    bank = tmp_path / "bank.sqlite"
    sqlite3.connect(bank).executescript(SCHEMA)
    monkeypatch.delenv(module.PASSWORD_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["server", "--db", str(bank), "--host", "0.0.0.0", "--port", "0"])
    with pytest.raises(SystemExit, match="without a password"):
        module.main()
    monkeypatch.setenv(module.PASSWORD_ENV, "short")
    with pytest.raises(SystemExit, match="at least 12"):
        module.main()


def test_static_files_skip_rate_limit_but_failed_auth_does_not(server):
    """화면 한 번에 패 이미지 수십 장을 받으므로 인증된 정적 파일은 속도 제한에서 뺀다. 인증 실패는 계속 센다."""
    Handler.password = "correct horse battery"
    Handler.limiter = RateLimiter(rate=0.001, burst=3)
    ok = auth_header("correct horse battery")
    assert [status_of(f"{server}/", ok)[0] for _ in range(10)] == [200] * 10
    assert [status_of(f"{server}/api/problem", ok)[0] for _ in range(4)] == [200, 200, 200, 429]
    Handler.limiter = RateLimiter(rate=0.001, burst=3)
    bad = auth_header("wrong password!!")
    assert [status_of(f"{server}/", bad)[0] for _ in range(4)] == [401, 401, 401, 429]


def test_cache_headers():
    from ukeire_dojo.server import cache_control

    assert "immutable" in cache_control("assets/index-B7aVWNGr.js")
    assert cache_control("tiles/1m.png") == "private, max-age=86400"
    assert cache_control("index.html") == "no-cache"
