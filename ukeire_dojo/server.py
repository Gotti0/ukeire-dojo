"""문제은행(SQLite)에서 문제를 내고 타패를 채점하는 HTTP 서버. 표준 라이브러리만 쓴다.

GET  /api/problem?min_loss=0.01&disagree=1&opp_riichi=any&menzen=any   문제 하나(정답 정보 없음)
POST /api/answer {"id": 123, "tile": "5m"}                                 채점 결과
그 밖의 경로는 web/dist(빌드한 화면)의 정적 파일을 준다.

사용(로컬): python -m ukeire_dojo.server --db data/bank.sqlite --port 8765
사용(공개): python -m ukeire_dojo.server --host 0.0.0.0 --port 5173 --password-file data/server-password.txt
공개할 때는 암호(Basic 인증)가 반드시 있어야 하고, IP별 속도 제한과 본문 크기 제한이 걸린다. HTTP라 암호가 평문으로 오가므로
다른 곳에 쓰는 암호를 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import base64
import bisect
import hmac
import json
import mimetypes
import os
import random
import sqlite3
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
WINDS = "ESWN"
MAX_BODY = 4096  # /api/answer의 본문은 수십 바이트다. 큰 본문으로 메모리를 채우는 요청을 막는다.
PASSWORD_ENV = "UKEIRE_DOJO_PASSWORD"


class RateLimiter:
    """IP별 토큰 버킷. 문제를 풀며 누르는 속도(초당 몇 번)는 넉넉히 통과시키고 자동 반복 요청만 막는다."""

    def __init__(self, rate: float = 5.0, burst: int = 30, clock=time.monotonic) -> None:
        self.rate, self.burst, self._clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            # 오래 조용한 IP는 버킷이 다시 가득 찬 것과 같으므로 지워 메모리가 늘지 않게 한다.
            if len(self._buckets) > 10_000:
                full = [k for k, (t, at) in self._buckets.items() if t + (now - at) * self.rate >= self.burst]
                for k in full:
                    del self._buckets[k]
            return True


def _tri(value: str | None) -> bool | None:
    """'1'/'0'/'any'(또는 없음)를 True/False/None으로."""
    if value in (None, "", "any"):
        return None
    if value in ("1", "true"):
        return True
    if value in ("0", "false"):
        return False
    raise ValueError(f"expected 1, 0 or any: {value}")


class ProblemPicker:
    """출제용 요약(id, eff_loss, 분류 3개)만 메모리에 두고 조건에 맞는 문제를 고르게 뽑는다.

    처음에는 SQL의 ORDER BY random()을 썼다. 그런데 인덱스를 못 타는 필터 조합에서 18만 행 기준 요청당 약 0.5초가 걸렸다.
    공개 서버에서 이 정도면 요청 몇 개로 서버를 붙잡을 수 있다. 그래서 (disagree, opp_riichi, menzen) 8칸으로 나누고 칸마다
    eff_loss로 정렬해 두었다. 요청마다 이진 탐색으로 고르고, 시작할 때 한 번 읽는 데 약 0.6초가 걸린다.
    """

    def __init__(self, db: sqlite3.Connection, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._buckets: dict[tuple[int, int, int], tuple[list[float], list[int]]] = {}
        rows = db.execute(
            "SELECT disagree, opp_riichi, menzen, eff_loss, id FROM problems ORDER BY disagree, opp_riichi, menzen, eff_loss"
        ).fetchall()
        for d, o, m, loss, problem_id in rows:
            losses, ids = self._buckets.setdefault((d, o, m), ([], []))
            losses.append(loss)
            ids.append(problem_id)

    def pick(
        self,
        min_loss: float = 0.0,
        disagree: bool | None = None,
        opp_riichi: bool | None = None,
        menzen: bool | None = None,
    ) -> int | None:
        wanted = (disagree, opp_riichi, menzen)
        spans = []
        for key, (losses, ids) in self._buckets.items():
            if all(w is None or int(w) == k for w, k in zip(wanted, key)):
                start = bisect.bisect_left(losses, min_loss)
                if start < len(ids):
                    spans.append((ids, start, len(ids) - start))
        total = sum(size for _, _, size in spans)
        if total == 0:
            return None
        offset = self._rng.randrange(total)
        for ids, start, size in spans:
            if offset < size:
                return ids[start + offset]
            offset -= size
        raise AssertionError("unreachable")


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
    picker: ProblemPicker
    password: str | None = None
    limiter: RateLimiter = RateLimiter()
    # 느리게 보내는 연결이 스레드를 오래 붙잡지 못하게 소켓 읽기에 시간 제한을 둔다.
    timeout = 15
    _local = threading.local()

    def _db(self) -> sqlite3.Connection:
        # ThreadingHTTPServer는 요청마다 스레드를 바꾸므로 연결을 스레드별로 둔다.
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            self._local.db = db
        return db

    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_json(self, status: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._common_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if self.password is None:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            _, _, supplied = base64.b64decode(header[6:], validate=True).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        # 사용자 이름은 보지 않는다. 암호 하나를 지인끼리 나눠 쓰는 용도다. 비교 시간으로 암호가 새지 않게 상수 시간 비교를 쓴다.
        return hmac.compare_digest(supplied.encode(), self.password.encode())

    def _guard(self) -> bool:
        """속도 제한과 인증을 통과하면 True. 아니면 응답을 이미 보냈다."""
        if not self.limiter.allow(self.client_address[0]):
            self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "too many requests"}, {"Retry-After": "5"})
            return False
        if not self._authorized():
            self._send_json(
                HTTPStatus.UNAUTHORIZED, {"error": "password required"}, {"WWW-Authenticate": 'Basic realm="ukeire-dojo"'}
            )
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        url = urlparse(self.path)
        if url.path == "/api/problem":
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                problem_id = self.picker.pick(
                    min_loss=float(query.get("min_loss", 0)),
                    disagree=_tri(query.get("disagree")),
                    opp_riichi=_tri(query.get("opp_riichi")),
                    menzen=_tri(query.get("menzen")),
                )
            except ValueError as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            if problem_id is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no problem matches the filters"})
                return
            row = self._db().execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
            self._send_json(HTTPStatus.OK, public_problem(row))
            return
        self._send_static(url.path)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        if urlparse(self.path).path != "/api/answer":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= MAX_BODY:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": f"body must be 1..{MAX_BODY} bytes"})
                return
            body = json.loads(self.rfile.read(length))
            row = self._db().execute("SELECT * FROM problems WHERE id = ?", (int(body["id"]),)).fetchone()
            if row is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown problem"})
                return
            self._send_json(HTTPStatus.OK, grade(row, str(body["tile"])))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
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
        self._common_headers()
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # 공개해 두면 누가 얼마나 들어오는지 봐야 한다. 기본 형식(시각, IP, 요청 줄, 상태)을 stderr로 남긴다. 암호는 요청 줄에 없다.
        sys.stderr.write(f"{self.log_date_time_string()} {self.client_address[0]} {format % args}\n")


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "bank.sqlite")
    parser.add_argument("--host", default="127.0.0.1", help="공개하려면 0.0.0.0. 이때는 암호가 필요하다")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--static", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument("--password-file", type=Path, help=f"암호 파일. 없으면 환경 변수 {PASSWORD_ENV}를 읽는다")
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"problem bank not found: {args.db}")
    password = (
        args.password_file.read_text(encoding="utf-8").strip() if args.password_file else os.environ.get(PASSWORD_ENV)
    ) or None
    if not is_loopback(args.host) and password is None:
        # 지인용 공개가 목적이므로, 암호 없이 외부 주소로 여는 실수를 시작 단계에서 막는다.
        raise SystemExit(f"refusing to listen on {args.host} without a password (--password-file or {PASSWORD_ENV})")
    if password is not None and len(password) < 12:
        raise SystemExit("password must be at least 12 characters")
    Handler.db_path = args.db.resolve()
    Handler.static_root = args.static.resolve()
    Handler.password = password
    Handler.picker = ProblemPicker(sqlite3.connect(f"file:{Handler.db_path}?mode=ro", uri=True))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(
        f"serving http://{args.host}:{args.port}  db={args.db}  auth={'on' if password else 'off'}",
        file=sys.stderr,
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
