"""Epsilon 리뷰어 백엔드를 상주 자식 프로세스로 띄워, 패보의 판단마다 전체 후보의 손실값을 받는 클라이언트.

엔진은 두 가지다. EngineConfig.cpu_v760()(기본값)은 v760 태그의 리뷰어를 릴리스 jar 위에서 CPU FP32로 돌린다.
EngineConfig.gpu_main()은 main을 CUDA 커널로 빌드한 리뷰어(scripts/build-epsilon-cuda.ps1)를 GPU BF16으로 돌린다.
프로토콜: vendor/epsilon-src/epsilon-reviewer/protocol 의 stdio.ts, commands.ts, result.ts(형식 6과 7 모두 읽는다).
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"

HONORS = "ESWNPFC"
RED_FIVES = ("5mr", "5pr", "5sr")
# protocol/result.ts actionOf의 고정 배치. (종류, 끝 ID(미포함))
_CALL_RANGES = (("chi", 155), ("pon", 192), ("daiminkan", 226), ("ankan", 260), ("kakan", 294))
_FINISHED = ("completed", "failed", "cancelled")


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


_V760_CLASSPATH = (
    VENDOR / "reviewer-v760" / "classes",
    VENDOR / "reviewer-v760" / "epsilon-reviewer" / "backend" / "src" / "main" / "resources",
    VENDOR / "epsilon-release" / "epsilon-prism-1.0-SNAPSHOT-all.jar",
)
_MAIN_REVIEWER_JAR = VENDOR / "epsilon-src" / "epsilon-reviewer" / "backend" / "target" / "epsilon-reviewer-1.0-SNAPSHOT-all.jar"
_LIBTORCH_CU128 = Path.home() / ".djl.ai" / "pytorch" / "2.11.0-cu128-win-x86_64"


@dataclass(frozen=True)
class EngineConfig:
    java: Path = VENDOR / "jdk" / "bin" / "java.exe"
    classpath: tuple[Path, ...] = _V760_CLASSPATH
    checkpoint: Path = VENDOR / "epsilon-release" / "epsilon-prism-v760"
    model_options: tuple[tuple[str, object], ...] = (("device", "cpu"),)
    # main의 모델 카탈로그는 variant 필드와 defaultModels 목록을 요구하고, v760은 defaultModel 하나를 쓴다.
    main_catalog: bool = False
    pytorch_flavor: str = "cpu"
    # DJL은 libtorch DLL을 전체 경로로 로드하므로, 그 DLL이 의존하는 같은 폴더의 CUDA DLL은 PATH에 있어야 찾힌다.
    extra_path: tuple[Path, ...] = ()
    work_root: Path = ROOT / "data" / "engine"
    poll_seconds: float = 0.2

    @classmethod
    def cpu_v760(cls, **overrides) -> EngineConfig:
        return cls(**overrides)

    @classmethod
    def gpu_main(cls, device: str = "gpu0", **overrides) -> EngineConfig:
        defaults = dict(
            classpath=(_MAIN_REVIEWER_JAR,),
            model_options=(("device", device), ("precision", "BFLOAT16"), ("kernels", "NATIVE"), ("maxBatch", 2048)),
            main_catalog=True,
            pytorch_flavor="cu128",
            extra_path=(_LIBTORCH_CU128,),
        )
        defaults.update(overrides)
        return cls(**defaults)


@dataclass(frozen=True)
class Action:
    kind: str  # dahai, riichi, chi, pon, daiminkan, ankan, kakan, tsumo, ron, pass, kyushu
    tile: str | None = None
    tsumogiri: bool = False

    @property
    def label(self) -> str:
        if self.tile is None:
            return self.kind
        return f"{self.kind} {self.tile}" + (" (tsumogiri)" if self.tsumogiri else "")


def tile_code(tile_type: int) -> str:
    if tile_type >= 27:
        return HONORS[tile_type - 27]
    return f"{tile_type % 9 + 1}{'mps'[tile_type // 9]}"


def _discard(kind: str, offset: int) -> Action:
    if offset < 34:
        return Action(kind, tile_code(offset))
    if offset < 68:
        return Action(kind, tile_code(offset - 34), tsumogiri=True)
    if offset < 71:
        return Action(kind, RED_FIVES[offset - 68])
    return Action(kind, RED_FIVES[offset - 71], tsumogiri=True)


def decode_action(action_id: int) -> Action:
    # 372(북 뽑기, nuki)는 결과 형식 7에서 3인 마작용으로 추가됐다. 4인 패보에서는 나오지 않는다.
    if not 0 <= action_id <= 372:
        raise ValueError(f"invalid action id: {action_id}")
    if action_id < 74:
        return _discard("dahai", action_id)
    for kind, end in _CALL_RANGES:
        if action_id < end:
            return Action(kind)
    if action_id < 368:
        return _discard("riichi", action_id - 294)
    return Action(("tsumo", "ron", "pass", "kyushu", "nuki")[action_id - 368])


@dataclass(frozen=True)
class Candidate:
    action_id: int
    action: Action
    loss: float  # 최선수 대비 순위 효용 손실. 최선수는 0.
    tiles: tuple[str, ...] = ()  # 울기·깡 후보에서만 쓰는 구성 패


@dataclass(frozen=True)
class Decision:
    round_index: int  # 장풍 × 4 + (국 − 1). 0이 동1국.
    honba: int
    step: int  # 국 안에서 steps 배열의 위치
    seat: int
    cause: int | None
    candidates: tuple[Candidate, ...]  # 최선수가 먼저
    chosen: int | None  # 패보에서 고른 후보의 위치

    @property
    def best(self) -> Candidate:
        return self.candidates[0]

    @property
    def chosen_candidate(self) -> Candidate | None:
        return None if self.chosen is None else self.candidates[self.chosen]


@dataclass(frozen=True)
class Analysis:
    record: dict
    temperature: float | None
    decisions: tuple[Decision, ...]
    seconds: float
    document: dict = field(repr=False)  # T3에서 국면 복원에 쓰는 원본 결과 문서


def parse_document(document: dict, seconds: float = 0.0) -> Analysis:
    decisions = []
    for rnd in document["rounds"]:
        for step_index, step in enumerate(rnd["steps"]):
            if "seat" not in step:
                continue
            candidates = tuple(
                Candidate(row[0], decode_action(row[0]), float(row[1]), tuple(row[2]) if len(row) > 2 else ())
                for row in step["candidates"]
            )
            decisions.append(
                Decision(
                    round_index=rnd["roundIndex"],
                    honba=rnd["honba"],
                    step=step_index,
                    seat=step["seat"],
                    cause=step.get("cause"),
                    candidates=candidates,
                    chosen=step.get("chosen"),
                )
            )
    return Analysis(document["record"], document.get("temperature"), tuple(decisions), seconds, document)


class EpsilonEngine:
    """엔진 프로세스 하나를 소유한다. 스레드 안전하지만 분석은 직렬로만 돈다.

    리뷰어는 분석 작업 스레드가 하나뿐이고 같은 소유자의 동시 분석을 거부한다(analysis_active). 그래서 요청을 겹쳐 보내도 빨라지지
    않는다. 처리량이 필요하면 이 객체를 여러 개 만든다. 객체마다 데이터 폴더를 따로 쓰는데, 리뷰어가 데이터 폴더를 잠그기 때문이다.
    """

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self._lock = threading.Lock()
        self._next_id = 1
        self.config.work_root.mkdir(parents=True, exist_ok=True)
        self._work = Path(tempfile.mkdtemp(prefix="engine-", dir=self.config.work_root))
        models = self._write_models()
        for sub in ("data", "logs", "tmp"):
            (self._work / sub).mkdir()
        classpath = os.pathsep.join(str(p) for p in self.config.classpath)
        env = dict(
            os.environ,
            EPSILON_REVIEWER_DATA=str(self._work / "data"),
            EPSILON_REVIEWER_MODELS=str(models),
            EPSILON_REVIEWER_LOGS=str(self._work / "logs"),
            # DJL은 GPU를 감지하면 CUDA판 libtorch를 받으므로, 엔진마다 쓸 판을 명시한다.
            PYTORCH_FLAVOR=self.config.pytorch_flavor,
        )
        if self.config.extra_path:
            env["PATH"] = os.pathsep.join([*map(str, self.config.extra_path), env.get("PATH", "")])
        self._stderr = open(self._work / "stderr.txt", "wb")
        self._proc = subprocess.Popen(
            [
                str(self.config.java),
                # 8.3 단축 경로 TEMP에서는 NIO Selector의 Unix 도메인 소켓이 만들어지지 않아 엔진 시작이 실패한다.
                f"-Djdk.net.unixdomain.tmpdir={self._work / 'tmp'}",
                "-cp",
                classpath,
                "com.epsilon.reviewer.StdioMain",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            env=env,
        )
        try:
            hello, _ = self._read_frame()
            if hello.get("event") != "ready":
                error = hello.get("error", {})
                raise EngineError(error.get("code", "not_ready"), error.get("message", str(hello)))
        except BaseException:
            self.close(keep_work=True)
            raise

    def _write_models(self) -> Path:
        path = self._work / "models.json"
        key = {"modelId": "epsilon-prism-v2", "revision": "prism-v760"}
        model = {
            **key,
            "displayName": "Epsilon-Prism v760",
            "version": "v760",
            "series": "epsilon-prism",
            "checkpoint": str(self.config.checkpoint),
            "options": dict(self.config.model_options),
        }
        if self.config.main_catalog:
            model["variant"] = "TENHOU_4P"
            catalog = {"models": [model], "defaultModels": [key]}
        else:
            catalog = {"models": [model], "defaultModel": key}
        path.write_text(json.dumps(catalog), encoding="utf-8")
        return path

    def __enter__(self) -> EpsilonEngine:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def analyze(self, record: bytes) -> Analysis:
        """완전한 패보(천봉 XML, mjai, 작혼, gzip 가능)를 분석한다. 재생 검증에 실패하면 EngineError."""
        if record[:2] == b"\x1f\x8b":
            record = gzip.decompress(record)
        with self._lock:
            started = time.perf_counter()
            imported = self._call_raw("importBytes", {}, record)[0]["response"]
            job = self._call(
                "startAnalysis",
                {"recordId": imported["recordId"], "modelId": "epsilon-prism-v2", "revision": "prism-v760"},
            )["response"]
            while True:
                snap = self._call("job", {"jobId": job["jobId"]})["response"]
                if snap["status"] in _FINISHED:
                    break
                time.sleep(self.config.poll_seconds)
            if snap["status"] != "completed":
                error = snap.get("error", {})
                raise EngineError(error.get("code", snap["status"]), error.get("message", ""))
            result_id = snap["resultId"]
            _, body = self._call_raw("result", {"resultId": result_id})
            # 리뷰어는 결과를 데이터 폴더에 쌓아 두므로 읽은 즉시 지운다.
            self._call("deleteResult", {"resultId": result_id})
            return parse_document(json.loads(gzip.decompress(body)), time.perf_counter() - started)

    def close(self, keep_work: bool = False) -> None:
        """keep_work=True면 엔진 로그를 남긴다(시작 실패를 조사할 때)."""
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.poll() is None:
            # stdin을 닫으면 리뷰어가 모델을 닫고 종료 코드 0으로 끝난다(protocol/stdio.ts).
            try:
                proc.stdin.close()
                proc.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait()
        if getattr(self, "_stderr", None) is not None:
            self._stderr.close()
        if not keep_work:
            shutil.rmtree(self._work, ignore_errors=True)

    @property
    def log_dir(self) -> Path:
        return self._work / "logs"

    def _call(self, command: str, request: dict) -> dict:
        reply, _ = self._call_raw(command, request)
        return reply

    def _call_raw(self, command: str, request: dict, body: bytes = b"") -> tuple[dict, bytes]:
        frame_id = self._next_id
        self._next_id += 1
        header = json.dumps({"id": frame_id, "command": command, "request": request}).encode()
        try:
            self._proc.stdin.write(struct.pack(">I", len(header)) + header + struct.pack(">I", len(body)) + body)
            self._proc.stdin.flush()
        except OSError as e:
            raise EngineError("engine_exited", self._exit_hint()) from e
        # 잠금 안에서 요청을 하나씩만 보내므로 응답 순서가 섞이지 않는다.
        reply, reply_body = self._read_frame()
        if reply.get("id") != frame_id:
            raise EngineError("protocol", f"expected reply {frame_id}, got {reply}")
        if not reply.get("ok", False):
            error = reply.get("error", {})
            raise EngineError(error.get("code", "unknown"), error.get("message", ""))
        return reply, reply_body

    def _read_frame(self) -> tuple[dict, bytes]:
        header = json.loads(self._read_exact(struct.unpack(">I", self._read_exact(4))[0]))
        return header, self._read_exact(struct.unpack(">I", self._read_exact(4))[0])

    def _read_exact(self, n: int) -> bytes:
        data = self._proc.stdout.read(n)
        if len(data) != n:
            raise EngineError("engine_exited", self._exit_hint())
        return data

    def _exit_hint(self) -> str:
        # close()가 작업 폴더를 지우므로 stderr 끝부분을 예외 메시지에 실어 둔다.
        self._stderr.flush()
        tail = (self._work / "stderr.txt").read_bytes()[-2000:].decode("utf-8", "replace")
        return f"engine stream closed (exit={self._proc.poll()}):\n{tail}"
