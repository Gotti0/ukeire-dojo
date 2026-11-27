import assert from "node:assert/strict";
import test from "node:test";
import { buildDiscardHintPrompt, buildExplanationPrompt } from "./prompt.js";

function makeProblem(overrides = {}) {
  return {
    id: 42,
    round: "E2",
    honba: 1,
    kyotaku: 1,
    dealer: 1,
    seat: 0,
    junme: 8,
    remaining: 41,
    dora: ["4m"],
    hand: ["5mr", "1m", "2m", "4p", "5p", "6p", "7s", "8s", "9s", "E", "E", "5s", "6s"],
    draw: "C",
    players: [
      {
        seat: 0,
        score: 25000,
        riichi: false,
        river: [{ tile: "9p" }, { tile: "1s", tsumogiri: true }],
        melds: [],
        concealed: 14,
      },
      {
        seat: 1,
        score: 31200,
        riichi: true,
        river: [{ tile: "N" }, { tile: "1p", riichi: true, called: true }],
        melds: [],
        concealed: 13,
      },
      {
        seat: 2,
        score: 22800,
        riichi: false,
        river: [{ tile: "S" }],
        melds: [{ type: "PON", tiles: ["W", "W", "W"], from: 0, calledIndex: 1, addedIndex: -1 }],
        concealed: 10,
      },
      {
        seat: 3,
        score: 21000,
        riichi: false,
        river: [],
        melds: [],
        concealed: 13,
      },
    ],
    legal: ["5s", "C", "E"],
    ...overrides,
  };
}

function makeResult(overrides = {}) {
  return {
    id: 42,
    tile: "C",
    ai_best: "E",
    ai_best_riichi: false,
    eff_best: ["C"],
    chosen_ai_loss: 0.025,
    eff_loss: 0.025,
    matches_ai: false,
    matches_efficiency: true,
    table: [
      { tile: "E", shanten: 1, ukeire: 20, waits: ["4s", "7s"], ai_dahai: 0.0, ai_riichi: null, ai_loss: 0.0 },
      { tile: "C", shanten: 0, ukeire: 7, waits: ["4s", "7s"], ai_dahai: 0.025, ai_riichi: 0.031, ai_loss: 0.025 },
      { tile: "5s", shanten: 2, ukeire: 12, waits: [], ai_dahai: 0.12, ai_riichi: null, ai_loss: 0.12 },
    ],
    ...overrides,
  };
}

test("mismatch prompt includes full table context, candidate table, and comparison questions", () => {
  const prompt = buildExplanationPrompt(makeProblem(), makeResult());

  assert.match(prompt, /동2국 1본장 · 공탁 1개 · 8순째 · 남은 패 41장/);
  assert.match(prompt, /도라 표시패: 4만\(4m\) -> 실제 도라: 5만\(5m\)/);
  assert.match(prompt, /내 손패: 1m 2m 5mr 4p 5p 6p 5s 6s 7s 8s 9s E E \/ 쯔모: 중\(C\)/);
  assert.match(prompt, /- 나\(북가\) · 25,000점 · 비리치 · 문전 · 비공개패 14장/);
  assert.match(prompt, /- 하가\(동가\) · 31,200점 · 리치 중 · 문전 · 비공개패 13장/);
  assert.match(prompt, /버림패: N 1p\(리치,울림\)/);
  assert.match(prompt, /- 대면\(남가\) · 22,800점 · 비리치 · 울기 1개 · 비공개패 10장/);
  assert.match(prompt, /울기: 퐁\(W W W\)/);
  assert.match(prompt, /- 상가\(서가\) · 21,000점 · 비리치 · 문전 · 비공개패 13장/);
  assert.match(prompt, /\| 동\(E\) \| 0\.000 \| — \| 1 \| 20 \| 4s 7s \| AI 최선 \|/);
  assert.match(prompt, /\| 중\(C\) \| 0\.025 \| 0\.031 \| 텐파이 \| 7 \| 4s 7s \| 내 선택, 효율 최선 \|/);
  assert.match(prompt, /AI 최선 불일치 · 효율 최선 일치/);
  assert.match(prompt, /AI 최선 타패가 내 선택보다 유리한 이유/);
});

test("match prompt uses combined tag and match-specific questions", () => {
  const prompt = buildExplanationPrompt(
    makeProblem(),
    makeResult({
      tile: "E",
      chosen_ai_loss: 0.0,
      matches_ai: true,
      matches_efficiency: false,
      ai_best_riichi: true,
    }),
  );

  assert.match(prompt, /AI 최선: 동\(E\) \(리치 권장\)/);
  assert.match(prompt, /\| 동\(E\) \| 0\.000 \| — \| 1 \| 20 \| 4s 7s \| AI 최선, 내 선택 \|/);
  assert.match(prompt, /AI 최선 일치 · 효율 최선 불일치/);
  assert.match(prompt, /내 선택\(AI 최선\)이 다른 상위 후보 타패/);
});

test("handles post-call discard (draw=null) and empty waits", () => {
  const prompt = buildExplanationPrompt(makeProblem({ draw: null }), makeResult());

  assert.match(prompt, /울기 직후 타패\(쯔모 없음\)/);
  assert.match(prompt, /\| 5삭\(5s\) \| 0\.120 \| — \| 2 \| 12 \| — \| — \|/);
});

test("live hint explains the current East round using only the visible table and discard losses", () => {
  const table = makeProblem({ draw: null, hiddenWall: ["SECRET"], roomToken: "PRIVATE" });
  const round = { kyoku: 3, honba: 2 };
  const hint = {
    bestTile: "5mr",
    candidates: [{ tile: "5mr", loss: 0 }, { tile: "E", loss: 0.0246 }],
  };

  const prompt = buildDiscardHintPrompt(table, round, hint);

  assert.match(prompt, /동풍전/);
  assert.match(prompt, /동3국 2본장 · 공탁 1개 · 남은 패 41장/);
  assert.match(prompt, /울기 직후 타패/);
  assert.match(prompt, /하가\(동가\).*리치 중/);
  assert.match(prompt, /AI 추천 타패: 적5만\(5mr\)/);
  assert.match(prompt, /\| 동\(E\) \| 0\.025 \|/);
  assert.doesNotMatch(prompt, /반장전|내 선택|효율 최선|SECRET|PRIVATE|undefined/);
});
