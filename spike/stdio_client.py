"""Epsilon 리뷰어 백엔드(v760)와 stdio 프레임으로 왕복해 판단별 전체 후보 가치를 뽑는 T1 실험 스크립트."""

import gzip
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
WORK = ROOT / "spike" / "work"

HONORS = ["E", "S", "W", "N", "P", "F", "C"]
RED_FIVES = ["5mr", "5pr", "5sr"]
CALL_NAMES = ["CHI", "PON", "DAIMINKAN", "ANKAN", "KAKAN"]


def tile_code(tile_type: int) -> str:
    if tile_type >= 27:
        return HONORS[tile_type - 27]
    return f"{tile_type % 9 + 1}{'mps'[tile_type // 9]}"


def discard_label(offset: int) -> str:
    if offset < 34:
        return tile_code(offset)
    if offset < 68:
        return tile_code(offset - 34) + "(tsumogiri)"
    if offset < 71:
        return RED_FIVES[offset - 68]
    return RED_FIVES[offset - 71] + "(tsumogiri)"


def action_label(action_id: int) -> str:
    # 경계값은 protocol/result.ts actionOf의 고정 배치를 그대로 따른다.
    if action_id < 74:
        return "dahai " + discard_label(action_id)
    for name, end in zip(CALL_NAMES, (155, 192, 226, 260, 294)):
        if action_id < end:
            return name.lower()
    if action_id < 368:
        return "riichi " + discard_label(action_id - 294)
    return ["tsumo", "ron", "pass", "kyushu"][action_id - 368]


class Engine:
    def __init__(self) -> None:
        WORK.mkdir(parents=True, exist_ok=True)
        for sub in ("data", "logs"):
            (WORK / sub).mkdir(exist_ok=True)
        classpath = os.pathsep.join(
            [
                str(VENDOR / "reviewer-v760" / "classes"),
                str(VENDOR / "reviewer-v760" / "epsilon-reviewer" / "backend" / "src" / "main" / "resources"),
                str(VENDOR / "epsilon-release" / "epsilon-prism-1.0-SNAPSHOT-all.jar"),
            ]
        )
        env = dict(
            os.environ,
            EPSILON_REVIEWER_DATA=str(WORK / "data"),
            EPSILON_REVIEWER_MODELS=str(ROOT / "spike" / "models.json"),
            EPSILON_REVIEWER_LOGS=str(WORK / "logs"),
            PYTORCH_FLAVOR="cpu",
        )
        # Windows의 NIO Selector는 TEMP에 Unix 도메인 소켓을 만드는데, 8.3 단축 경로의 TEMP에서는 connect가
        # "Invalid argument"로 실패해 HttpClient를 만드는 엔진 시작이 죽는다. 소켓 폴더를 일반 경로로 옮긴다.
        (WORK / "tmp").mkdir(exist_ok=True)
        self.proc = subprocess.Popen(
            [
                str(VENDOR / "jdk" / "bin" / "java.exe"),
                f"-Djdk.net.unixdomain.tmpdir={WORK / 'tmp'}",
                "-cp",
                classpath,
                "com.epsilon.reviewer.StdioMain",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(WORK / "stderr.txt", "wb"),
            env=env,
        )
        self.next_id = 1
        hello, _ = self._read_frame()
        if hello.get("event") != "ready":
            raise RuntimeError(f"engine did not start: {hello}")

    def _read_frame(self) -> tuple[dict, bytes]:
        header = json.loads(self._read_exact(struct.unpack(">I", self._read_exact(4))[0]))
        body = self._read_exact(struct.unpack(">I", self._read_exact(4))[0])
        return header, body

    def _read_exact(self, n: int) -> bytes:
        data = self.proc.stdout.read(n)
        if len(data) != n:
            raise RuntimeError(f"engine closed the stream (exit={self.proc.poll()}); see {WORK / 'stderr.txt'}")
        return data

    def call(self, command: str, request: dict | None = None, body: bytes = b"") -> tuple[dict, bytes]:
        frame_id = self.next_id
        self.next_id += 1
        header = json.dumps({"id": frame_id, "command": command, "request": request or {}}).encode()
        self.proc.stdin.write(struct.pack(">I", len(header)) + header + struct.pack(">I", len(body)) + body)
        self.proc.stdin.flush()
        # 요청을 하나씩만 보내므로 응답 순서가 섞이지 않는다.
        reply, reply_body = self._read_frame()
        if not reply.get("ok", False):
            raise RuntimeError(f"{command} failed: {reply}")
        return reply, reply_body

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=60)


def main(record_path: str) -> None:
    started = time.perf_counter()
    engine = Engine()
    try:
        reply, _ = engine.call("models")
        print("models:", [m["modelId"] for m in reply["response"]["models"]])
        print(f"startup+models: {time.perf_counter() - started:.1f}s")

        record = Path(record_path).read_bytes()
        if record_path.endswith(".gz"):
            record = gzip.decompress(record)
        imported = engine.call("importBytes", {}, record)[0]["response"]
        print("imported:", imported["recordId"], imported["record"])

        t0 = time.perf_counter()
        job = engine.call(
            "startAnalysis",
            {"recordId": imported["recordId"], "modelId": "epsilon-prism-v2", "revision": "prism-v760"},
        )[0]["response"]
        while True:
            snap = engine.call("job", {"jobId": job["jobId"]})[0]["response"]
            if snap["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.5)
        if snap["status"] != "completed":
            raise RuntimeError(f"analysis {snap}")
        print(f"analysis: {time.perf_counter() - t0:.1f}s")

        _, doc_gz = engine.call("result", {"resultId": snap["resultId"]})
        doc = json.loads(gzip.decompress(doc_gz))
        (WORK / "result.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

        decisions = [s for r in doc["rounds"] for s in r["steps"] if "seat" in s]
        sizes = [len(d["candidates"]) for d in decisions]
        print(f"temperature={doc.get('temperature')} decisions={len(decisions)} "
              f"candidates/decision min={min(sizes)} max={max(sizes)}")
        richest = max(decisions, key=lambda d: len(d["candidates"]))
        print(f"largest decision (seat {richest['seat']}, chosen={richest.get('chosen')}):")
        for action_id, value, *tiles in richest["candidates"]:
            print(f"  {action_label(action_id):24s} {value}")
    finally:
        engine.close()


if __name__ == "__main__":
    main(sys.argv[1])
