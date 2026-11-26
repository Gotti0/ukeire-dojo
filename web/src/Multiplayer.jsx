import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Melds, Tile } from "./TileView.jsx";
import { doraOf, tileName } from "./tiles.js";

const STORAGE_KEY = "ukeire_mp_session";
const WINDS = "동남서북";
const RELATIVE = ["나", "하가", "대면", "상가"];
const OUTCOME_LABELS = {
  TSUMO: "쯔모 화료",
  RON: "론 화료",
  EXHAUSTIVE: "유국",
  ABORTIVE: "도중 유국",
};
const ABORTIVE_LABELS = {
  NINE_TERMINALS: "구종구패",
  FOUR_WINDS: "사풍연타",
  FOUR_RIICHI: "사가리치",
  FOUR_KANS: "사깡산료",
  TRIPLE_RON: "삼가화",
};

function readSession() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.roomCode && parsed.token) return parsed;
  } catch {
    // 저장소 접근이 제한된 환경에서는 무시한다.
  }
  return null;
}

function saveSession(session) {
  try {
    if (!session) {
      sessionStorage.removeItem(STORAGE_KEY);
    } else {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    }
  } catch {
    // 무시한다.
  }
}

function syncRoomQuery(roomCode) {
  const url = new URL(window.location.href);
  if (roomCode) {
    url.searchParams.set("room", roomCode);
  } else {
    url.searchParams.delete("room");
  }
  window.history.replaceState(null, "", url.pathname + url.search);
}

async function apiJson(path, { method = "GET", token = null, body = null } = {}) {
  const headers = {};
  if (token) headers["X-Player-Token"] = token;
  if (body !== null) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body !== null ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    const err = new Error(data.error || response.statusText);
    err.status = response.status;
    throw err;
  }
  return data;
}

function seatLabel(seat, dealer, room) {
  const seatInfo = room.seats[seat];
  const wind = WINDS[(seat - dealer + 4) % 4];
  const who = seatInfo.kind === "ai" ? seatInfo.name || `AI ${seat + 1}` : seatInfo.name || `좌석 ${seat + 1}`;
  return { seatInfo, wind, who };
}

function lastEventText(match, room) {
  const ev = match.table.lastEvent;
  if (!ev || ev.kind === "NONE" || ev.seat < 0) return null;
  const { who, wind } = seatLabel(ev.seat, match.table.dealer, room);
  const prefix = `${who}(${wind}가)`;
  if (ev.kind === "DRAW") return ev.tile ? `${prefix} 쯔모 ${tileName(ev.tile)}` : `${prefix} 쯔모`;
  if (ev.kind === "DISCARD") return `${prefix} 타패 ${ev.tile ? tileName(ev.tile) : ""}`;
  if (ev.kind === "KAN") return `${prefix} 깡 ${ev.tile ? tileName(ev.tile) : ""}`;
  return null;
}

function LiveSeat({ room, match, seat, position }) {
  const player = match.table.players[seat];
  const { seatInfo, wind, who } = seatLabel(seat, match.table.dealer, room);
  const rel = RELATIVE[(seat - match.table.seat + 4) % 4];
  const isActive = match.phase === "DECISIONS" && match.table.activeSeat === seat;
  return (
    <div className={`seat seat-${position} ${isActive ? "seat-active" : ""}`}>
      <div className="seat-label">
        <b>{rel}</b> {wind}가 · {who} · {player.score.toLocaleString()}점
        {player.doubleRiichi && <span className="badge riichi">더블 리치</span>}
        {!player.doubleRiichi && player.riichi && <span className="badge riichi">리치</span>}
        {seatInfo.kind === "ai" && <span className="badge ai-badge">AI</span>}
        {seatInfo.aiTakeover && <span className="badge takeover-badge">AI 대타 중</span>}
        {seatInfo.kind === "human" && !seatInfo.connected && !seatInfo.aiTakeover && (
          <span className="badge offline-badge">재접속 대기</span>
        )}
      </div>
      {seat !== match.table.seat && (
        <div className="concealed-count">손패 {player.concealed}장</div>
      )}
      <div className="river">
        {player.river.map((t, i) => (
          <Tile key={i} code={t.tile} small dim={t.called} sideways={t.riichi} marks={t.tsumogiri ? ["tsumogiri"] : []} />
        ))}
      </div>
      {player.melds.length > 0 && <Melds melds={player.melds} />}
    </div>
  );
}

function actionLabel(action) {
  switch (action.type) {
    case "TSUMO_AGARI":
      return "쯔모 화료";
    case "RON_AGARI":
      return "론";
    case "CHI":
      return `치 (${action.consumed.map(tileName).join("+")} 사용)`;
    case "PON":
      return `퐁 ${tileName(action.tile)}${action.red ? " (적5)" : ""}`;
    case "DAIMINKAN":
      return `대명깡 ${tileName(action.tile)}`;
    case "ANKAN":
      return `암깡 ${tileName(action.tile)}`;
    case "KAKAN":
      return `가깡 ${tileName(action.tile)}`;
    case "KYUSHU_KYUHAI":
      return "구종구패";
    case "PASS":
      return "패스";
    default:
      return action.type;
  }
}

export default function Multiplayer() {
  const initialRoom = useMemo(() => new URLSearchParams(window.location.search).get("room") || "", []);
  const [session, setSession] = useState(() => readSession());
  const [room, setRoom] = useState(null);
  const [name, setName] = useState(() => readSession()?.name || "");
  const [joinCode, setJoinCode] = useState(() => initialRoom.toUpperCase());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [streamStatus, setStreamStatus] = useState("");
  const [riichiMode, setRiichiMode] = useState(false);
  const [hintState, setHintState] = useState(null);
  const [hintBusy, setHintBusy] = useState(false);
  const [hintError, setHintError] = useState("");
  const retryDelayRef = useRef(1000);
  const hintControllerRef = useRef(null);
  const currentRevRef = useRef(null);

  const resetSession = useCallback((message = "") => {
    hintControllerRef.current?.abort();
    hintControllerRef.current = null;
    saveSession(null);
    setSession(null);
    setRoom(null);
    setStreamStatus("");
    setHintState(null);
    setHintBusy(false);
    setHintError("");
    syncRoomQuery(null);
    if (message) setError(message);
  }, []);

  const matchNumber = room?.match?.matchNumber ?? null;
  const matchPhase = room?.match?.phase ?? null;
  const decisionRevision = room?.match?.decision?.revision ?? null;
  currentRevRef.current = decisionRevision;

  useEffect(() => {
    hintControllerRef.current?.abort();
    hintControllerRef.current = null;
    setRiichiMode(false);
    setHintState(null);
    setHintBusy(false);
    setHintError("");
  }, [matchNumber, matchPhase, decisionRevision]);

  useEffect(() => () => hintControllerRef.current?.abort(), []);

  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    let active = true;
    let retryTimer = null;

    const connect = async () => {
      try {
        const response = await fetch(`/api/rooms/${encodeURIComponent(session.roomCode)}/events`, {
          headers: { "X-Player-Token": session.token },
          signal: controller.signal,
        });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          if ([401, 403, 404].includes(response.status)) {
            if (active) resetSession(body.error || "방 연결이 만료되었습니다.");
            return;
          }
          throw new Error(body.error || response.statusText);
        }
        retryDelayRef.current = 1000;
        if (active) setStreamStatus("");
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffered = "";
        try {
          while (active) {
            const { value, done } = await reader.read();
            if (done) throw new Error("실시간 연결이 끊겼습니다.");
            buffered += value;
            const lines = buffered.split("\n");
            buffered = lines.pop();
            for (const line of lines) {
              if (!line) continue;
              const event = JSON.parse(line);
              if (event.type === "state" && active) {
                setRoom(event.state);
                setError("");
              }
            }
          }
        } finally {
          reader.releaseLock();
        }
      } catch (err) {
        if (!active || controller.signal.aborted) return;
        setStreamStatus("실시간 연결 복구 중…");
        const delay = retryDelayRef.current;
        retryDelayRef.current = Math.min(3000, delay + 1000);
        retryTimer = window.setTimeout(connect, delay);
      }
    };

    connect();
    return () => {
      active = false;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      controller.abort();
    };
  }, [session, resetSession]);

  const createRoom = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = await apiJson("/api/rooms", { method: "POST", body: { name } });
      const nextSession = { roomCode: data.roomCode, token: data.token, name: name.trim() };
      saveSession(nextSession);
      syncRoomQuery(data.roomCode);
      setSession(nextSession);
      setRoom(data.state);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const joinRoom = async (e) => {
    e.preventDefault();
    const code = joinCode.trim().toUpperCase();
    if (!code) {
      setError("초대 코드를 입력하세요.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = await apiJson(`/api/rooms/${encodeURIComponent(code)}/join`, {
        method: "POST",
        token: session?.roomCode === code ? session.token : null,
        body: { name },
      });
      const nextSession = { roomCode: data.roomCode, token: data.token, name: name.trim() };
      saveSession(nextSession);
      syncRoomQuery(data.roomCode);
      setSession(nextSession);
      setRoom(data.state);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const withRoomPost = async (suffix, body = {}) => {
    if (!session) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const nextState = await apiJson(`/api/rooms/${encodeURIComponent(session.roomCode)}/${suffix}`, {
        method: "POST",
        token: session.token,
        body,
      });
      if (suffix === "leave") {
        resetSession();
      } else {
        setRoom(nextState);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const copyInvite = async () => {
    if (!room) return;
    const url = `${window.location.origin}/?room=${room.roomCode}`;
    try {
      await navigator.clipboard.writeText(url);
      setNotice(`초대 링크를 복사했습니다 (${room.roomCode}).`);
    } catch {
      setNotice(`초대 코드: ${room.roomCode} (${url})`);
    }
  };

  const requestHint = async () => {
    const decision = room?.match?.decision;
    if (!session || !decision || hintBusy) return;
    const requestedRev = decision.revision;
    hintControllerRef.current?.abort();
    const controller = new AbortController();
    hintControllerRef.current = controller;
    setHintBusy(true);
    setHintError("");
    try {
      const response = await fetch(`/api/rooms/${encodeURIComponent(session.roomCode)}/hint`, {
        method: "POST",
        headers: {
          "X-Player-Token": session.token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ revision: requestedRev }),
        signal: controller.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || response.statusText);
      }
      if (!controller.signal.aborted && currentRevRef.current === requestedRev && data.revision === requestedRev) {
        setHintState(data);
      }
    } catch (err) {
      if (!controller.signal.aborted && currentRevRef.current === requestedRev) {
        setHintError(err.message || "타패 추천을 불러오지 못했습니다.");
      }
    } finally {
      if (hintControllerRef.current === controller) {
        hintControllerRef.current = null;
        setHintBusy(false);
      }
    }
  };

  if (!session || !room) {
    return (
      <section className="mp-panel">
        <h2>동풍전 멀티플레이</h2>
        <p className="muted">
          2~4명의 사람이 같은 방에 모여 동풍전을 진행합니다. 빈 좌석은 Epsilon-Prism AI가 맡습니다.
        </p>
        {error && <div className="error">{error}</div>}
        {session && !room && !error && <p className="muted">방 상태를 불러오는 중…</p>}
        <form className="mp-entry" onSubmit={joinRoom}>
          <label>
            닉네임
            <input
              type="text"
              maxLength={20}
              placeholder="표시할 이름"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={busy}
            />
          </label>
          <div className="mp-entry-actions">
            <button type="button" className="btn-primary" disabled={busy || !name.trim()} onClick={createRoom}>
              새 방 만들기
            </button>
            <span className="mp-divider">또는</span>
            <input
              type="text"
              maxLength={8}
              className="code-input"
              placeholder="초대 코드 (6자리)"
              value={joinCode}
              onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
              disabled={busy}
            />
            <button type="submit" disabled={busy || !name.trim() || !joinCode.trim()}>
              방 입장
            </button>
          </div>
        </form>
      </section>
    );
  }

  if (room.status === "LOBBY") {
    const me = room.seats[room.mySeat];
    return (
      <section className="mp-panel">
        <div className="mp-lobby-head">
          <div>
            <h2>초대 방 · {room.roomCode}</h2>
            <p className="muted small-text">
              비어 있는 좌석은 대국 시작 시 AI로 채워집니다. 이탈 시 {room.disconnectGraceSeconds}초 뒤 AI가 임시 대타를 맡습니다.
            </p>
          </div>
          <div className="mp-head-buttons">
            <button type="button" onClick={copyInvite}>초대 링크 복사</button>
            <button type="button" onClick={() => withRoomPost("leave")} disabled={busy}>방 나가기</button>
          </div>
        </div>
        {streamStatus && <div className="muted small-text">{streamStatus}</div>}
        {notice && <div className="notice">{notice}</div>}
        {error && <div className="error">{error}</div>}
        <div className="seat-grid">
          {room.seats.map((slot) => (
            <div key={slot.seat} className={`seat-card ${slot.seat === room.mySeat ? "mine" : ""}`}>
              <div className="seat-card-title">
                <strong>{WINDS[slot.seat]}가 (좌석 {slot.seat + 1})</strong>
                {slot.isHost && <span className="badge host-badge">방장</span>}
                {slot.seat === room.mySeat && <span className="badge me-badge">나</span>}
              </div>
              {slot.kind === "empty" ? (
                <div className="seat-card-body">
                  <span className="muted">빈 좌석 (시작 시 AI 배정)</span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => withRoomPost("seat", { seat: slot.seat })}
                  >
                    이 자리로 이동
                  </button>
                </div>
              ) : (
                <div className="seat-card-body">
                  <span>{slot.name}</span>
                  <span className={`badge ${slot.ready ? "ready-badge" : "wait-badge"}`}>
                    {slot.ready ? "준비 완료" : "대기 중"}
                  </span>
                  {!slot.connected && <span className="badge offline-badge">연결 끊김</span>}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="mp-lobby-actions">
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => withRoomPost("ready", { ready: !me.ready })}
          >
            {me.ready ? "준비 취소" : "준비 완료"}
          </button>
          {room.isHost && (
            <button
              type="button"
              className="btn-start"
              disabled={busy || !room.canStart}
              onClick={() => withRoomPost("start")}
            >
              동풍전 시작
            </button>
          )}
          {room.isHost && !room.canStart && room.startBlockReason && (
            <span className="muted">{room.startBlockReason}</span>
          )}
        </div>
      </section>
    );
  }

  const match = room.match;
  const seatAt = (offset) => (match.table.seat + offset) % 4;
  const decision = match.decision;
  const actions = decision?.actions || [];
  const dahaiActions = actions.filter((a) => a.type === "DAHAI");
  const riichiActions = actions.filter((a) => a.type === "RIICHI_DAHAI");
  const specialActions = actions.filter((a) => a.type !== "DAHAI" && a.type !== "RIICHI_DAHAI");
  const activeDiscards = riichiMode ? riichiActions : dahaiActions;

  const findDiscardAction = (code, isDraw) => {
    const exact = activeDiscards.find((a) => a.tile === code && a.tsumogiri === isDraw);
    if (exact) return exact;
    return activeDiscards.find((a) => a.tile === code) || null;
  };

  const eventSummary = lastEventText(match, room);
  const settlement = match.settlement;
  const activeHint = decision && hintState && hintState.revision === decision.revision ? hintState : null;
  const firstResult = room.firstMatchResult;
  const secondResult = room.secondMatchResult;

  return (
    <section className="mp-panel">
      <div className="mp-match-bar">
        <strong>
          방 {room.roomCode} · {match.matchNumber}경기
          {match.matchNumber === 1 ? " (AI 도움 없음)" : " (동일 패산 · 타패 추천 가능)"} ·{" "}
          동{match.round.kyoku}국 {match.round.honba}본장
        </strong>
        <span className="muted">
          내 자풍: {WINDS[(match.table.seat - match.table.dealer + 4) % 4]}가
          {match.table.furiten ? " · 후리텐" : ""}
        </span>
        {streamStatus && <span className="muted small-text">{streamStatus}</span>}
      </div>
      {error && <div className="error">{error}</div>}
      <div className="table">
        <LiveSeat room={room} match={match} seat={seatAt(2)} position="top" />
        <LiveSeat room={room} match={match} seat={seatAt(3)} position="left" />
        <div className="center">
          <div className="round">
            동{match.round.kyoku}국 {match.round.honba}본장 · 공탁 {match.table.kyotaku}
          </div>
          <div>남은 패 {match.table.remaining}</div>
          <div className="dora">
            도라 표시패 {match.table.dora.map((d, i) => <Tile key={i} code={d} small />)}
            <span className="muted">({match.table.dora.map((d) => tileName(doraOf(d))).join(", ")})</span>
          </div>
          {eventSummary && <div className="small-text muted">{eventSummary}</div>}
        </div>
        <LiveSeat room={room} match={match} seat={seatAt(1)} position="right" />
        <LiveSeat room={room} match={match} seat={match.table.seat} position="bottom" />
      </div>

      {match.phase === "DECISIONS" && (
        <div className="prompt">
          {decision
            ? (riichiMode ? "리치 선언패를 고르세요" : "행동을 선택하세요")
            : (match.decisionSubmitted ? "선택 완료 · 다른 응답을 기다리는 중…" : "다른 참가자·AI의 차례를 기다리는 중…")}
        </div>
      )}

      <div className="hand">
        {match.table.hand.map((code, i) => {
          const action = findDiscardAction(code, false);
          return (
            <Tile
              key={`${code}-${i}`}
              code={code}
              marks={[
                riichiMode && action && "riichi-cand",
                !riichiMode && activeHint && code === activeHint.bestTile && "ai-best",
              ].filter(Boolean)}
              disabled={busy || !decision || !action}
              onClick={() => action && withRoomPost("action", { revision: decision.revision, action: action.code })}
            />
          );
        })}
        {match.table.draw && <span className="draw-gap" />}
        {match.table.draw && (() => {
          const drawAction = findDiscardAction(match.table.draw, true);
          return (
            <Tile
              key="draw"
              code={match.table.draw}
              marks={[
                riichiMode && drawAction && "riichi-cand",
                !riichiMode && activeHint && match.table.draw === activeHint.bestTile && "ai-best",
              ].filter(Boolean)}
              disabled={busy || !decision || !drawAction}
              onClick={() => drawAction && withRoomPost("action", { revision: decision.revision, action: drawAction.code })}
            />
          );
        })()}
      </div>

      {match.table.players[match.table.seat].melds.length > 0 && (
        <div className="my-melds">
          울기 <Melds melds={match.table.players[match.table.seat].melds} />
        </div>
      )}

      {decision && (match.hintAllowed || riichiActions.length > 0 || specialActions.length > 0) && (
        <div className="action-bar">
          {match.hintAllowed && (
            <button
              type="button"
              className="btn-hint"
              disabled={busy || hintBusy}
              onClick={requestHint}
            >
              {hintBusy ? "타패 추천 계산 중…" : "AI 타패 추천 보기"}
            </button>
          )}
          {riichiActions.length > 0 && (
            <button
              type="button"
              className={riichiMode ? "btn-riichi active" : "btn-riichi"}
              disabled={busy}
              onClick={() => setRiichiMode((v) => !v)}
            >
              {riichiMode ? "리치 선언 취소" : "리치 선언"}
            </button>
          )}
          {specialActions.map((a) => (
            <button
              key={a.code}
              type="button"
              className={a.type === "TSUMO_AGARI" || a.type === "RON_AGARI" ? "btn-win" : "btn-action"}
              disabled={busy}
              onClick={() => withRoomPost("action", { revision: decision.revision, action: a.code })}
            >
              {actionLabel(a)}
            </button>
          ))}
        </div>
      )}

      {hintError && <div className="error">{hintError}</div>}

      {activeHint && (
        <div className="hint-panel">
          <div className="verdict">
            AI 추천 타패: <Tile code={activeHint.bestTile} small /> {tileName(activeHint.bestTile)}
          </div>
          <p className="muted small-text">
            최종 타패는 손패에서 직접 선택합니다. 손실이 0.000에 가까울수록 AI 최선과 같습니다.
          </p>
          <table>
            <thead>
              <tr><th>버릴 패</th><th>AI 손실</th></tr>
            </thead>
            <tbody>
              {activeHint.candidates.map((row) => (
                <tr key={row.tile} className={row.tile === activeHint.bestTile ? "ai-row" : ""}>
                  <td><Tile code={row.tile} small /> {tileName(row.tile)}</td>
                  <td>{row.loss.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {match.phase === "ROUND_END" && settlement && (
        <div className="result">
          <div className="verdict">
            동{settlement.round.kyoku}국 {settlement.round.honba}본장 정산 ·{" "}
            {OUTCOME_LABELS[settlement.outcome] || settlement.outcome}
            {settlement.abortive ? ` (${ABORTIVE_LABELS[settlement.abortive] || settlement.abortive})` : ""}
          </div>
          {settlement.winners.length > 0 && (
            <p className="muted">
              화료: {settlement.winners.map((w) => seatLabel(w, match.table.dealer, room).who).join(", ")}
              {settlement.loser >= 0 ? ` · 방총: ${seatLabel(settlement.loser, match.table.dealer, room).who}` : ""}
            </p>
          )}
          <table>
            <thead>
              <tr><th>좌석</th><th>참가자</th><th>변동</th><th>점수</th><th>순위</th></tr>
            </thead>
            <tbody>
              {room.seats.map((slot) => (
                <tr key={slot.seat} className={slot.seat === room.mySeat ? "ai-row" : ""}>
                  <td>{WINDS[slot.seat]}가</td>
                  <td>{slot.name || `좌석 ${slot.seat + 1}`}</td>
                  <td>{settlement.changes[slot.seat] >= 0 ? `+${settlement.changes[slot.seat].toLocaleString()}` : settlement.changes[slot.seat].toLocaleString()}</td>
                  <td>{settlement.scores[slot.seat].toLocaleString()}</td>
                  <td>{settlement.ranks[slot.seat]}위</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mp-lobby-actions">
            <button
              type="button"
              className="next"
              disabled={busy || match.roundEndConfirmed}
              onClick={() => withRoomPost("next-round", { kyoku: settlement.round.kyoku, honba: settlement.round.honba })}
            >
              {match.roundEndConfirmed ? "다른 참가자 확인 대기 중" : "다음 국 진행"}
            </button>
            <span className="muted">
              확인 {match.roundEndConfirmedCount} / {match.roundEndRequiredCount}명
            </span>
          </div>
        </div>
      )}

      {room.status === "MATCH_END" && settlement && (
        <div className="result">
          <div className="verdict good">1경기 동풍전 종료</div>
          <table>
            <thead>
              <tr><th>순위</th><th>좌석</th><th>참가자</th><th>최종 점수</th><th>득실</th></tr>
            </thead>
            <tbody>
              {[...room.seats]
                .sort((a, b) => settlement.ranks[a.seat] - settlement.ranks[b.seat])
                .map((slot) => {
                  const sc = settlement.scores[slot.seat];
                  const diff = sc - 25000;
                  return (
                    <tr key={slot.seat} className={slot.seat === room.mySeat ? "ai-row" : ""}>
                      <td>{settlement.ranks[slot.seat]}위</td>
                      <td>{WINDS[slot.seat]}가</td>
                      <td>{slot.name || `좌석 ${slot.seat + 1}`}</td>
                      <td>{sc.toLocaleString()}</td>
                      <td>{diff >= 0 ? `+${diff.toLocaleString()}` : diff.toLocaleString()}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
          <p className="muted small-text">
            같은 좌석·25,000점·동일 국·본장 패산으로 2경기를 시작하며, 2경기에서는 자기 차례에 AI 타패 추천을 볼 수 있습니다.
          </p>
          <div className="mp-lobby-actions">
            {room.canStartReplay ? (
              <button type="button" className="btn-start" disabled={busy} onClick={() => withRoomPost("replay")}>
                같은 패산으로 2경기 시작
              </button>
            ) : (
              <span className="muted">방장이 같은 패산으로 2경기를 시작할 때까지 기다리는 중…</span>
            )}
            <button type="button" onClick={() => resetSession()}>
              방 나가기
            </button>
          </div>
        </div>
      )}

      {room.status === "SERIES_END" && firstResult && secondResult && (
        <div className="result">
          <div className="verdict good">1경기 vs 2경기 결과 비교</div>
          <p className="muted small-text">
            동일한 국·본장 패산에서 AI 도움 없이 둔 1경기와 요청형 타패 추천을 참고해 둔 2경기의 결과입니다.
          </p>
          <table>
            <thead>
              <tr>
                <th>좌석</th>
                <th>참가자</th>
                <th>1경기 점수 (순위)</th>
                <th>2경기 점수 (순위)</th>
                <th>점수 변화</th>
                <th>순위 변화</th>
              </tr>
            </thead>
            <tbody>
              {room.seats.map((slot) => {
                const s1 = firstResult.scores[slot.seat];
                const r1 = firstResult.ranks[slot.seat];
                const s2 = secondResult.scores[slot.seat];
                const r2 = secondResult.ranks[slot.seat];
                const scoreDiff = s2 - s1;
                const rankDiff = r1 - r2;
                return (
                  <tr key={slot.seat} className={slot.seat === room.mySeat ? "ai-row" : ""}>
                    <td>{WINDS[slot.seat]}가</td>
                    <td>{slot.name || `좌석 ${slot.seat + 1}`}</td>
                    <td>{s1.toLocaleString()} ({r1}위)</td>
                    <td>{s2.toLocaleString()} ({r2}위)</td>
                    <td className={scoreDiff > 0 ? "delta-up" : scoreDiff < 0 ? "delta-down" : ""}>
                      {scoreDiff > 0 ? `+${scoreDiff.toLocaleString()}` : scoreDiff.toLocaleString()}
                    </td>
                    <td className={rankDiff > 0 ? "delta-up" : rankDiff < 0 ? "delta-down" : ""}>
                      {rankDiff > 0 ? `▲${rankDiff}` : rankDiff < 0 ? `▼${-rankDiff}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <button type="button" className="next" onClick={() => resetSession()}>
            방 목록으로 돌아가기
          </button>
        </div>
      )}
    </section>
  );
}
