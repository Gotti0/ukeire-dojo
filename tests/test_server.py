import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ukeire_dojo.bank import SCHEMA
from ukeire_dojo.server import Handler, grade, pick_problem, public_problem


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


def test_pick_problem_filters(db):
    agree = insert(db, ai_best="C", eff_best=["C"], disagree=0, eff_loss=0.0, opp_riichi=0)
    hard = insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.05, opp_riichi=1, menzen=0)
    assert pick_problem(db, disagree=False)["id"] == agree
    assert pick_problem(db, min_loss=0.01)["id"] == hard
    assert pick_problem(db, opp_riichi=True, menzen=False)["id"] == hard
    assert pick_problem(db, min_loss=0.5) is None


@pytest.fixture
def server(db, tmp_path):
    insert(db, ai_best="E", eff_best=["C"], disagree=1, eff_loss=0.02)
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    Handler.db_path = tmp_path / "bank.sqlite"
    Handler.static_root = static
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


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
