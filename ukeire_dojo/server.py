"""문제은행(SQLite)에서 문제를 내고 타패를 채점하는 로컬 HTTP 서버. 표준 라이브러리만 쓴다.

GET  /api/problem?min_loss=0.01&disagree=1&opp_riichi=any&menzen=any   문제 하나(정답 정보 없음)
POST /api/answer {"id": 123, "tile": "5m"}                                 채점 결과
그 밖의 경로는 web/dist(빌드한 화면)의 정적 파일을 준다.

사용: python -I -m ukeire_dojo.server --db data/bank.sqlite --port 8765
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
WINDS = "ESWN"


def _tri(value: str | None) -> bool | None:
    """'1'/'0'/'any'(또는 없음)를 True/False/None으로."""
    if value in (None, "", "any"):
        return None
    if value in ("1", "true"):
        return True
    if value in ("0", "false"):
        return False
    raise ValueError(f"expected 1, 0 or any: {value}")


def pick_problem(
    db: sqlite3.Connection,
    min_loss: float = 0.0,
    disagree: bool | None = None,
    opp_riichi: bool | None = None,
    menzen: bool | None = None,
) -> sqlite3.Row | None:
    where, params = ["eff_loss >= ?"], [min_loss]
    for column, value in (("disagree", disagree), ("opp_riichi", opp_riichi), ("menzen", menzen)):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(int(value))
    # ORDER BY random()는 조건에 맞는 행을 모두 훑지만, 18만 행에서도 수십 ms라 인덱스용 난수 열을 따로 두지 않는다.
    return db.execute(
        f"SELECT * FROM problems WHERE {' AND '.join(where)} ORDER BY random() LIMIT 1", params
    ).fetchone()


def public_problem(row: sqlite3.Row) -> dict:
    """화면에 보낼 문제. 상대 손패와 정답(효율·AI 값)은 넣지 않는다."""
    state = json.loads(row["state"])
    seat = row["seat"]
    me = state["players"][seat]
    players = []
    for index, player in enumerate(state["players"]):
        players.append(
            {
                "seat": index,
                "score": player["score"],
                "riichi": player["riichi"],
                "river": player["river"],
                "melds": player["melds"],
                "concealed": len(player["hand"]) + (player["draw"] is not None),
            }
        )
    round_index = row["round_index"]
    return {
        "id": row["id"],
        "round": f"{WINDS[round_index // 4]}{round_index % 4 + 1}",
        "honba": row["honba"],
        "kyotaku": state["kyotaku"],
        "dealer": round_index % 4,
        "seat": seat,
        "junme": row["junme"],
        "remaining": state["remaining"],
        "dora": state["dora"],
        "hand": me["hand"],
        "draw": me["draw"],
        "players": players,
        "legal": sorted(json.loads(row["efficiency"])),
    }


def grade(row: sqlite3.Row, tile: str) -> dict:
    """고른 패(종류, 적5는 r을 떼고)를 효율 기준과 AI 기준으로 채점한다."""
    tile = tile.rstrip("r")
    efficiency = json.loads(row["efficiency"])
    ai = json.loads(row["ai"])
    if tile not in efficiency:
        raise ValueError(f"not a legal discard: {tile}")

    def loss(entry: dict) -> float:
        return min(v for v in (entry["dahai"], entry["riichi"]) if v is not None)

    table = sorted(
        (
            {
                "tile": t,
                "shanten": efficiency[t]["shanten"],
                "ukeire": efficiency[t]["ukeire"],
                "waits": efficiency[t]["tiles"],
                "ai_dahai": ai[t]["dahai"],
                "ai_riichi": ai[t]["riichi"],
                "ai_loss": loss(ai[t]),
            }
            for t in efficiency
        ),
        key=lambda r: (r["ai_loss"], r["shanten"], -r["ukeire"]),
    )
    eff_best = json.loads(row["eff_best"])
    chosen = next(r for r in table if r["tile"] == tile)
    return {
        "id": row["id"],
        "tile": tile,
        "ai_best": row["ai_best"],
        "ai_best_riichi": bool(row["ai_best_riichi"]),
        "eff_best": eff_best,
        "chosen_ai_loss": chosen["ai_loss"],
        "eff_loss": row["eff_loss"],
        "matches_ai": tile == row["ai_best"],
        "matches_efficiency": tile in eff_best,
        "table": table,
        "source": {"record": row["source"], "round_index": row["round_index"], "honba": row["honba"], "step": row["step"]},
    }


class Handler(BaseHTTPRequestHandler):
    db_path: Path
    static_root: Path
    _local = threading.local()

    def _db(self) -> sqlite3.Connection:
        # ThreadingHTTPServer는 요청마다 스레드를 바꾸므로 연결을 스레드별로 둔다.
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            self._local.db = db
        return db

    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/api/problem":
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                row = pick_problem(
                    self._db(),
                    min_loss=float(query.get("min_loss", 0)),
                    disagree=_tri(query.get("disagree")),
                    opp_riichi=_tri(query.get("opp_riichi")),
                    menzen=_tri(query.get("menzen")),
                )
            except ValueError as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            if row is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no problem matches the filters"})
                return
            self._send_json(HTTPStatus.OK, public_problem(row))
            return
        self._send_static(url.path)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/answer":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            row = self._db().execute("SELECT * FROM problems WHERE id = ?", (int(body["id"]),)).fetchone()
            if row is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown problem"})
                return
            self._send_json(HTTPStatus.OK, grade(row, str(body["tile"])))
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})

    def _send_static(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        file = (self.static_root / relative).resolve()
        # 경로 조작(../)으로 static_root 밖을 읽지 못하게 한다.
        if self.static_root.resolve() not in file.parents or not file.is_file():
            file = self.static_root / "index.html"
        if not file.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "web/dist is not built"})
            return
        data = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "bank.sqlite")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--static", type=Path, default=ROOT / "web" / "dist")
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"problem bank not found: {args.db}")
    Handler.db_path = args.db.resolve()
    Handler.static_root = args.static.resolve()
    # 개인용이라 localhost에만 연다.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving http://127.0.0.1:{args.port}  db={args.db}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
