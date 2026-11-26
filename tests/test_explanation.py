import io
import json
import sys
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest

from ukeire_dojo import explanation


def test_verified_lines_only_replace_known_facts():
    facts = {"chosen_loss": "0.021", "best_tile": "5m"}
    assert explanation.verified_line("[[best_tile]] 대비 손실 [[chosen_loss]]입니다.", facts) == "5m 대비 손실 0.021입니다."
    for line in ("손실은 0.02입니다.", "유효패 세 장", "AI 최선은 [[bad_tile]]입니다.", "아직 [[chosen_lo"):
        assert explanation.verified_line(line, facts) is None


def test_codex_uses_deltas_and_does_not_repeat_completed_item():
    events = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"data": [
            {"model": "alternative", "isDefault": False}, {"model": "account-default", "isDefault": True},
        ]}},
        {"id": 3, "result": {"thread": {"id": "thread"}}},
        {"id": 4, "result": {}},
        {"method": "item/agentMessage/delta", "params": {"delta": "첫 문장\n"}},
        {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "hidden"}},
        {"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "첫 문장\n"}}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ]
    process = SimpleNamespace(stdin=io.StringIO(), stdout=map(json.dumps, events))
    assert list(explanation.codex_text(process, "prompt", "/tmp")) == ["첫 문장\n"]
    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert [item["method"] for item in requests] == ["initialize", "initialized", "model/list", "thread/start", "turn/start"]
    assert requests[-2]["params"]["approvalPolicy"] == "never"
    assert requests[-2]["params"]["model"] == "account-default"


@pytest.mark.parametrize(("provider", "events"), [
    ("agy", [
        {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "first"}},
        {"event": "result", "result": {"status": "SUCCESS", "response": "first"}},
    ]),
    ("claude", [
        {"type": "stream_event", "event": {"delta": {"type": "thinking_delta", "thinking": "hidden"}}},
        {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "first"}}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"type": "result", "is_error": False},
    ]),
])
def test_print_providers_only_emit_text_deltas(provider, events):
    process = SimpleNamespace(stdout=map(json.dumps, events), wait=lambda: 0)
    assert list(explanation.print_text(provider, process, None)) == ["first"]


def test_claude_authentication_error_does_not_stream_error_body():
    events = [{"type": "result", "is_error": True, "result": "OAuth expired; private details"}]
    process = SimpleNamespace(stdout=map(json.dumps, events))
    with pytest.raises(explanation.ExplanationError, match="다시 로그인") as error:
        list(explanation.print_text("claude", process, None))
    assert "private details" not in str(error.value)


def test_stream_checks_complete_lines_and_kills_running_process_on_close(monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"
    child = r"""
import json, os, pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
for fragment in ("incorrect 33.\n손실은 [[chosen_", "loss]]입니다.\n"):
    print(json.dumps({"event": "step_update", "step_update": {
        "step_type": "agent_response", "text_delta": fragment}}), flush=True)
time.sleep(300)
"""
    monkeypatch.setattr(explanation, "cli_command", lambda *args: ([sys.executable, "-c", child, str(pid_file)], None))
    stopped = threading.Event()
    real_stop = explanation.stop_process

    def stop(process):
        real_stop(process)
        assert process.pid == int(pid_file.read_text())
        assert process.poll() is not None
        stopped.set()

    monkeypatch.setattr(explanation, "stop_process", stop)
    with closing(explanation.stream_explanation("agy", "stub", "prompt", {"chosen_loss": "0.125"})) as events:
        for event in events:
            if event["type"] != "heartbeat":
                assert event == {"type": "text", "text": "손실은 0.125입니다.\n"}
                break
    assert stopped.is_set()
