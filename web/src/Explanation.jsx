import { useEffect, useRef, useState } from "react";

const PROVIDERS = {
  codex: "Codex CLI",
  agy: "agy CLI (Antigravity)",
  claude: "Claude Code CLI",
};

export default function Explanation({ result, provider, onProviderChange }) {
  const controllerRef = useRef(null);
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [running, setRunning] = useState(false);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const explain = async () => {
    if (controllerRef.current) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setText("");
    setStatus("해설 생성 중…");
    setRunning(true);
    try {
      const response = await fetch("/api/explanation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: result.id, tile: result.tile, provider }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.error || response.statusText);
      }
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffered = "";
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) throw new Error("해설이 끝나기 전에 연결이 닫혔습니다.");
          buffered += value;
          const lines = buffered.split("\n");
          buffered = lines.pop();
          for (const line of lines) {
            const event = JSON.parse(line);
            if (event.type === "text") setText((current) => current + event.text);
            if (event.type === "error") throw new Error(event.error);
            if (event.type === "done") {
              setStatus("해설 완료");
              return;
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    } catch (error) {
      setStatus(controller.signal.aborted ? "해설 취소됨" : error.message);
    } finally {
      controllerRef.current = null;
      setRunning(false);
    }
  };

  return (
    <section className="explanation">
      <div className="explanation-controls">
        <label>
          해설 도구{" "}
          <select disabled={running} value={provider} onChange={(event) => onProviderChange(event.target.value)}>
            {Object.entries(PROVIDERS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        {running
          ? <button type="button" onClick={() => controllerRef.current?.abort()}>해설 취소</button>
          : <button type="button" onClick={explain}>해설 보기</button>}
      </div>
      {status && <p className="muted small-text" role="status">{status}</p>}
      {text && <p className="explanation-text">{text}</p>}
    </section>
  );
}
