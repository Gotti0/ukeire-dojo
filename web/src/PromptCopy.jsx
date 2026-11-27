import { useEffect, useState } from "react";

async function copyToClipboard(text) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 비HTTPS 환경 등에서 Clipboard API가 거절되면 아래 폴백을 시도한다.
    }
  }
  if (typeof document === "undefined" || !document.body) return false;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-9999px";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.focus();
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  } finally {
    document.body.removeChild(area);
  }
  return copied;
}

export default function PromptCopy({ prompt }) {
  const [copyResult, setCopyResult] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const status = copyResult?.prompt === prompt ? copyResult : null;

  useEffect(() => {
    if (!status?.copied) return;
    const timer = setTimeout(() => setCopyResult(null), 4000);
    return () => clearTimeout(timer);
  }, [status]);

  const handleCopy = async () => {
    const copied = await copyToClipboard(prompt);
    setCopyResult({ prompt, copied });
    if (!copied) setPreviewOpen(true);
  };

  return (
    <section className="explanation">
      <div className="explanation-controls">
        <button type="button" className="explanation-copy" onClick={handleCopy}>
          AI 해설 프롬프트 복사
        </button>
        {status && <span className="muted small-text" role="status">{
          status.copied
            ? "프롬프트를 복사했습니다. ChatGPT·Claude·Gemini 등에 붙여넣어 해설을 받아 보세요."
            : "클립보드 복사에 실패했습니다. 아래 프롬프트를 직접 선택해 복사하세요."
        }</span>}
      </div>
      <details
        className="explanation-preview"
        open={previewOpen}
        onToggle={(event) => setPreviewOpen(event.currentTarget.open)}
      >
        <summary className="muted small-text">프롬프트 미리보기</summary>
        <textarea
          className="explanation-prompt"
          readOnly
          rows={12}
          value={prompt}
          onFocus={(event) => event.target.select()}
          aria-label="AI 해설 프롬프트"
        />
      </details>
    </section>
  );
}
