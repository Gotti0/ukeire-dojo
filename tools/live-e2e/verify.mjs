import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const playwrightRoot = process.env.PLAYWRIGHT_ROOT;
if (!playwrightRoot) {
  throw new Error("PLAYWRIGHT_ROOT is required (run via tools/live-e2e/run.ps1)");
}
const { chromium } = require(playwrightRoot);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");

const deviceIdx = process.argv.indexOf("--device");
const DEVICE = deviceIdx >= 0 && process.argv[deviceIdx + 1] ? process.argv[deviceIdx + 1] : "gpu0";

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

async function waitForServer(url) {
  for (let i = 0; i < 80; i++) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("ukeire_dojo.server failed to start");
}

function summarize(samples) {
  assert.ok(samples.length > 0, "no timing samples collected");
  const sorted = [...samples].sort((a, b) => a - b);
  const p50 = sorted[Math.floor((sorted.length - 1) * 0.5)];
  const p95 = sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)];
  const max = sorted[sorted.length - 1];
  return {
    n: sorted.length,
    p50_ms: Number(p50.toFixed(1)),
    p95_ms: Number(p95.toFixed(1)),
    max_ms: Number(max.toFixed(1)),
  };
}

function assertSeatIsolation(payload, allTokens) {
  const raw = JSON.stringify(payload);
  for (const tok of allTokens) {
    assert.ok(!raw.includes(tok), "player token leaked into room state payload");
  }
  for (const forbidden of ['"wall":', '"seed":', '"pendingSeats":', '"pending_submissions":']) {
    assert.ok(!raw.includes(forbidden), `forbidden field ${forbidden} leaked`);
  }
  if (payload.match?.table?.players) {
    for (const p of payload.match.table.players) {
      assert.equal("hand" in p, false, "opponent hand leaked in table.players");
      assert.equal("draw" in p, false, "opponent draw leaked in table.players");
    }
  }
}

async function apiFetch(baseUrl, pathSuffix, { method = "GET", token = null, body = null } = {}) {
  const headers = {};
  if (token) headers["X-Player-Token"] = token;
  if (body !== null) headers["Content-Type"] = "application/json";
  const res = await fetch(`${baseUrl}${pathSuffix}`, {
    method,
    headers,
    body: body !== null ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, data };
}

async function getSession(page) {
  return page.evaluate(() => JSON.parse(sessionStorage.getItem("ukeire_mp_session")));
}

async function createRoomViaUi(page, baseUrl, name) {
  await page.goto(baseUrl);
  await page.getByRole("button", { name: "동풍전 멀티플레이" }).click();
  await page.getByPlaceholder("표시할 이름").fill(name);
  await page.getByRole("button", { name: "새 방 만들기" }).click();
  await page.waitForSelector(".mp-lobby-head h2");
  const heading = await page.locator(".mp-lobby-head h2").textContent();
  const roomCode = heading.match(/초대 방 · ([A-Z0-9]{6})/)[1];
  const session = await getSession(page);
  return { roomCode, session };
}

async function joinRoomViaUi(page, baseUrl, roomCode, name) {
  await page.goto(`${baseUrl}/?room=${roomCode}`);
  await page.getByPlaceholder("표시할 이름").fill(name);
  await page.getByRole("button", { name: "방 입장" }).click();
  await page.waitForSelector(".seat-card");
  return getSession(page);
}

async function stepPage(page, { actionTimes = null, hintTimes = null, requestHints = false, hintTargetCount = 0 } = {}) {
  const nextBtn = page.getByRole("button", { name: "다음 국 진행" });
  if ((await nextBtn.isVisible().catch(() => false)) && (await nextBtn.isEnabled().catch(() => false))) {
    await nextBtn.click();
    return true;
  }
  if (requestHints && (!hintTargetCount || (hintTimes && hintTimes.length < hintTargetCount))) {
    const hintBtn = page.getByRole("button", { name: "AI 타패 추천 보기" });
    if ((await hintBtn.isVisible().catch(() => false)) && (await hintBtn.isEnabled().catch(() => false))) {
      const hasPanel = (await page.locator(".hint-panel").count()) > 0;
      if (!hasPanel) {
        const t0 = performance.now();
        await hintBtn.click();
        await page.locator(".hint-panel").waitFor({ timeout: 15_000 });
        if (hintTimes) hintTimes.push(performance.now() - t0);
      }
    }
  }
  for (const label of ["쯔모 화료", "론", "패스"]) {
    const btn = page.locator(".action-bar button", { hasText: label }).first();
    if ((await btn.isVisible().catch(() => false)) && (await btn.isEnabled().catch(() => false))) {
      const t0 = performance.now();
      const postPromise = page.waitForResponse(
        (r) => r.url().includes("/action") && r.request().method() === "POST",
        { timeout: 15_000 }
      );
      await btn.click();
      await postPromise;
      if (actionTimes) actionTimes.push(performance.now() - t0);
      return true;
    }
  }
  const tileBtn = page.locator(".hand button.tile:not(:disabled)").first();
  if (await tileBtn.isVisible().catch(() => false)) {
    const t0 = performance.now();
    const postPromise = page.waitForResponse(
      (r) => r.url().includes("/action") && r.request().method() === "POST",
      { timeout: 15_000 }
    );
    await tileBtn.click();
    await postPromise;
    if (actionTimes) actionTimes.push(performance.now() - t0);
    return true;
  }
  return false;
}

async function driveToHeading(pages, headingText, opts = {}) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    const waiting = [];
    for (const page of pages) {
      if (!(await page.getByText(headingText).isVisible())) waiting.push(page);
    }
    if (waiting.length === 0) return;
    let acted = false;
    for (const page of waiting) {
      if (await stepPage(page, opts)) acted = true;
    }
    await new Promise((resolve) => setTimeout(resolve, acted ? 45 : 40));
  }
  throw new Error(`Timed out waiting for "${headingText}"`);
}

async function runSoloScenario(browser, baseUrl) {
  const page = await browser.newPage();
  try {
    await createRoomViaUi(page, baseUrl, "솔로");
    await page.getByRole("button", { name: "동풍전 시작" }).click();
    await page.getByText("싱글플레이 (타패 추천 가능)").waitFor();
    await page.getByRole("button", { name: "AI 타패 추천 보기" }).click();
    await page.locator(".hint-panel").waitFor();
    await page.getByRole("button", { name: "AI 해설 프롬프트 복사" }).waitFor();

    await driveToHeading([page], "싱글플레이 동풍전 종료");
    assert.equal(await page.locator(".result tbody tr").count(), 4);
    assert.equal(await page.getByRole("button", { name: "같은 패산으로 2경기 시작" }).count(), 0);
    await page.getByRole("button", { name: "방 나가기" }).click();
    await page.getByRole("button", { name: "새 방 만들기" }).waitFor();
    return { hintOnFirstTurn: true, finishedAfterOneMatch: true };
  } finally {
    await page.close();
  }
}

async function runScenarioA(browser, baseUrl) {
  const ctxA = await browser.newContext();
  const ctxB = await browser.newContext();
  const pageA = await ctxA.newPage();
  const pageB = await ctxB.newPage();
  const actionTimes = [];
  const hintTimes = [];
  try {
    const { roomCode, session: sessA } = await createRoomViaUi(pageA, baseUrl, "동가A");
    const sessB = await joinRoomViaUi(pageB, baseUrl, roomCode, "남가B");
    const allTokens = [sessA.token, sessB.token];

    await pageA.getByRole("button", { name: "준비 완료" }).click();
    await pageB.getByRole("button", { name: "준비 완료" }).click();
    await pageA.getByRole("button", { name: "동풍전 시작" }).click();

    await pageA.getByText("1경기 (AI 도움 없음)").waitFor({ timeout: 30_000 });
    await pageB.getByText("1경기 (AI 도움 없음)").waitFor({ timeout: 30_000 });
    assert.equal(await pageA.getByRole("button", { name: "AI 타패 추천 보기" }).count(), 0);

    // 좌석별 정보 분리 및 1경기 추천 거부 확인
    const stA1 = await apiFetch(baseUrl, `/api/rooms/${roomCode}`, { token: sessA.token });
    const stB1 = await apiFetch(baseUrl, `/api/rooms/${roomCode}`, { token: sessB.token });
    assertSeatIsolation(stA1.data, allTokens);
    assertSeatIsolation(stB1.data, allTokens);
    assert.equal(stB1.data.match.table.draw, null);
    assert.equal(stB1.data.match.decision, null);

    const hintM1 = await apiFetch(baseUrl, `/api/rooms/${roomCode}/hint`, { method: "POST", token: sessA.token, body: {} });
    assert.equal(hintM1.status, 403);

    // 1경기 동1국 0본장 양쪽 초기 패 기록
    const firstHandA = await pageA.locator(".hand .tile img").evaluateAll((imgs) => imgs.map((i) => i.alt));
    const firstHandB = await pageB.locator(".hand .tile img").evaluateAll((imgs) => imgs.map((i) => i.alt));

    // 중복·지연·불법 입력 검증 (동가 첫 차례)
    const rev0 = stA1.data.match.decision.revision;
    const legal0 = stA1.data.match.decision.actions[0].code;
    const badRes = await apiFetch(baseUrl, `/api/rooms/${roomCode}/action`, {
      method: "POST",
      token: sessA.token,
      body: { revision: rev0, action: -999 },
    });
    assert.equal(badRes.status, 400);
    const [dup1, dup2] = await Promise.all([
      apiFetch(baseUrl, `/api/rooms/${roomCode}/action`, { method: "POST", token: sessA.token, body: { revision: rev0, action: legal0 } }),
      apiFetch(baseUrl, `/api/rooms/${roomCode}/action`, { method: "POST", token: sessA.token, body: { revision: rev0, action: legal0 } }),
    ]);
    assert.ok(
      [dup1.status, dup2.status].every((s) => s === 200 || s === 409) && (dup1.status === 200 || dup2.status === 200),
      `unexpected duplicate action statuses: ${dup1.status}, ${dup2.status}`
    );
    const afterDup = await apiFetch(baseUrl, `/api/rooms/${roomCode}`, { token: sessA.token });
    assert.equal(afterDup.data.match.table.players[0].river.length, 1, "duplicate request advanced twice");
    const staleRes = await apiFetch(baseUrl, `/api/rooms/${roomCode}/action`, {
      method: "POST",
      token: sessA.token,
      body: { revision: rev0, action: legal0 },
    });
    assert.equal(staleRes.status, 409);

    // 새로고침 복귀 검증 (유예 2.0초 내 자동 재연결, AI 대타 미발동)
    await pageB.reload();
    await pageB.getByText(`방 ${roomCode} · 1경기 (AI 도움 없음)`).waitFor({ timeout: 10_000 });
    await pageB.locator(".seat-bottom").getByText("남가B").waitFor();
    assert.equal(await pageA.locator(".takeover-badge").count(), 0);

    // 1경기 완주
    await driveToHeading([pageA, pageB], "1경기 동풍전 종료", { actionTimes });
    const m1End = await apiFetch(baseUrl, `/api/rooms/${roomCode}`, { token: sessA.token });
    assert.equal(m1End.data.firstMatchResult.scores.reduce((a, b) => a + b, 0), 100000);

    // 2경기 시작 및 동일 패산·배패 확인
    await pageA.getByRole("button", { name: "같은 패산으로 2경기 시작" }).click();
    await pageA.getByText("2경기 (동일 패산 · 타패 추천 가능)").waitFor({ timeout: 20_000 });
    await pageB.getByText("2경기 (동일 패산 · 타패 추천 가능)").waitFor({ timeout: 20_000 });

    const secondHandA = await pageA.locator(".hand .tile img").evaluateAll((imgs) => imgs.map((i) => i.alt));
    const secondHandB = await pageB.locator(".hand .tile img").evaluateAll((imgs) => imgs.map((i) => i.alt));
    assert.deepEqual(secondHandA, firstHandA, "2경기 A 초기 패가 1경기와 다릅니다");
    assert.deepEqual(secondHandB, firstHandB, "2경기 B 초기 패가 1경기와 다릅니다");

    const stA2 = await apiFetch(baseUrl, `/api/rooms/${roomCode}`, { token: sessA.token });
    const stB2 = await apiFetch(baseUrl, `/api/rooms/${roomCode}`, { token: sessB.token });
    assertSeatIsolation(stA2.data, allTokens);
    assertSeatIsolation(stB2.data, allTokens);

    // 2경기 요청형 추천(요청자만 표시) + 지연 응답 차단 확인
    await pageA.getByRole("button", { name: "AI 타패 추천 보기" }).click();
    await pageA.locator(".hint-panel").waitFor({ timeout: 15_000 });
    assert.ok((await pageA.locator(".hand .ai-best").count()) >= 1);
    assert.equal(await pageB.locator(".hint-panel").count(), 0);

    const copy = pageA.getByRole("button", { name: "AI 해설 프롬프트 복사" });
    await ctxA.grantPermissions(["clipboard-read", "clipboard-write"], { origin: baseUrl });
    await copy.click();
    await pageA.getByRole("status").filter({ hasText: "프롬프트를 복사했습니다" }).waitFor();
    const copiedPrompt = await pageA.evaluate(() => navigator.clipboard.readText());
    assert.match(copiedPrompt, /동1국 0본장/);
    assert.match(copiedPrompt, /\| 타패 \| AI 손실 \|/);
    assert.equal(await pageB.getByRole("button", { name: "AI 해설 프롬프트 복사" }).count(), 0);

    let delayedOnce = false;
    await pageA.route("**/api/rooms/*/hint", async (route) => {
      if (!delayedOnce) {
        delayedOnce = true;
        await new Promise((r) => setTimeout(r, 700));
      }
      await route.continue();
    });
    await pageA.getByRole("button", { name: "AI 타패 추천 보기" }).click();
    await pageA.locator(".hand button.tile:not(:disabled)").first().click();
    await new Promise((r) => setTimeout(r, 900));
    assert.equal(await pageA.locator(".hint-panel").count(), 0, "지연 도착한 이전 추천이 표시되면 안 됩니다");
    await pageA.unroute("**/api/rooms/*/hint");

    // 2경기 완주 (양 브라우저에서 최소 16회 이상 추천 왕복 측정)
    await driveToHeading([pageA, pageB], "1경기 vs 2경기 결과 비교", {
      actionTimes,
      hintTimes,
      requestHints: true,
      hintTargetCount: 16,
    });
    assert.equal(await pageA.locator(".result table tbody tr").count(), 4);
    assert.equal(await pageB.locator(".result table tbody tr").count(), 4);
    const seriesEnd = await apiFetch(baseUrl, `/api/rooms/${roomCode}`, { token: sessA.token });
    assert.equal(seriesEnd.data.secondMatchResult.scores.reduce((a, b) => a + b, 0), 100000);

    return {
      roomCode,
      action: summarize(actionTimes),
      hint: summarize(hintTimes),
      firstScores: seriesEnd.data.firstMatchResult.scores,
      secondScores: seriesEnd.data.secondMatchResult.scores,
    };
  } finally {
    await ctxA.close();
    await ctxB.close();
  }
}

async function runScenarioB(browser, baseUrl) {
  const ctxA = await browser.newContext();
  const ctxB = await browser.newContext();
  const pageA = await ctxA.newPage();
  const pageB = await ctxB.newPage();
  let ctxB2;
  try {
    const { roomCode } = await createRoomViaUi(pageA, baseUrl, "방장B");
    const sessB = await joinRoomViaUi(pageB, baseUrl, roomCode, "이탈자B");
    await pageA.getByRole("button", { name: "준비 완료" }).click();
    await pageB.getByRole("button", { name: "준비 완료" }).click();
    await pageA.getByRole("button", { name: "동풍전 시작" }).click();
    await pageA.getByText("1경기 (AI 도움 없음)").waitFor({ timeout: 20_000 });
    await pageB.getByText("1경기 (AI 도움 없음)").waitFor({ timeout: 20_000 });

    // B 브라우저 종료 -> 2.0초 유예 후 AI 대타 전환 확인
    await ctxB.close();
    await pageA.getByText("AI 대타 중").waitFor({ timeout: 8_000 });

    // A가 한 수 진행해도 대타 AI 덕분에 막히지 않음
    await stepPage(pageA);

    // B가 동일 토큰으로 새 컨텍스트에서 재접속 -> AI 대타 해제 및 복귀 확인
    ctxB2 = await browser.newContext();
    await ctxB2.addInitScript((saved) => {
      sessionStorage.setItem("ukeire_mp_session", JSON.stringify(saved));
    }, sessB);
    const pageB2 = await ctxB2.newPage();
    await pageB2.goto(`${baseUrl}/?room=${roomCode}`);
    await pageB2.getByText(`방 ${roomCode} · 1경기 (AI 도움 없음)`).waitFor({ timeout: 10_000 });
    await pageB2.locator(".seat-bottom").getByText("이탈자B").waitFor();
    await pageA.waitForFunction(() => document.querySelectorAll(".takeover-badge").length === 0, { timeout: 6_000 });
    return { roomCode, takeoverObserved: true, reconnectedCleanly: true };
  } finally {
    await ctxA.close();
    await ctxB.close().catch(() => {});
    if (ctxB2) await ctxB2.close();
  }
}

async function runScenarioC(browser, baseUrl) {
  async function verifyCount(count) {
    const contexts = await Promise.all(Array.from({ length: count }, () => browser.newContext()));
    try {
      const pages = await Promise.all(contexts.map((c) => c.newPage()));
      const { roomCode } = await createRoomViaUi(pages[0], baseUrl, "플레이어1");
      for (let i = 1; i < count; i++) {
        await joinRoomViaUi(pages[i], baseUrl, roomCode, `플레이어${i + 1}`);
      }
      for (const p of pages) {
        await p.getByRole("button", { name: "준비 완료" }).click();
      }
      await pages[0].getByRole("button", { name: "동풍전 시작" }).click();
      await pages[0].getByText("1경기 (AI 도움 없음)").waitFor({ timeout: 20_000 });
      const aiBadges = await pages[0].locator(".badge.ai-badge").count();
      assert.equal(aiBadges, 4 - count, `expected ${4 - count} AI badges for ${count} humans, got ${aiBadges}`);
      return { roomCode, humans: count, ai: aiBadges };
    } finally {
      await Promise.all(contexts.map((c) => c.close()));
    }
  }
  const three = await verifyCount(3);
  const four = await verifyCount(4);
  return { three, four };
}

async function runScenarioD(browser, baseUrl) {
  const rooms = await Promise.all(
    [1, 2].map(async (idx) => {
      const ctxA = await browser.newContext();
      const ctxB = await browser.newContext();
      const pageA = await ctxA.newPage();
      const pageB = await ctxB.newPage();
      const { roomCode, session: sessA } = await createRoomViaUi(pageA, baseUrl, `동시방${idx}A`);
      const sessB = await joinRoomViaUi(pageB, baseUrl, roomCode, `동시방${idx}B`);
      await pageA.getByRole("button", { name: "준비 완료" }).click();
      await pageB.getByRole("button", { name: "준비 완료" }).click();
      await pageA.getByRole("button", { name: "동풍전 시작" }).click();
      await pageA.getByText("1경기 (AI 도움 없음)").waitFor({ timeout: 20_000 });
      return { idx, roomCode, sessA, sessB, ctxA, ctxB, pageA, pageB };
    })
  );

  const actionTimes = [];
  const hintTimes = [];
  try {
    // 두 방 동시에 1경기 완주 → 2경기 전환 → 방당 최소 12회 이상 추천 + 타패 병렬 실행
    await Promise.all(
      rooms.map(async (r) => {
        await driveToHeading([r.pageA, r.pageB], "1경기 동풍전 종료", { actionTimes });
        await r.pageA.getByRole("button", { name: "같은 패산으로 2경기 시작" }).click();
        await r.pageA.getByText("2경기 (동일 패산 · 타패 추천 가능)").waitFor({ timeout: 20_000 });
        await r.pageB.getByText("2경기 (동일 패산 · 타패 추천 가능)").waitFor({ timeout: 20_000 });
        const deadline = Date.now() + 90_000;
        let roomHints = 0;
        while (Date.now() < deadline && roomHints < 12) {
          const done = await r.pageA.getByText("1경기 vs 2경기 결과 비교").isVisible().catch(() => false);
          if (done) break;
          const before = hintTimes.length;
          const actedA = await stepPage(r.pageA, { actionTimes, hintTimes, requestHints: true });
          const actedB = await stepPage(r.pageB, { actionTimes, hintTimes, requestHints: true });
          roomHints += hintTimes.length - before;
          await new Promise((rWait) => setTimeout(rWait, actedA || actedB ? 65 : 40));
        }
        assert.ok(roomHints >= 12, `room ${r.roomCode} collected only ${roomHints} concurrent hints`);
      })
    );
    return {
      rooms: rooms.map((r) => r.roomCode),
      action: summarize(actionTimes),
      hint: summarize(hintTimes),
    };
  } finally {
    await Promise.all(rooms.flatMap((r) => [r.ctxA.close(), r.ctxB.close()]));
  }
}

const port = await getFreePort();
const baseUrl = `http://127.0.0.1:${port}`;
const serverProc = spawn(
  "python",
  [
    "-m",
    "ukeire_dojo.server",
    "--db",
    "data/bank.sqlite",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--live-bot",
    "prism",
    "--live-device",
    DEVICE,
    "--disconnect-grace",
    "2.0",
  ],
  { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] }
);

let stderrBuf = "";
serverProc.stderr.on("data", (d) => {
  stderrBuf += d.toString();
});

let browser;
try {
  await waitForServer(`${baseUrl}/api/problem`);
  browser = await chromium.launch({ headless: true });

  const solo = await runSoloScenario(browser, baseUrl);
  const a = await runScenarioA(browser, baseUrl);
  const b = await runScenarioB(browser, baseUrl);
  const c = await runScenarioC(browser, baseUrl);
  const d = await runScenarioD(browser, baseUrl);

  console.log(JSON.stringify({ ok: true, device: DEVICE, solo, scenarioA: a, scenarioB: b, scenarioC: c, scenarioD: d }, null, 2));
} catch (err) {
  console.error("E2E_FAILED:", err);
  console.error("SERVER_STDERR_TAIL:", stderrBuf.slice(-3000));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  serverProc.kill("SIGTERM");
  await new Promise((r) => setTimeout(r, 500));
  if (serverProc.exitCode === null) serverProc.kill("SIGKILL");
}
