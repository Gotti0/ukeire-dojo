import { doraOf, sortTiles, tileName } from "./tiles.js";

const WINDS = "동남서북";
const ROUND_WIND = { E: "동", S: "남", W: "서", N: "북" };
const RELATIVE = ["나", "하가", "대면", "상가"];
const MELD_LABELS = {
  CHI: "치",
  PON: "퐁",
  MINKAN: "대명깡",
  ANKAN: "안깡",
  KAKAN: "가깡",
};

const fmtLoss = (value) => (value === null || value === undefined ? "—" : value.toFixed(3));
const fmtShanten = (shanten) => (shanten === 0 ? "텐파이" : String(shanten));
const fmtShantenSummary = (row) => (row ? (row.shanten === 0 ? "텐파이" : `${row.shanten}샨텐`) : "—");
const fmtTile = (code) => `${tileName(code)}(${code})`;

function formatRiver(river) {
  if (!river || river.length === 0) return "없음";
  return river
    .map((entry) => {
      const tags = [entry.riichi && "리치", entry.called && "울림", entry.tsumogiri && "쯔모기리"].filter(Boolean);
      return tags.length > 0 ? `${entry.tile}(${tags.join(",")})` : entry.tile;
    })
    .join(" ");
}

function formatMelds(melds) {
  if (!melds || melds.length === 0) return "없음";
  return melds.map((meld) => `${MELD_LABELS[meld.type] || meld.type}(${meld.tiles.join(" ")})`).join(", ");
}

function rowNotes(row, result) {
  const tags = [];
  if (row.tile === result.ai_best) tags.push("AI 최선");
  if (row.tile === result.tile) tags.push("내 선택");
  if (result.eff_best.includes(row.tile)) tags.push("효율 최선");
  return tags.join(", ") || "—";
}

export function buildExplanationPrompt(problem, result) {
  const roundLabel = `${ROUND_WIND[problem.round[0]] ?? problem.round[0]}${problem.round.slice(1)}국`;
  const doraIndicators = problem.dora.map(fmtTile).join(", ");
  const actualDora = problem.dora.map((d) => fmtTile(doraOf(d))).join(", ");
  const handList = sortTiles(problem.hand).join(" ");
  const drawText = problem.draw ? `쯔모: ${fmtTile(problem.draw)}` : "울기 직후 타패(쯔모 없음)";

  const playerLines = [0, 1, 2, 3].map((offset) => {
    const seat = (problem.seat + offset) % 4;
    const player = problem.players[seat];
    const wind = WINDS[(seat - problem.dealer + 4) % 4];
    const role = RELATIVE[offset];
    const riichiText = player.riichi ? "리치 중" : "비리치";
    const handKind = player.melds.length > 0 ? `울기 ${player.melds.length}개` : "문전";
    return [
      `- ${role}(${wind}가) · ${player.score.toLocaleString()}점 · ${riichiText} · ${handKind} · 비공개패 ${player.concealed}장`,
      `  - 울기: ${formatMelds(player.melds)}`,
      `  - 버림패: ${formatRiver(player.river)}`,
    ].join("\n");
  });

  const chosenRow = result.table.find((row) => row.tile === result.tile);
  const bestRow = result.table.find((row) => row.tile === result.ai_best);
  const effBestText = result.eff_best.map(fmtTile).join(", ");

  const tableLines = [
    "| 타패 | AI 손실 | 리치 손실 | 샨텐 | 유효패 | 유효 종류 | 비고 |",
    "| --- | ---: | ---: | --- | ---: | --- | --- |",
    ...result.table.map((row) => {
      const waitsText = row.waits.length > 0 ? row.waits.join(" ") : "—";
      return `| ${fmtTile(row.tile)} | ${fmtLoss(row.ai_loss)} | ${fmtLoss(row.ai_riichi)} | ${fmtShanten(row.shanten)} | ${row.ukeire} | ${waitsText} | ${rowNotes(row, result)} |`;
    }),
  ];

  const questions = result.matches_ai
    ? [
        "1. 내 선택(AI 최선)이 다른 상위 후보 타패(특히 효율 최선 또는 차선 후보)보다 유리한 이유를 타점·도라·역, 속도·유효패, 수비·상대 리치/울기, 점수·순위 상황 관점에서 설명해 주세요.",
        "2. 이 국면에서 AI 최선을 결정지은 가장 핵심적인 요인 한 가지를 짚어 주세요.",
        "3. 비슷한 실전 국면에서 바로 떠올릴 수 있는 판단 기준 1~2가지를 요약해 주세요.",
      ]
    : [
        "1. AI 최선 타패가 내 선택보다 유리한 이유를 타점·도라·역, 속도·유효패, 수비·상대 리치/울기, 점수·순위 상황 관점에서 비교해 주세요.",
        "2. 내가 고른 타패의 위험 요소 또는 기회비용은 무엇인지 설명해 주세요.",
        "3. 비슷한 실전 국면에서 실수를 줄이기 위한 핵심 판단 기준 1~2가지를 요약해 주세요.",
      ];

  return [
    "당신은 4인 리치 마작(반장전, 적도라 있음) 전략 해설가입니다.",
    "아래에 주어진 국면 정보와 Epsilon-Prism v760 채점 수치만을 근거로 한국어로 명확하게 해설해 주세요.",
    "",
    "## 해설 규칙",
    "- 제공된 패, 점수, 버림패, 울기, 수치만 사용하고 임의로 수치를 바꾸거나 확인할 수 없는 상대 손패·패산을 단정하지 마세요.",
    "- AI 손실은 순위 효용(1~4위 = +2/3, +1/3, 0, -1)의 최선수 대비 차이이며, 방총 확률이 아닙니다.",
    "- 패 표기: m=만, p=통, s=삭, r=적5, E/S/W/N=동/남/서/북, P/F/C=백/발/중.",
    "",
    "## 국면 정보",
    `- 장/국: ${roundLabel} ${problem.honba}본장 · 공탁 ${problem.kyotaku}개 · ${problem.junme}순째 · 남은 패 ${problem.remaining}장`,
    `- 도라 표시패: ${doraIndicators} -> 실제 도라: ${actualDora}`,
    `- 내 손패: ${handList} / ${drawText}`,
    "- 테이블 상태:",
    ...playerLines,
    "",
    "## 채점 결과 요약",
    `- 내 선택: ${fmtTile(result.tile)} (AI 손실 ${fmtLoss(result.chosen_ai_loss)}, ${fmtShantenSummary(chosenRow)}, 유효패 ${chosenRow ? chosenRow.ukeire : "—"}장)`,
    `- AI 최선: ${fmtTile(result.ai_best)}${result.ai_best_riichi ? " (리치 권장)" : ""} (AI 손실 ${fmtLoss(bestRow?.ai_loss)}, ${fmtShantenSummary(bestRow)}, 유효패 ${bestRow ? bestRow.ukeire : "—"}장)`,
    `- 효율 최선: ${effBestText} (효율 최선의 AI 손실 ${fmtLoss(result.eff_loss)})`,
    `- 판정: AI 최선 ${result.matches_ai ? "일치" : "불일치"} · 효율 최선 ${result.matches_efficiency ? "일치" : "불일치"}`,
    "",
    "## 전체 후보 타패 표",
    ...tableLines,
    "",
    "## 질문",
    ...questions,
  ].join("\n");
}
