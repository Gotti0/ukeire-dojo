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
import socket
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

    def _guard(self, api: bool) -> bool:
        """인증과 속도 제한을 통과하면 True. 아니면 응답을 이미 보냈다.

        속도 제한은 API 요청과 인증 실패에만 건다. 처음에는 모든 요청에 걸었다. 그런데 화면 한 번에 패 이미지 수십 장을 받아서
        순간 한도(30)를 넘었고, 외부 접속에서 이미지가 429로 깨졌다. 인증을 통과한 정적 파일은 디스크에서 읽어 주기만 하므로 막을
        이유가 없다. 인증 실패는 계속 세어야 정적 경로를 이용한 암호 대입도 막힌다.
        """
        if not self._authorized():
            if not self.limiter.allow(self.client_address[0]):
                self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "too many requests"}, {"Retry-After": "5"})
            else:
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "password required"},
                    {"WWW-Authenticate": 'Basic realm="ukeire-dojo"'},
                )
            return False
        if api and not self.limiter.allow(self.client_address[0]):
            self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "too many requests"}, {"Retry-After": "5"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not self._guard(api=url.path.startswith("/api/")):
            return
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
        if not self._guard(api=True):
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
            file = (self.static_root / "index.html").resolve()
        if not file.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "web/dist is not built"})
            return
        data = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control(file.relative_to(self.static_root.resolve()).as_posix()))
        self._common_headers()
        self.end_headers()
        self.wfile.write(data)

    log_file: Path | None = None
    _log_lock = threading.Lock()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # 공개해 두면 누가 얼마나 들어오는지 봐야 한다. 기본 형식(시각, IP, 요청 줄, 상태)을 남긴다. 암호는 요청 줄에 없다.
        write_log(self.log_file, f"{self.log_date_time_string()} {self.client_address[0]} {format % args}")


def write_log(path: Path | None, line: str) -> None:
    """stderr에 쓰고, 지정했으면 파일에도 덧붙인다.

    처음에는 serve-public.ps1이 Tee-Object로 파일에 썼다. 그런데 Tee-Object는 파일을 독점으로 열어서, 같은 로그에 덧붙이는 다른
    서버가 있으면 시작부터 실패했다. 그래서 줄마다 덧붙이기 모드로 열고 닫는다. 이 서버의 요청 수에서는 비용이 무시할 만하다.
    """
    sys.stderr.write(line + "\n")
    sys.stderr.flush()
    if path is None:
        return
    with Handler._log_lock, open(path, "a", encoding="utf-8") as log:
        log.write(line + "\n")


class Server(ThreadingHTTPServer):
    """포트를 독점하는 HTTP 서버.

    HTTPServer는 SO_REUSEADDR를 켠다. Windows에서는 이 옵션 때문에 다른 프로세스가 같은 포트에 또 바인드할 수 있다. 배치 파일을 두 번
    실행했을 때 실제로 서버 두 개가 5180을 함께 잡고 연결을 나눠 가졌고, 한쪽이 응답하지 않아 요청이 시간 초과로 끝났다. 그래서
    Windows에서는 재사용을 끄고 SO_EXCLUSIVEADDRUSE를 건다. 두 번째 실행은 "이미 사용 중"으로 바로 실패한다.
    """

    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def cache_control(relative: str) -> str:
    """정적 파일의 캐시 정책. 인증 뒤의 내용이라 공유 캐시(프록시)에는 두지 않는다(private)."""
    if relative.startswith("assets/"):
        # Vite가 내용 해시를 파일 이름에 넣으므로, 내용이 바뀌면 이름도 바뀐다.
        return "private, max-age=31536000, immutable"
    if relative.startswith("tiles/"):
        return "private, max-age=86400"
    # index.html은 새 빌드의 해시 파일 이름을 가리키므로 매번 확인하게 한다.
    return "no-cache"


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "bank.sqlite")
    parser.add_argument("--host", default="127.0.0.1", help="공개하려면 0.0.0.0. 이때는 암호가 필요하다")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--static", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument("--password-file", type=Path, help=f"암호 파일. 없으면 환경 변수 {PASSWORD_ENV}를 읽는다")
    parser.add_argument("--log-file", type=Path, help="접속 기록을 덧붙일 파일(stderr에도 쓴다)")
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
    Handler.log_file = args.log_file
    Handler.picker = ProblemPicker(sqlite3.connect(f"file:{Handler.db_path}?mode=ro", uri=True))
    try:
        server = Server((args.host, args.port), Handler)
    except OSError as e:
        raise SystemExit(f"cannot listen on {args.host}:{args.port} ({e}). Is another server already running?") from e
    write_log(args.log_file, f"serving http://{args.host}:{args.port}  db={args.db}  auth={'on' if password else 'off'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
