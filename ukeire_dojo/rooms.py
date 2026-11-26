"""실시간 멀티플레이 동풍전 방 관리자와 Java 대국 호스트 클라이언트."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
LIVE_MATCH_DIR = ROOT / "tools" / "live-match"
LIVE_MATCH_CLASSES = ROOT / "data" / "live-match" / "classes"
_REVIEWER_JAR = VENDOR / "epsilon-src" / "epsilon-reviewer" / "backend" / "target" / "epsilon-reviewer-1.0-SNAPSHOT-all.jar"
_CHECKPOINT = VENDOR / "epsilon-release" / "epsilon-prism-v760"
_LIBTORCH_CU128 = Path.home() / ".djl.ai" / "pytorch" / "2.11.0-cu128-win-x86_64"

_ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MAX_PLAYERS = 4
_MIN_HUMANS = 2
_MAX_NAME_LEN = 20
_DEFAULT_PING_SECONDS = 10.0


class RoomError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def ensure_live_match_compiled() -> Path:
    """tools/live-match Java 소스가 클래스보다 새로우면 컴파일한다."""
    sources = sorted((LIVE_MATCH_DIR / "src").rglob("*.java"))
    if not sources:
        raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, "실시간 대국 Java 소스를 찾을 수 없습니다.")
    target_class = LIVE_MATCH_CLASSES / "ukeire_dojo" / "live" / "LiveMatchHost.class"
    newest_src = max(p.stat().st_mtime for p in sources)
    if target_class.is_file() and target_class.stat().st_mtime >= newest_src:
        return LIVE_MATCH_CLASSES
    javac = VENDOR / "jdk" / "bin" / "javac.exe"
    if not javac.is_file() or not _REVIEWER_JAR.is_file():
        raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, "실시간 대국 JDK 또는 리뷰어 jar가 준비되지 않았습니다.")
    LIVE_MATCH_CLASSES.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            str(javac),
            "--release",
            "21",
            "-encoding",
            "UTF-8",
            "-cp",
            str(_REVIEWER_JAR),
            "-d",
            str(LIVE_MATCH_CLASSES),
            *(str(s) for s in sources),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, f"실시간 대국 컴파일 실패: {proc.stderr.strip()}")
    return LIVE_MATCH_CLASSES


class LiveMatchClient:
    """상주 LiveMatchHost Java 프로세스를 지연 시작하고 JSON-lines로 통신한다."""

    def __init__(
        self,
        *,
        bot_mode: str = "prism",
        device: str = "gpu0",
        work_root: Path = ROOT / "data" / "live-match",
        checkpoint: Path = _CHECKPOINT,
    ) -> None:
        if bot_mode not in ("prism", "scripted"):
            raise ValueError(f"unsupported bot_mode: {bot_mode}")
        self.bot_mode = bot_mode
        self.device = device
        self.work_root = work_root
        self.checkpoint = checkpoint
        self._lock = threading.Lock()
        self._next_id = 1
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr = None
        self._work: Path | None = None
        self._closed = False

    def _ensure_started(self) -> None:
        if self._closed:
            raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, "대국 엔진이 종료되었습니다.")
        if self._proc is not None and self._proc.poll() is None:
            return
        classes = ensure_live_match_compiled()
        java = VENDOR / "jdk" / "bin" / "java.exe"
        if not java.is_file():
            raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, "JDK를 찾을 수 없습니다.")
        if self.bot_mode == "prism" and not self.checkpoint.exists():
            raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, "Prism 체크포인트를 찾을 수 없습니다.")
        self.work_root.mkdir(parents=True, exist_ok=True)
        self._work = Path(tempfile.mkdtemp(prefix="host-", dir=self.work_root))
        tmp_dir = self._work / "tmp"
        tmp_dir.mkdir()
        env = dict(os.environ)
        args = [self.bot_mode]
        if self.bot_mode == "prism":
            args.extend([str(self.checkpoint), self.device])
            if self.device.startswith("gpu"):
                env["PYTORCH_FLAVOR"] = "cu128"
                if _LIBTORCH_CU128.is_dir():
                    env["PATH"] = os.pathsep.join([str(_LIBTORCH_CU128), env.get("PATH", "")])
            else:
                env["PYTORCH_FLAVOR"] = "cpu"
        self._stderr = open(self._work / "stderr.txt", "wb")
        self._proc = subprocess.Popen(
            [
                str(java),
                f"-Djdk.net.unixdomain.tmpdir={tmp_dir}",
                "-cp",
                os.pathsep.join([str(classes), str(_REVIEWER_JAR)]),
                "ukeire_dojo.live.LiveMatchHost",
                *args,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            env=env,
        )
        line = self._proc.stdout.readline()
        if not line:
            hint = self._exit_hint()
            self.close()
            raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, f"대국 엔진 시작 실패: {hint}")
        try:
            hello = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self.close()
            raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, f"대국 엔진 초기화 응답 오류: {e}") from e
        if hello.get("event") != "ready":
            self.close()
            raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, f"대국 엔진 준비 실패: {hello}")

    def call(self, cmd: str, **params) -> dict:
        with self._lock:
            self._ensure_started()
            req_id = self._next_id
            self._next_id += 1
            payload = {"id": req_id, "cmd": cmd, **params}
            raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            assert self._proc is not None and self._proc.stdin is not None and self._proc.stdout is not None
            try:
                self._proc.stdin.write(raw)
                self._proc.stdin.flush()
            except OSError as e:
                raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, f"대국 엔진 통신 오류: {self._exit_hint()}") from e
            line = self._proc.stdout.readline()
            if not line:
                raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, f"대국 엔진이 종료되었습니다: {self._exit_hint()}")
            reply = json.loads(line.decode("utf-8"))
            if reply.get("id") != req_id:
                raise RoomError(HTTPStatus.INTERNAL_SERVER_ERROR, f"대국 엔진 응답 순서 불일치: {reply}")
            if not reply.get("ok", False):
                raise RoomError(HTTPStatus.BAD_REQUEST, reply.get("error", "대국 엔진 오류"))
            return reply["result"]

    def _exit_hint(self) -> str:
        if self._stderr is not None:
            self._stderr.flush()
        if self._work is not None and (self._work / "stderr.txt").is_file():
            return (self._work / "stderr.txt").read_bytes()[-1500:].decode("utf-8", "replace")
        return ""

    def close(self) -> None:
        with self._lock:
            self._closed = True
            proc = self._proc
            self._proc = None
            if proc is not None and proc.poll() is None:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                    proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    proc.kill()
                    proc.wait()
            if self._stderr is not None:
                self._stderr.close()
                self._stderr = None
            if self._work is not None:
                shutil.rmtree(self._work, ignore_errors=True)
                self._work = None


@dataclass
class SeatSlot:
    seat: int
    kind: str = "empty"  # "empty" | "human" | "ai"
    name: str | None = None
    token: str | None = None
    ready: bool = False
    connected: bool = False
    active_streams: int = 0
    disconnected_at: float | None = None
    ai_takeover: bool = False
    disconnect_timer: threading.Timer | None = None


@dataclass
class Room:
    code: str
    host_seat: int
    lock: threading.RLock = field(default_factory=threading.RLock)
    cond: threading.Condition = field(init=False)
    status: str = "LOBBY"  # "LOBBY" | "PLAYING" | "MATCH_END" | "SERIES_END"
    match_number: int = 1
    version: int = 1
    seats: list[SeatSlot] = field(default_factory=lambda: [SeatSlot(i) for i in range(_MAX_PLAYERS)])
    pending: dict | None = None
    pending_revision: int | None = None
    pending_submissions: dict[int, int] = field(default_factory=dict)
    round_end_confirmations: set[int] = field(default_factory=set)
    first_match_result: dict | None = None
    second_match_result: dict | None = None

    def __post_init__(self) -> None:
        self.cond = threading.Condition(self.lock)


def _clean_name(raw: object) -> str:
    if not isinstance(raw, str):
        raise RoomError(HTTPStatus.BAD_REQUEST, "닉네임이 필요합니다.")
    name = " ".join(raw.strip().split())
    if not name:
        raise RoomError(HTTPStatus.BAD_REQUEST, "닉네임을 입력하세요.")
    if len(name) > _MAX_NAME_LEN:
        raise RoomError(HTTPStatus.BAD_REQUEST, f"닉네임은 {_MAX_NAME_LEN}자 이하여야 합니다.")
    return name


class RoomManager:
    """초대 코드 방, 좌석 배정, 연결 끊김 유예·AI 대타, 동풍전 1경기 진행을 관리한다."""

    def __init__(
        self,
        client: LiveMatchClient,
        *,
        disconnect_grace_seconds: float = 15.0,
        ping_interval: float = _DEFAULT_PING_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.disconnect_grace_seconds = disconnect_grace_seconds
        self.ping_interval = ping_interval
        self._clock = clock
        self._rooms_lock = threading.Lock()
        self._rooms: dict[str, Room] = {}
        self._closed = False

    def close(self) -> None:
        with self._rooms_lock:
            self._closed = True
            rooms = list(self._rooms.values())
        for room in rooms:
            with room.lock:
                for slot in room.seats:
                    if slot.disconnect_timer is not None:
                        slot.disconnect_timer.cancel()
                        slot.disconnect_timer = None
                room.cond.notify_all()
        self.client.close()

    def _new_code(self) -> str:
        for _ in range(64):
            code = "".join(secrets.choice(_ROOM_ALPHABET) for _ in range(6))
            if code not in self._rooms:
                return code
        raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, "방 코드를 만들 수 없습니다.")

    def _get_room(self, code: str) -> Room:
        normalized = code.strip().upper()
        with self._rooms_lock:
            room = self._rooms.get(normalized)
        if room is None:
            raise RoomError(HTTPStatus.NOT_FOUND, "존재하지 않는 방 코드입니다.")
        return room

    def _require_seat(self, room: Room, token: str | None) -> SeatSlot:
        if not token:
            raise RoomError(HTTPStatus.UNAUTHORIZED, "참가자 토큰이 필요합니다.")
        for slot in room.seats:
            if slot.kind == "human" and slot.token == token:
                return slot
        raise RoomError(HTTPStatus.FORBIDDEN, "이 방에 속한 참가자가 아닙니다.")

    def _bump(self, room: Room) -> None:
        room.version += 1
        room.cond.notify_all()

    def _cancel_timer(self, slot: SeatSlot) -> None:
        if slot.disconnect_timer is not None:
            slot.disconnect_timer.cancel()
            slot.disconnect_timer = None

    def _mark_reconnected(self, room: Room, slot: SeatSlot) -> bool:
        changed = not slot.connected or slot.ai_takeover
        self._cancel_timer(slot)
        slot.connected = True
        slot.disconnected_at = None
        if slot.ai_takeover:
            slot.ai_takeover = False
        return changed

    def create_room(self, name: object) -> dict:
        clean = _clean_name(name)
        token = secrets.token_hex(16)
        with self._rooms_lock:
            if self._closed:
                raise RoomError(HTTPStatus.SERVICE_UNAVAILABLE, "서버가 종료 중입니다.")
            code = self._new_code()
            room = Room(code=code, host_seat=0)
            room.seats[0] = SeatSlot(seat=0, kind="human", name=clean, token=token, ready=False, connected=True)
            self._rooms[code] = room
        with room.lock:
            return {"roomCode": code, "token": token, "seat": 0, "state": self._snapshot(room, 0)}

    def join_room(self, code: str, name: object, existing_token: str | None = None) -> dict:
        room = self._get_room(code)
        with room.lock:
            if existing_token:
                for slot in room.seats:
                    if slot.kind == "human" and slot.token == existing_token:
                        if self._mark_reconnected(room, slot):
                            self._bump(room)
                        return {
                            "roomCode": room.code,
                            "token": slot.token,
                            "seat": slot.seat,
                            "reconnected": True,
                            "state": self._snapshot(room, slot.seat),
                        }
            if room.status != "LOBBY":
                raise RoomError(HTTPStatus.CONFLICT, "이미 시작된 방에는 새로 입장할 수 없습니다.")
            clean = _clean_name(name)
            empty = next((s for s in room.seats if s.kind == "empty"), None)
            if empty is None:
                raise RoomError(HTTPStatus.CONFLICT, "방 인원이 가득 찼습니다.")
            token = secrets.token_hex(16)
            empty.kind = "human"
            empty.name = clean
            empty.token = token
            empty.ready = False
            empty.connected = True
            empty.active_streams = 0
            empty.disconnected_at = None
            empty.ai_takeover = False
            self._bump(room)
            return {
                "roomCode": room.code,
                "token": token,
                "seat": empty.seat,
                "reconnected": False,
                "state": self._snapshot(room, empty.seat),
            }

    def choose_seat(self, code: str, token: str | None, target_seat: int) -> dict:
        if not 0 <= target_seat < _MAX_PLAYERS:
            raise RoomError(HTTPStatus.BAD_REQUEST, "좌석 번호는 0~3이어야 합니다.")
        room = self._get_room(code)
        with room.lock:
            if room.status != "LOBBY":
                raise RoomError(HTTPStatus.CONFLICT, "대국 중에는 좌석을 바꿀 수 없습니다.")
            current = self._require_seat(room, token)
            if current.seat == target_seat:
                return self._snapshot(room, current.seat)
            dest = room.seats[target_seat]
            if dest.kind != "empty":
                raise RoomError(HTTPStatus.CONFLICT, "이미 다른 참가자가 앉은 좌석입니다.")
            room.seats[target_seat] = SeatSlot(
                seat=target_seat,
                kind="human",
                name=current.name,
                token=current.token,
                ready=False,
                connected=current.connected,
                active_streams=current.active_streams,
                disconnected_at=current.disconnected_at,
                ai_takeover=False,
                disconnect_timer=current.disconnect_timer,
            )
            room.seats[current.seat] = SeatSlot(seat=current.seat)
            if room.host_seat == current.seat:
                room.host_seat = target_seat
            self._bump(room)
            return self._snapshot(room, target_seat)

    def set_ready(self, code: str, token: str | None, ready: bool) -> dict:
        room = self._get_room(code)
        with room.lock:
            if room.status != "LOBBY":
                raise RoomError(HTTPStatus.CONFLICT, "대국 중에는 준비 상태를 바꿀 수 없습니다.")
            slot = self._require_seat(room, token)
            if slot.ready != bool(ready):
                slot.ready = bool(ready)
                self._bump(room)
            return self._snapshot(room, slot.seat)

    def leave_room(self, code: str, token: str | None) -> dict:
        room = self._get_room(code)
        delete_room = False
        with room.lock:
            slot = self._require_seat(room, token)
            if room.status != "LOBBY":
                raise RoomError(HTTPStatus.CONFLICT, "진행 중인 대국에서는 나갈 수 없습니다. 새로고침으로 복귀할 수 있습니다.")
            self._cancel_timer(slot)
            room.seats[slot.seat] = SeatSlot(seat=slot.seat)
            remaining = [s for s in room.seats if s.kind == "human"]
            if not remaining:
                delete_room = True
                room.cond.notify_all()
            else:
                if room.host_seat == slot.seat:
                    room.host_seat = remaining[0].seat
                self._bump(room)
        if delete_room:
            with self._rooms_lock:
                self._rooms.pop(room.code, None)
        return {"left": True}

    def start_match(self, code: str, token: str | None) -> dict:
        room = self._get_room(code)
        with room.lock:
            slot = self._require_seat(room, token)
            if room.status != "LOBBY":
                raise RoomError(HTTPStatus.CONFLICT, "이미 시작된 방입니다.")
            if slot.seat != room.host_seat:
                raise RoomError(HTTPStatus.FORBIDDEN, "방장만 대국을 시작할 수 있습니다.")
            ok, reason = self._can_start(room)
            if not ok:
                raise RoomError(HTTPStatus.CONFLICT, reason or "시작할 수 없습니다.")
            self.client.call("create_series", roomId=room.code)
            for s in room.seats:
                if s.kind == "empty":
                    s.kind = "ai"
                    s.name = f"AI {s.seat + 1}"
                    s.ready = True
                    s.connected = True
            room.status = "PLAYING"
            room.match_number = 1
            room.pending_revision = None
            room.pending_submissions.clear()
            room.round_end_confirmations.clear()
            self._drive_match(room)
            self._bump(room)
            return self._snapshot(room, slot.seat)

    def start_replay(self, code: str, token: str | None) -> dict:
        room = self._get_room(code)
        with room.lock:
            slot = self._require_seat(room, token)
            if slot.seat != room.host_seat:
                raise RoomError(HTTPStatus.FORBIDDEN, "방장만 두 번째 경기를 시작할 수 있습니다.")
            if room.match_number == 2 and room.status in ("PLAYING", "SERIES_END"):
                return self._snapshot(room, slot.seat)
            if room.status != "MATCH_END" or room.match_number != 1:
                raise RoomError(HTTPStatus.CONFLICT, "첫 번째 경기가 끝난 뒤에만 두 번째 경기를 시작할 수 있습니다.")
            self.client.call("start_replay", roomId=room.code)
            for s in room.seats:
                if s.kind == "human":
                    if s.seat == slot.seat:
                        self._mark_reconnected(room, s)
                    elif not s.connected:
                        # 2경기 시작 시점까지 미복귀한 사람은 원래 좌석·토큰을 유지한 채 AI 대타로 시작한다.
                        s.ai_takeover = True
                    else:
                        s.ai_takeover = False
            room.match_number = 2
            room.status = "PLAYING"
            room.pending_revision = None
            room.pending_submissions.clear()
            room.round_end_confirmations.clear()
            self._drive_match(room)
            self._bump(room)
            return self._snapshot(room, slot.seat)

    def get_discard_hint(self, code: str, token: str | None, revision: int | None = None) -> dict:
        room = self._get_room(code)
        with room.lock:
            slot = self._require_seat(room, token)
            if room.match_number != 2:
                raise RoomError(HTTPStatus.FORBIDDEN, "타패 추천은 두 번째 경기에서만 사용할 수 있습니다.")
            if room.status != "PLAYING" or room.pending is None or room.pending["phase"] != "DECISIONS":
                raise RoomError(HTTPStatus.CONFLICT, "현재 타패 추천을 요청할 수 있는 상태가 아닙니다.")
            current_rev = room.pending["revision"]
            if revision is not None and revision != current_rev:
                raise RoomError(HTTPStatus.CONFLICT, "이미 지나간 선택 시점입니다.")
            if slot.seat not in room.pending["pendingSeats"] or slot.seat in room.pending_submissions:
                raise RoomError(HTTPStatus.CONFLICT, "지금은 내 타패 차례가 아닙니다.")
            seat_view = self.client.call("seat_state", roomId=room.code, matchIndex=2, seat=slot.seat)
            decision = seat_view.get("decision")
            if not decision or not any(a["type"] == "DAHAI" for a in decision.get("actions", ())):
                raise RoomError(HTTPStatus.CONFLICT, "현재 타패를 고르는 차례가 아닙니다.")
            return self.client.call("discard_hint", roomId=room.code, matchIndex=2, seat=slot.seat)

    def submit_action(self, code: str, token: str | None, revision: int, action: int) -> dict:
        room = self._get_room(code)
        with room.lock:
            slot = self._require_seat(room, token)
            if room.status != "PLAYING" or room.pending is None or room.pending["phase"] != "DECISIONS":
                raise RoomError(HTTPStatus.CONFLICT, "현재 행동을 입력할 수 있는 상태가 아닙니다.")
            current_rev = room.pending["revision"]
            if revision != current_rev:
                raise RoomError(HTTPStatus.CONFLICT, "이미 지나간 선택 시점입니다.")
            pending_seats = room.pending["pendingSeats"]
            if slot.seat not in pending_seats:
                raise RoomError(HTTPStatus.CONFLICT, "지금은 내 차례가 아닙니다.")
            if slot.seat in room.pending_submissions:
                if room.pending_submissions[slot.seat] == action:
                    return self._snapshot(room, slot.seat)
                raise RoomError(HTTPStatus.CONFLICT, "이미 이 시점의 행동을 제출했습니다.")
            seat_view = self.client.call("seat_state", roomId=room.code, matchIndex=room.match_number, seat=slot.seat)
            decision = seat_view.get("decision")
            legal_codes = {a["code"] for a in (decision or {}).get("actions", ())}
            if action not in legal_codes:
                raise RoomError(HTTPStatus.BAD_REQUEST, "합법 행동 목록에 없는 선택입니다.")
            if self._mark_reconnected(room, slot):
                pass
            room.pending_submissions[slot.seat] = action
            self._drive_match(room)
            self._bump(room)
            return self._snapshot(room, slot.seat)

    def confirm_next_round(self, code: str, token: str | None, kyoku: int, honba: int) -> dict:
        room = self._get_room(code)
        with room.lock:
            slot = self._require_seat(room, token)
            if room.status != "PLAYING" or room.pending is None or room.pending["phase"] != "ROUND_END":
                raise RoomError(HTTPStatus.CONFLICT, "국 정산 확인 단계가 아닙니다.")
            current_round = room.pending["round"]
            if (kyoku, honba) != (current_round["kyoku"], current_round["honba"]):
                raise RoomError(HTTPStatus.CONFLICT, "이미 지나간 국 정산입니다.")
            self._mark_reconnected(room, slot)
            room.round_end_confirmations.add(slot.seat)
            self._drive_match(room)
            self._bump(room)
            return self._snapshot(room, slot.seat)

    def get_state(self, code: str, token: str | None) -> dict:
        room = self._get_room(code)
        with room.lock:
            slot = self._require_seat(room, token)
            return self._snapshot(room, slot.seat)

    def open_stream(self, code: str, token: str | None) -> tuple[Room, int, str]:
        room = self._get_room(code)
        with room.lock:
            slot = self._require_seat(room, token)
            assert slot.token is not None
            slot.active_streams += 1
            if self._mark_reconnected(room, slot):
                self._bump(room)
            return room, slot.seat, slot.token

    def close_stream(self, room: Room, token: str) -> None:
        with room.lock:
            slot = next((s for s in room.seats if s.kind == "human" and s.token == token), None)
            if slot is None:
                return
            slot.active_streams = max(0, slot.active_streams - 1)
            if slot.active_streams > 0:
                return
            slot.connected = False
            slot.disconnected_at = self._clock()
            self._cancel_timer(slot)
            timer = threading.Timer(self.disconnect_grace_seconds, self._on_grace_expired, args=(room.code, token))
            timer.daemon = True
            slot.disconnect_timer = timer
            timer.start()
            self._bump(room)

    def _on_grace_expired(self, code: str, token: str) -> None:
        with self._rooms_lock:
            if self._closed:
                return
            room = self._rooms.get(code)
        if room is None:
            return
        with room.lock:
            slot = next((s for s in room.seats if s.kind == "human" and s.token == token), None)
            if slot is None or slot.connected:
                return
            slot.disconnect_timer = None
            if room.status == "LOBBY":
                if slot.ready:
                    slot.ready = False
                    self._bump(room)
                return
            if room.status == "PLAYING":
                slot.ai_takeover = True
                self._drive_match(room)
                self._bump(room)

    def subscribe(self, code: str, token: str | None, is_disconnected: Callable[[], bool]) -> Iterator[dict]:
        room, seat, verified_token = self.open_stream(code, token)
        try:
            with room.lock:
                last_version = room.version
                initial = self._snapshot(room, seat)
            yield {"type": "state", "state": initial}
            last_ping = self._clock()
            while True:
                if is_disconnected() or self._closed:
                    return
                with room.lock:
                    if self._closed:
                        return
                    if room.version == last_version:
                        room.cond.wait(timeout=0.25)
                    if self._closed:
                        return
                    # 방에서 나간 경우(좌석 토큰 제거) 스트림을 정상 종료한다.
                    still_member = any(s.kind == "human" and s.token == verified_token for s in room.seats)
                    if not still_member:
                        return
                    if room.version != last_version:
                        last_version = room.version
                        current_seat = next(s.seat for s in room.seats if s.token == verified_token)
                        snap = self._snapshot(room, current_seat)
                        last_ping = self._clock()
                        event: dict | None = {"type": "state", "state": snap}
                    elif self._clock() - last_ping >= self.ping_interval:
                        last_ping = self._clock()
                        event = {"type": "ping"}
                    else:
                        event = None
                if event is not None:
                    yield event
        finally:
            self.close_stream(room, verified_token)

    def _can_start(self, room: Room) -> tuple[bool, str | None]:
        humans = [s for s in room.seats if s.kind == "human"]
        if len(humans) < _MIN_HUMANS:
            return False, "최소 2명의 사람이 입장해야 시작할 수 있습니다."
        if any(not s.connected for s in humans):
            return False, "연결이 끊긴 참가자가 있어 시작할 수 없습니다."
        if any(not s.ready for s in humans):
            return False, "참가자 전원이 준비해야 시작할 수 있습니다."
        return True, None

    def _drive_match(self, room: Room) -> None:
        while room.status == "PLAYING":
            pending = self.client.call("pending", roomId=room.code, matchIndex=room.match_number)
            room.pending = pending
            phase = pending["phase"]
            if phase == "MATCH_END":
                settlement = pending["settlement"]
                summary = {
                    "matchNumber": room.match_number,
                    "scores": list(settlement["scores"]),
                    "ranks": list(settlement["ranks"]),
                    "finalRound": dict(settlement["round"]),
                    "outcome": settlement["outcome"],
                }
                if room.match_number == 1:
                    room.first_match_result = summary
                    room.status = "MATCH_END"
                else:
                    room.second_match_result = summary
                    room.status = "SERIES_END"
                room.pending_submissions.clear()
                room.round_end_confirmations.clear()
                return
            if phase == "ROUND_END":
                required = {s.seat for s in room.seats if s.kind == "human" and not s.ai_takeover}
                if required <= room.round_end_confirmations:
                    rnd = pending["round"]
                    self.client.call(
                        "next_round",
                        roomId=room.code,
                        matchIndex=room.match_number,
                        kyoku=rnd["kyoku"],
                        honba=rnd["honba"],
                    )
                    room.round_end_confirmations.clear()
                    room.pending_submissions.clear()
                    continue
                return
            # phase == "DECISIONS"
            revision = pending["revision"]
            if room.pending_revision != revision:
                room.pending_revision = revision
                room.pending_submissions.clear()
            pending_seats = pending["pendingSeats"]
            for seat in pending_seats:
                if seat in room.pending_submissions:
                    continue
                slot = room.seats[seat]
                if slot.kind == "ai" or (slot.kind == "human" and slot.ai_takeover):
                    choice = self.client.call("ai_choice", roomId=room.code, matchIndex=room.match_number, seat=seat)
                    room.pending_submissions[seat] = int(choice["action"])
            if len(room.pending_submissions) == len(pending_seats):
                actions = {str(s): room.pending_submissions[s] for s in pending_seats}
                self.client.call(
                    "commit",
                    roomId=room.code,
                    matchIndex=room.match_number,
                    revision=revision,
                    actions=actions,
                )
                room.pending_submissions.clear()
                continue
            return

    def _snapshot(self, room: Room, my_seat: int) -> dict:
        can_start, start_reason = self._can_start(room) if room.status == "LOBBY" else (False, None)
        seats_public = [
            {
                "seat": s.seat,
                "kind": s.kind,
                "name": s.name,
                "ready": s.ready,
                "connected": s.connected,
                "aiTakeover": s.ai_takeover,
                "isHost": s.kind == "human" and s.seat == room.host_seat,
            }
            for s in room.seats
        ]
        match_payload = None
        if room.status in ("PLAYING", "MATCH_END", "SERIES_END"):
            match_payload = self.client.call("seat_state", roomId=room.code, matchIndex=room.match_number, seat=my_seat)
            submitted = my_seat in room.pending_submissions
            decision = match_payload.get("decision")
            match_payload["decisionSubmitted"] = submitted
            match_payload["waitingForOthers"] = (
                match_payload["phase"] == "DECISIONS" and (decision is None or submitted)
            )
            match_payload["hintAllowed"] = (
                room.match_number == 2
                and room.status == "PLAYING"
                and match_payload["phase"] == "DECISIONS"
                and not submitted
                and decision is not None
                and any(a["type"] == "DAHAI" for a in decision.get("actions", ()))
            )
            if submitted:
                match_payload["decision"] = None
            required = {s.seat for s in room.seats if s.kind == "human" and not s.ai_takeover}
            match_payload["roundEndConfirmed"] = my_seat in room.round_end_confirmations
            match_payload["roundEndConfirmedCount"] = len(room.round_end_confirmations & required)
            match_payload["roundEndRequiredCount"] = len(required)
        return {
            "roomCode": room.code,
            "status": room.status,
            "version": room.version,
            "matchNumber": room.match_number,
            "mySeat": my_seat,
            "isHost": my_seat == room.host_seat,
            "disconnectGraceSeconds": self.disconnect_grace_seconds,
            "seats": seats_public,
            "canStart": can_start,
            "startBlockReason": start_reason,
            "canStartReplay": room.status == "MATCH_END" and room.match_number == 1 and my_seat == room.host_seat,
            "firstMatchResult": room.first_match_result,
            "secondMatchResult": room.second_match_result,
            "match": match_payload,
        }
