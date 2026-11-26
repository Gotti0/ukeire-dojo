"""CLI의 텍스트 스트림을 해설로 변환하고, 표시할 수치를 서버에서 확정한다."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Iterator

LABELS = {"codex": "Codex CLI", "agy": "agy CLI", "claude": "Claude Code CLI"}
REFERENCE = re.compile(r"\[\[([a-z_]+)\]\]")
UNVERIFIED = re.compile(
    r"[\[\]\d①-⑳¼-¾]|"
    r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|[영공일이삼사오육칠팔구십백천만]+)\s*(?:장|개|점|퍼센트|샨텐)\b|"
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
    re.I,
)
MAX_LINE = 4096


class ExplanationError(RuntimeError):
    pass


def cli_executable(provider: str) -> str | None:
    return shutil.which(f"{provider}.exe" if os.name == "nt" else provider)


def explanation_prompt(problem: dict, result: dict) -> tuple[str, dict[str, str]]:
    chosen = next(row for row in result["table"] if row["tile"] == result["tile"])
    best = next(row for row in result["table"] if row["tile"] == result["ai_best"])
    facts = {}
    for name, row in (("chosen", chosen), ("best", best)):
        facts.update({
            f"{name}_tile": row["tile"],
            f"{name}_loss": f'{row["ai_loss"]:.3f}',
            f"{name}_shanten": str(row["shanten"]),
            f"{name}_ukeire": str(row["ukeire"]),
        })
    situation = {
        "hand": problem["hand"], "draw": problem["draw"], "dora_indicators": problem["dora"],
        "opponents_riichi": any(p["riichi"] for p in problem["players"] if p["seat"] != problem["seat"]),
        "matches_efficiency": result["matches_efficiency"], "matches_ai": result["matches_ai"],
    }
    prompt = (
        "리치 마작 타패 채점 결과를 한국어로 설명하세요. 도구를 호출하지 마세요. "
        "사용자가 버린 패와 AI 최선 타패의 손실, 샨텐, 유효패를 비교하세요. "
        "AI 손실은 순위 효용의 차이이며 방총 확률이 아닙니다. 확인할 수 없는 이유는 단정하지 마세요. "
        "숫자, 패 이름, 수치의 문자 표현 대신 반드시 제공된 [[키]]를 사용하세요. "
        "각 문장을 완결된 한 줄로 출력하고 줄바꿈으로 구분하세요. 제목, 목록, 마크다운 없이 세 문장 정도면 됩니다. "
        "예: 선택한 [[chosen_tile]]의 AI 손실은 [[chosen_loss]]입니다.\n"
        f"국면: {json.dumps(situation, ensure_ascii=False)}\n"
        f"사용 가능한 참조와 실제 값: {json.dumps({f'[[{k}]]': v for k, v in facts.items()}, ensure_ascii=False)}"
    )
    return prompt, facts


def verified_line(line: str, facts: dict[str, str]) -> str | None:
    if UNVERIFIED.search(REFERENCE.sub("", line)):
        return None
    try:
        return REFERENCE.sub(lambda match: facts[match[1]], line)
    except KeyError:
        return None


def cli_command(provider: str, executable: str, prompt: str) -> tuple[list[str], str | None]:
    if provider == "codex":
        return [
            executable, "app-server", "-c", "mcp_servers={}", "-c", "features.shell_tool=false",
            "-c", 'web_search="disabled"',
        ], None
    if provider == "agy":
        settings = {"permissions": {"deny": [
            "command(*)", "unsandboxed(*)", "read_file(*)", "write_file(*)",
            "read_url(*)", "execute_url(*)", "mcp(*)",
        ]}}
        return [
            executable, "--sandbox", "--disable-slash-commands", "--settings", json.dumps(settings),
            "--output-format", "stream-json", "-p", prompt,
        ], None
    return [
        executable, "-p", "--tools", "", "--setting-sources", "", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence", "--verbose",
        "--output-format", "stream-json", "--include-partial-messages",
    ], prompt


def json_lines(proc: subprocess.Popen) -> Iterator[dict]:
    for line in proc.stdout:
        if line.strip():
            yield json.loads(line)


def codex_text(proc: subprocess.Popen, prompt: str, cwd: str) -> Iterator[str]:
    def send(method: str, params: dict, request_id: int | None = None) -> None:
        message = {"method": method, "params": params}
        if request_id is not None:
            message["id"] = request_id
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    send("initialize", {
        "clientInfo": {"name": "ukeire_dojo", "version": "1"},
        "capabilities": {"experimentalApi": True},
    }, 1)
    for event in json_lines(proc):
        method = event.get("method")
        request_id = event.get("id")
        params = event.get("params", {})
        if "error" in event:
            raise ExplanationError("Codex 요청을 실행할 수 없습니다. 로그인과 CLI 설정을 확인하세요.")
        if request_id == 1 and "result" in event:
            send("initialized", {})
            # 데스크톱 모델 설정과 CLI 로그인 계정의 모델 목록이 다를 수 있다.
            send("model/list", {}, 2)
        elif request_id == 2 and "result" in event:
            default = next((model for model in event["result"]["data"] if model["isDefault"]), None)
            if default is None:
                raise ExplanationError("Codex 로그인 계정의 기본 모델을 찾을 수 없습니다.")
            send("thread/start", {
                "cwd": cwd, "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never",
                "model": default["model"],
            }, 3)
        elif request_id == 3 and "result" in event:
            send("turn/start", {
                "threadId": event["result"]["thread"]["id"],
                "input": [{"type": "text", "text": prompt}],
            }, 4)
        elif method == "item/agentMessage/delta":
            yield params["delta"]
        elif method == "turn/completed":
            if params["turn"]["status"] != "completed":
                raise ExplanationError("Codex가 해설을 완료하지 못했습니다. 로그인과 CLI 설정을 확인하세요.")
            return
        elif method and request_id is not None:
            raise ExplanationError("Codex가 해설 생성 중 상호작용을 요청했습니다.")
    raise ExplanationError("Codex가 완료 신호를 보내기 전에 종료됐습니다.")


def print_text(provider: str, proc: subprocess.Popen, stdin: str | None) -> Iterator[str]:
    if stdin is not None:
        proc.stdin.write(stdin)
        proc.stdin.close()
    completed = False
    for event in json_lines(proc):
        if provider == "agy":
            step = event.get("step_update", {})
            if step.get("step_type") == "agent_response":
                yield step.get("text_delta", "")
            if event.get("event") == "result":
                completed = event["result"]["status"] == "SUCCESS"
        else:
            delta = event.get("event", {}).get("delta", {})
            if event.get("type") == "stream_event" and delta.get("type") == "text_delta":
                yield delta["text"]
            if event.get("type") == "result":
                completed = not event.get("is_error", True)
                if not completed and re.search(r"auth|oauth|login", str(event.get("result", "")), re.I):
                    raise ExplanationError("Claude Code 인증에 실패했습니다. CLI에서 다시 로그인하세요.")
    if proc.wait() != 0 or not completed:
        raise ExplanationError("CLI가 해설을 완료하지 못했습니다. 로그인과 CLI 설정을 확인하세요.")


def stop_process(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        if proc.poll() is not None:
            return
        # 실행 파일과 그 하위 프로세스를 함께 종료해야 취소 뒤에도 CLI가 계속 실행되지 않는다.
        subprocess.run(
            ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if proc.poll() is None:
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


def stream_explanation(provider: str, executable: str, prompt: str, facts: dict[str, str]) -> Iterator[dict]:
    command, stdin = cli_command(provider, executable, prompt)
    messages: queue.Queue = queue.Queue()
    with tempfile.TemporaryDirectory(prefix="ukeire-explain-") as cwd:
        # 해설은 서버가 제공한 국면 데이터만 사용하므로 저장소 밖에서 실행한다.
        with subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as proc:
            def read_output() -> None:
                buffered = ""
                try:
                    chunks = codex_text(proc, prompt, cwd) if provider == "codex" else print_text(provider, proc, stdin)
                    for chunk in chunks:
                        *lines, buffered = (buffered + chunk).split("\n")
                        for line in lines:
                            if line.strip():
                                messages.put(verified_line(line.strip(), facts) if len(line) <= MAX_LINE else None)
                        if len(buffered) > MAX_LINE:
                            raise ExplanationError("CLI가 해설 문장 형식을 지키지 않았습니다.")
                    if buffered.strip():
                        messages.put(verified_line(buffered.strip(), facts))
                    messages.put({"type": "done"})
                except ExplanationError as error:
                    messages.put({"type": "error", "error": str(error)})
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    messages.put({"type": "error", "error": "CLI 출력 형식을 읽을 수 없습니다."})

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            shown = rejected = 0
            try:
                while True:
                    try:
                        message = messages.get(timeout=1)
                    except queue.Empty:
                        # 출력이 없을 때도 연결 종료를 감지한다. 대기 시간으로 해설을 중단하지 않는다.
                        yield {"type": "heartbeat"}
                        continue
                    if message is None:
                        rejected += 1
                    elif isinstance(message, str):
                        shown += 1
                        yield {"type": "text", "text": message + "\n"}
                    else:
                        if message["type"] == "done" and shown == 0:
                            message = {"type": "error", "error": "검증을 통과한 해설을 받지 못했습니다."}
                        yield {**message, "shown": shown, "rejected": rejected}
                        return
            finally:
                stop_process(proc)
                reader.join()
