"""문제은행(SQLite)에서 문제를 내고 타패를 채점하는 HTTP 서버. 표준 라이브러리만 쓴다.

GET  /api/problem?min_loss=0.01&disagree=1&opp_riichi=any&menzen=any   문제 하나(정답 정보 없음)
POST /api/answer {"id": 123, "tile": "5m"}                                 채점 결과
브라우저는 출제 응답의 attempt_id를 채점 요청에 보내 풀이를 기록한다.
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
import math
import mimetypes
import os
import random
import re
import secrets
import select
import socket
import sqlite3
import sys
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

from ukeire_dojo.chart import render_history_chart_svg
from ukeire_dojo.practice import MISTAKE_LOSS, Practice, practice_weights, problem_kind
from ukeire_dojo.rooms import LiveMatchClient, RoomError, RoomManager

ROOT = Path(__file__).resolve().parents[1]
WINDS = "ESWN"
MAX_BODY = 4096  # /api/answer의 본문은 수십 바이트다. 큰 본문으로 메모리를 채우는 요청을 막는다.
PASSWORD_ENV = "UKEIRE_DOJO_PASSWORD"
PROFILE_COOKIE = "ukeire_profile"
PRACTICE_TOKEN = re.compile(r"[0-9a-f]{32}")
ROOM_EVENTS_PATH = re.compile(r"^/api/rooms/([A-Za-z0-9]+)/events$")
ROOM_STATE_PATH = re.compile(r"^/api/rooms/([A-Za-z0-9]+)$")
ROOM_ACTION_PATH = re.compile(r"^/api/rooms/([A-Za-z0-9]+)/(join|seat|ready|start|replay|hint|action|next-round|leave)$")


class RateLimiter:
    """IP별 토큰 버킷. 여러 명이 같은 공유기에서 대국·풀이하는 속도는 넉넉히 통과시키고 자동 반복 요청만 막는다."""

    def __init__(self, rate: float = 20.0, burst: int = 100, clock=time.monotonic) -> None:
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
        weights: dict[str, float] | None = None,
    ) -> int | None:
        wanted = (disagree, opp_riichi, menzen)
        spans = []
        for key, (losses, ids) in self._buckets.items():
            if all(w is None or int(w) == k for w, k in zip(wanted, key)):
                start = bisect.bisect_left(losses, min_loss)
                if start < len(ids):
                    weight = weights[problem_kind(key[1], key[2])] if weights else 1
                    spans.append((ids, start, len(ids) - start, weight))
        if not spans:
            return None
        ids, start, size, _ = self._rng.choices(
            spans, weights=[size * weight for _, _, size, weight in spans],
        )[0]
        return ids[start + self._rng.randrange(size)]


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


def render_badge_svg(label: str, value: str, color: str) -> bytes:
    """shields.io 스타일의 20px 높이 SVG 배지를 생성한다."""
    label_w = max(46, len(label) * 7 + 14)
    value_w = max(36, len(value) * 7 + 14)
    total_w = label_w + value_w
    esc_label = escape(label)
    esc_value = escape(value)
    esc_color = escape(color)
    title = escape(f"{label}: {value}")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="20" role="img" aria-label="{title}">'
        f"<title>{title}</title>"
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/>'
        f"</linearGradient>"
        f'<clipPath id="r"><rect width="{total_w}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{label_w}" height="20" fill="#555"/>'
        f'<rect x="{label_w}" width="{value_w}" height="20" fill="{esc_color}"/>'
        f'<rect width="{total_w}" height="20" fill="url(#s)"/>'
        f"</g>"
        f'<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        f'text-rendering="geometricPrecision" font-size="11">'
        f'<text x="{label_w / 2:.1f}" y="14">{esc_label}</text>'
        f'<text x="{label_w + value_w / 2:.1f}" y="14">{esc_value}</text>'
        f"</g>"
        f"</svg>"
    )
    return svg.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    db_path: Path
    static_root: Path
    picker: ProblemPicker
    practice: Practice | None = None
    rooms: RoomManager | None = None
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

    def _profile(self) -> str | None:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", "")).get(PROFILE_COOKIE)
        except CookieError:
            return None
        return cookie.value if cookie and PRACTICE_TOKEN.fullmatch(cookie.value) else None

    def _player_token(self) -> str | None:
        token = (self.headers.get("X-Player-Token") or "").strip()
        return token if PRACTICE_TOKEN.fullmatch(token) else None

    def _peer_closed(self) -> bool:
        try:
            sock = self.connection
            if sock.fileno() == -1:
                return True
            readable, _, _ = select.select([sock], [], [], 0)
            if not readable:
                return False
            data = sock.recv(1, socket.MSG_PEEK)
            return data == b""
        except BlockingIOError:
            return False
        except OSError:
            return True

    def _read_json_body(self, *, allow_empty: bool = False) -> dict | None:
        raw_len = self.headers.get("Content-Length")
        length = int(raw_len) if raw_len is not None else 0
        if length == 0 and allow_empty:
            return {}
        if not 0 < length <= MAX_BODY:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": f"body must be 1..{MAX_BODY} bytes"})
            return None
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("JSON object expected")
        return body

    def _send_room_events(self, code: str) -> None:
        if self.rooms is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "멀티플레이 방 기능이 비활성화되어 있습니다."})
            return
        token = self._player_token()
        try:
            events = self.rooms.subscribe(code, token, self._peer_closed)
            first_event = next(events)
        except RoomError as e:
            self._send_json(e.status, {"error": e.message})
            return
        except StopIteration:
            return
        try:
            self.connection.settimeout(None)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.close_connection = True
            self._common_headers()
            self.end_headers()

            def write(event: dict) -> None:
                self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()

            write(first_event)
            for event in events:
                write(event)
        except ConnectionError:
            pass
        finally:
            events.close()

    def _handle_room_post(self, path: str) -> None:
        if self.rooms is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "멀티플레이 방 기능이 비활성화되어 있습니다."})
            return
        try:
            body = self._read_json_body(allow_empty=True)
            if body is None:
                return
            token = self._player_token()
            if path == "/api/rooms":
                self._send_json(HTTPStatus.CREATED, self.rooms.create_room(body.get("name")))
                return
            match = ROOM_ACTION_PATH.match(path)
            if not match:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            code, action = match.groups()
            if action == "join":
                self._send_json(HTTPStatus.OK, self.rooms.join_room(code, body.get("name"), existing_token=token))
            elif action == "seat":
                self._send_json(HTTPStatus.OK, self.rooms.choose_seat(code, token, int(body["seat"])))
            elif action == "ready":
                self._send_json(HTTPStatus.OK, self.rooms.set_ready(code, token, bool(body["ready"])))
            elif action == "start":
                self._send_json(HTTPStatus.OK, self.rooms.start_match(code, token))
            elif action == "replay":
                self._send_json(HTTPStatus.OK, self.rooms.start_replay(code, token))
            elif action == "hint":
                revision = int(body["revision"]) if body.get("revision") is not None else None
                self._send_json(HTTPStatus.OK, self.rooms.get_discard_hint(code, token, revision=revision))
            elif action == "action":
                self._send_json(
                    HTTPStatus.OK,
                    self.rooms.submit_action(code, token, int(body["revision"]), int(body["action"])),
                )
            elif action == "next-round":
                self._send_json(
                    HTTPStatus.OK,
                    self.rooms.confirm_next_round(code, token, int(body["kyoku"]), int(body["honba"])),
                )
            elif action == "leave":
                self._send_json(HTTPStatus.OK, self.rooms.leave_room(code, token))
        except RoomError as e:
            self._send_json(e.status, {"error": e.message})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not self._guard(api=url.path.startswith("/api/")):
            return
        if m := ROOM_EVENTS_PATH.match(url.path):
            self._send_room_events(m.group(1))
            return
        if m := ROOM_STATE_PATH.match(url.path):
            if self.rooms is None:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "멀티플레이 방 기능이 비활성화되어 있습니다."})
                return
            try:
                state = self.rooms.get_state(m.group(1), self._player_token())
            except RoomError as e:
                self._send_json(e.status, {"error": e.message})
                return
            self._send_json(HTTPStatus.OK, state)
            return
        if url.path == "/api/stats":
            stats = self._global_stats()
            if stats is not None:
                self._send_json(HTTPStatus.OK, stats)
            return
        if url.path == "/api/stats/chart.svg":
            payload = self._stats_history()
            if payload is None:
                return
            history, totals = payload
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            svg = render_history_chart_svg(
                history,
                metric=query.get("metric", "visitors"),
                theme=query.get("theme", "light"),
                totals=totals,
            )
            self._send_svg(svg)
            return
        if url.path in ("/api/badge/visitors.svg", "/api/badge/solved.svg"):
            stats = self._global_stats()
            if stats is None:
                return
            if url.path.endswith("visitors.svg"):
                svg = render_badge_svg("visitors", f"{stats['visitors']:,}", "#2f5d50")
            else:
                svg = render_badge_svg("solved", f"{stats['answered']:,}", "#2b62c4")
            self._send_svg(svg)
            return
        if url.path == "/api/problem":
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                min_loss = float(query.get("min_loss", 0))
                if not math.isfinite(min_loss):
                    raise ValueError("min_loss must be finite")
                adaptive = _tri(query.get("adaptive", "0"))
                profile = self._profile()
                summary = self.practice.summary(profile) if self.practice else None
                if self.practice is not None and profile is not None:
                    self.practice.touch_visitor(profile)
                problem_id = self.picker.pick(
                    min_loss=min_loss,
                    disagree=_tri(query.get("disagree")),
                    opp_riichi=_tri(query.get("opp_riichi")),
                    menzen=_tri(query.get("menzen")),
                    weights=practice_weights(summary) if adaptive and summary else None,
                )
            except ValueError as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return
            except sqlite3.Error as e:
                self.log_error("practice storage unavailable: %s", e)
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "풀이 기록을 읽을 수 없습니다."})
                return
            if problem_id is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no problem matches the filters"})
                return
            row = self._db().execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
            problem = public_problem(row)
            headers = {}
            if summary is not None:
                problem.update(attempt_id=secrets.token_hex(16), practice=summary)
                if profile is None:
                    headers["Set-Cookie"] = (
                        f"{PROFILE_COOKIE}={secrets.token_hex(16)}; Max-Age=31536000; Path=/api; HttpOnly; SameSite=Strict"
                    )
            self._send_json(HTTPStatus.OK, problem, headers)
            return
        self._send_static(url.path)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard(api=True):
            return
        path = urlparse(self.path).path
        if path == "/api/rooms" or ROOM_ACTION_PATH.match(path):
            self._handle_room_post(path)
            return
        if path != "/api/answer":
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
            result = grade(row, str(body["tile"]))
            if self.practice and "attempt_id" in body:
                profile = self._profile()
                attempt = body["attempt_id"]
                if not isinstance(attempt, str) or not PRACTICE_TOKEN.fullmatch(attempt) or profile is None:
                    raise ValueError("invalid attempt_id or missing practice cookie")
                kind = problem_kind(row["opp_riichi"], row["menzen"])
                saved_id, saved_tile = self.practice.record(
                    profile, attempt, row["id"], kind, result["tile"], result["chosen_ai_loss"],
                )
                if saved_id != row["id"]:
                    self._send_json(HTTPStatus.CONFLICT, {"error": "attempt already used for another problem"})
                    return
                if saved_tile != result["tile"]:
                    result = grade(row, saved_tile)
                result["practice"] = self.practice.summary(profile)
                result["mistake_kind"] = kind if result["chosen_ai_loss"] >= MISTAKE_LOSS else None
            self._send_json(HTTPStatus.OK, result)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
        except sqlite3.Error as e:
            self.log_error("answer storage unavailable: %s", e)
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "풀이 기록을 저장할 수 없습니다."})

    def _send_svg(self, svg: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(svg)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self._common_headers()
        self.end_headers()
        self.wfile.write(svg)

    def _stats_history(self) -> tuple[list[dict[str, int | str]], dict[str, int]] | None:
        if self.practice is None:
            return ([], {"visitors": 0, "answered": 0})
        try:
            return self.practice.daily_history(), self.practice.global_stats()
        except sqlite3.Error as e:
            self.log_error("stats history unavailable: %s", e)
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "통계를 읽을 수 없습니다."})
            return None

    def _global_stats(self) -> dict[str, int] | None:
        if self.practice is None:
            return {"visitors": 0, "answered": 0}
        try:
            return self.practice.global_stats()
        except sqlite3.Error as e:
            self.log_error("stats storage unavailable: %s", e)
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "통계를 읽을 수 없습니다."})
            return None

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
    parser.add_argument("--practice-db", type=Path, help="풀이 기록 DB. 기본값은 문제은행 옆 practice.sqlite")
    parser.add_argument("--host", default="127.0.0.1", help="공개하려면 0.0.0.0. 이때는 암호가 필요하다")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--static", type=Path, default=ROOT / "web" / "dist")
    parser.add_argument("--password-file", type=Path, help=f"암호 파일. 없으면 환경 변수 {PASSWORD_ENV}를 읽는다")
    parser.add_argument("--log-file", type=Path, help="접속 기록을 덧붙일 파일(stderr에도 쓴다)")
    parser.add_argument("--live-bot", choices=("prism", "scripted"), default="prism")
    parser.add_argument("--live-device", default="gpu0")
    parser.add_argument("--disconnect-grace", type=float, default=15.0)
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
    practice_path = (args.practice_db or Handler.db_path.with_name("practice.sqlite")).resolve()
    if practice_path == Handler.db_path or (practice_path.exists() and practice_path.samefile(Handler.db_path)):
        raise SystemExit("practice database must be separate from the problem bank")
    Handler.practice = Practice(practice_path)
    Handler.picker = ProblemPicker(sqlite3.connect(f"file:{Handler.db_path}?mode=ro", uri=True))
    Handler.rooms = RoomManager(
        LiveMatchClient(bot_mode=args.live_bot, device=args.live_device),
        disconnect_grace_seconds=args.disconnect_grace,
    )
    try:
        server = Server((args.host, args.port), Handler)
    except OSError as e:
        Handler.rooms.close()
        raise SystemExit(f"cannot listen on {args.host}:{args.port} ({e}). Is another server already running?") from e
    write_log(args.log_file, f"serving http://{args.host}:{args.port}  db={args.db}  auth={'on' if password else 'off'}")
    try:
        server.serve_forever()
    finally:
        Handler.rooms.close()


if __name__ == "__main__":
    main()
