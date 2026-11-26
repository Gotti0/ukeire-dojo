import { useCallback, useEffect, useState } from "react";
import { doraOf, sortTiles, tileImage, tileName, tileType } from "./tiles.js";

const WINDS = "동남서북";
const ROUND_WIND = { E: "동", S: "남", W: "서", N: "북" };
const RELATIVE = ["나", "하가", "대면", "상가"];

const DEFAULT_FILTERS = { minLoss: "0.01", disagree: "1", oppRiichi: "any", menzen: "any" };

function Tile({ code, small, dim, sideways, onClick, marks = [], disabled }) {
  const className = ["tile", small && "small", dim && "dim", sideways && "sideways", ...marks].filter(Boolean).join(" ");
  const img = <img src={tileImage(code)} alt={tileName(code)} title={tileName(code)} draggable={false} />;
  if (!onClick) return <span className={className}>{img}</span>;
  return (
    <button type="button" className={className} onClick={onClick} disabled={disabled} aria-label={`${tileName(code)} 버리기`}>
      {img}
    </button>
  );
}

function Melds({ melds }) {
  return (
    <span className="melds">
      {melds.map((meld, i) => (
        <span className="meld" key={i}>
          {meld.tiles.map((t, j) => (
            <Tile key={j} code={meld.type === "ANKAN" && (j === 0 || j === 3) ? "back" : t} small sideways={j === meld.calledIndex} />
          ))}
        </span>
      ))}
    </span>
  );
}

function Seat({ problem, seat, position }) {
  const player = problem.players[seat];
  const wind = WINDS[(seat - problem.dealer + 4) % 4];
  return (
    <div className={`seat seat-${position}`}>
      <div className="seat-label">
        <b>{RELATIVE[(seat - problem.seat + 4) % 4]}</b> {wind}가 · {player.score.toLocaleString()}점
        {player.riichi && <span className="badge riichi">리치</span>}
      </div>
      <div className="river">
        {player.river.map((t, i) => (
          <Tile key={i} code={t.tile} small dim={t.called} sideways={t.riichi} marks={t.tsumogiri ? ["tsumogiri"] : []} />
        ))}
      </div>
      {player.melds.length > 0 && <Melds melds={player.melds} />}
    </div>
  );
}

function TableView({ problem }) {
  const seatAt = (offset) => (problem.seat + offset) % 4;
  return (
    <div className="table">
      <Seat problem={problem} seat={seatAt(2)} position="top" />
      <Seat problem={problem} seat={seatAt(3)} position="left" />
      <div className="center">
        <div className="round">
          {ROUND_WIND[problem.round[0]]}
          {problem.round[1]}국 {problem.honba}본장 · 공탁 {problem.kyotaku}
        </div>
        <div>
          {problem.junme}순째 · 남은 패 {problem.remaining}
        </div>
        <div className="dora">
          도라 표시패 {problem.dora.map((d, i) => <Tile key={i} code={d} small />)}
          <span className="muted">(도라 {problem.dora.map((d) => tileName(doraOf(d))).join(", ")})</span>
        </div>
      </div>
      <Seat problem={problem} seat={seatAt(1)} position="right" />
      <Seat problem={problem} seat={problem.seat} position="bottom" />
    </div>
  );
}

function Hand({ problem, result, onDiscard, busy }) {
  const legal = new Set(problem.legal);
  const marksFor = (code) => {
    if (!result) return [];
    const type = tileType(code);
    return [
      type === result.tile && "chosen",
      type === result.ai_best && "ai-best",
      result.eff_best.includes(type) && "eff-best",
    ].filter(Boolean);
  };
  const tileButton = (code, key) => (
    <Tile
      key={key}
      code={code}
      marks={marksFor(code)}
      disabled={busy || Boolean(result) || !legal.has(tileType(code))}
      onClick={() => onDiscard(code)}
    />
  );
  return (
    <div className="hand">
      {sortTiles(problem.hand).map((code, i) => tileButton(code, i))}
      {problem.draw && <span className="draw-gap" />}
      {problem.draw && tileButton(problem.draw, "draw")}
    </div>
  );
}

const fmt = (v) => (v === null || v === undefined ? "—" : v.toFixed(3));

function Result({ result, onNext }) {
  const verdict = result.matches_ai
    ? "AI 최선과 같습니다."
    : `AI 최선은 ${tileName(result.ai_best)}${result.ai_best_riichi ? "(리치)" : ""}입니다. 고른 수의 손실 ${fmt(result.chosen_ai_loss)}`;
  return (
    <div className="result">
      <div className={`verdict ${result.matches_ai ? "good" : "bad"}`}>{verdict}</div>
      <div className="muted">
        효율 최선: {result.eff_best.map(tileName).join(", ")}
        {result.matches_efficiency ? " (고른 수가 효율 최선)" : ""} · 효율 최선의 AI 손실 {fmt(result.eff_loss)}
      </div>
      <table>
        <thead>
          <tr>
            <th>버릴 패</th>
            <th>AI 손실</th>
            <th>리치 시</th>
            <th>샨텐</th>
            <th>유효패</th>
            <th>유효 종류</th>
          </tr>
        </thead>
        <tbody>
          {result.table.map((row) => (
            <tr
              key={row.tile}
              className={[row.tile === result.tile && "chosen-row", row.tile === result.ai_best && "ai-row"].filter(Boolean).join(" ")}
            >
              <td>
                <Tile code={row.tile} small /> {tileName(row.tile)}
              </td>
              <td>{fmt(row.ai_loss)}</td>
              <td>{fmt(row.ai_riichi)}</td>
              <td>{row.shanten === 0 ? "텐파이" : row.shanten}</td>
              <td>{row.ukeire}</td>
              <td className="waits">{row.waits.map(tileName).join(" ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small-text">
        손실은 Epsilon-Prism v760이 추정한 순위 효용(1~4위 = 2/3, 1/3, 0, −1)의 최선수 대비 차이입니다. 유효패는 내 손패와 보이는 패를 뺀 남은
        장수입니다.
      </p>
      <button type="button" className="next" onClick={onNext}>
        다음 문제 (Enter)
      </button>
    </div>
  );
}

function Filters({ filters, onChange }) {
  const select = (key, label, options) => (
    <label>
      {label}
      <select value={filters[key]} onChange={(e) => onChange({ ...filters, [key]: e.target.value })}>
        {options.map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <div className="filters">
      {select("minLoss", "효율 최선의 AI 손실 ≥", [["0", "0"], ["0.005", "0.005"], ["0.01", "0.01"], ["0.02", "0.02"], ["0.05", "0.05"]])}
      {select("disagree", "효율·AI", [["1", "갈리는 문제만"], ["any", "전부"], ["0", "같은 문제만"]])}
      {select("oppRiichi", "상대 리치", [["any", "상관없음"], ["1", "있음"], ["0", "없음"]])}
      {select("menzen", "내 손", [["any", "상관없음"], ["1", "문전"], ["0", "울기"]])}
    </div>
  );
}

export default function App() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [problem, setProblem] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [score, setScore] = useState({ solved: 0, aiMatch: 0, lossSum: 0 });

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    const query = new URLSearchParams({
      min_loss: filters.minLoss,
      disagree: filters.disagree,
      opp_riichi: filters.oppRiichi,
      menzen: filters.menzen,
    });
    try {
      const response = await fetch(`/api/problem?${query}`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || response.statusText);
      setProblem(body);
    } catch (e) {
      setProblem(null);
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  const discard = async (code) => {
    setBusy(true);
    try {
      const response = await fetch("/api/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: problem.id, tile: code }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || response.statusText);
      setResult(body);
      setScore((s) => ({
        solved: s.solved + 1,
        aiMatch: s.aiMatch + (body.matches_ai ? 1 : 0),
        lossSum: s.lossSum + body.chosen_ai_loss,
      }));
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Enter" && result && !busy) load();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [result, busy, load]);

  return (
    <div className="app">
      <header>
        <h1>ukeire-dojo</h1>
        <span className="muted">
          푼 문제 {score.solved} · AI 일치 {score.aiMatch} · 평균 손실 {score.solved ? (score.lossSum / score.solved).toFixed(3) : "—"}
        </span>
      </header>
      <Filters filters={filters} onChange={setFilters} />
      {error && <div className="error">{error}</div>}
      {problem && (
        <>
          <TableView problem={problem} />
          <div className="prompt">{result ? "결과" : "무엇을 버릴까요?"}</div>
          <Hand problem={problem} result={result} onDiscard={discard} busy={busy} />
          {problem.players[problem.seat].melds.length > 0 && (
            <div className="my-melds">
              울기 <Melds melds={problem.players[problem.seat].melds} />
            </div>
          )}
          {result && <Result result={result} onNext={load} />}
        </>
      )}
      <footer className="muted small-text">
        패 이미지: Euophrys Riichi-Trainer (CC BY 4.0) · AI: Epsilon-Prism v760 (가중치 MIT). 개인 학습용이며 온라인 대국 보조에 쓰지 않습니다.
      </footer>
    </div>
  );
}
