package ukeire_dojo.live;

import com.epsilon.agent.observe.Observation;
import com.epsilon.agent.observe.ObservationBatch;
import com.epsilon.agent.observe.ObservationWriter;
import com.epsilon.agent.runtime.Kernels;
import com.epsilon.agent.runtime.NetworkDevices;
import com.epsilon.agent.runtime.NetworkRuntime;
import com.epsilon.agent.runtime.Precision;
import com.epsilon.mahjong.action.Action;
import com.epsilon.mahjong.engine.Decisions;
import com.epsilon.mahjong.engine.EventKind;
import com.epsilon.mahjong.engine.EventLog;
import com.epsilon.mahjong.engine.SeatView;
import com.epsilon.mahjong.engine.TurnEvent;
import com.epsilon.mahjong.rules.Variant;
import com.epsilon.mahjong.score.RiichiState;
import com.epsilon.mahjong.tile.Tiles;
import com.epsilon.prism.policy.PrismPolicy;
import com.epsilon.prism.run.Snapshot;
import com.epsilon.prism.settings.BehaviourSettings;
import com.epsilon.reviewer.engine.view.MeldLayout;
import com.epsilon.reviewer.engine.view.MeldView;
import com.epsilon.reviewer.engine.view.TileCode;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.PrintStream;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 상주 JSON-lines 호스트. 방별 {@link EastSeries}를 단일 스레드에서 관리하고, 좌석별 관측(SeatView)과 AI 판단을 제공한다.
 */
public final class LiveMatchHost {
  private static final Variant RULES = Variant.TENHOU_4P;
  private static final Gson GSON = new GsonBuilder().serializeNulls().create();

  private interface BotPolicy extends AutoCloseable {
    int choose(SeatView view, Decisions decisions, int slot);

    float[] evaluateValues(SeatView view, Decisions decisions, int slot);

    @Override
    default void close() {}
  }

  private static final class PrismBot implements BotPolicy {
    private final NetworkRuntime runtime;
    private final PrismPolicy policy;
    private final ObservationWriter writer = new ObservationWriter(RULES);
    private final ObservationBatch batch = new ObservationBatch(RULES, 64);
    private final Observation observation = new Observation(RULES);

    PrismBot(Path checkpoint, String deviceName) throws Exception {
      var device = NetworkDevices.device(deviceName);
      boolean gpu = device.isGpu();
      this.runtime = new NetworkRuntime(1, 1, 1);
      try {
        this.policy = new PrismPolicy(
            Snapshot.open(
                checkpoint,
                List.of(device),
                runtime,
                1,
                gpu ? Precision.BFLOAT16 : Precision.FLOAT32,
                gpu ? Kernels.NATIVE : Kernels.EAGER),
            RULES,
            "live-match",
            null,
            BehaviourSettings.defaults().twinDiscard());
      } catch (Exception | Error err) {
        runtime.close();
        throw err;
      }
    }

    @Override
    public int choose(SeatView view, Decisions decisions, int slot) {
      batch.clear();
      int row = writer.capture(view, decisions, slot, batch);
      int choice = policy.evaluate(observation.bind(batch, row)).greedy();
      return decisions.legal(slot, choice);
    }

    @Override
    public float[] evaluateValues(SeatView view, Decisions decisions, int slot) {
      batch.clear();
      int row = writer.capture(view, decisions, slot, batch);
      float[] values = policy.evaluate(observation.bind(batch, row)).values();
      if (values == null || values.length != decisions.legalCount(slot)) {
        throw new IllegalStateException("unexpected policy value array length");
      }
      return values;
    }

    @Override
    public void close() {
      try {
        policy.close();
      } finally {
        runtime.close();
      }
    }
  }

  private static final class ScriptedBot implements BotPolicy {
    @Override
    public int choose(SeatView view, Decisions decisions, int slot) {
      int count = decisions.legalCount(slot);
      Integer discard = null;
      Integer pass = null;
      for (int i = 0; i < count; i++) {
        int code = decisions.legal(slot, i);
        switch (Action.of(code).type()) {
          case RON_AGARI, TSUMO_AGARI -> { return code; }
          case DAHAI -> { if (discard == null) discard = code; }
          case PASS -> pass = code;
          default -> {}
        }
      }
      if (discard != null) return discard;
      return pass != null ? pass : decisions.legal(slot, 0);
    }

    @Override
    public float[] evaluateValues(SeatView view, Decisions decisions, int slot) {
      int count = decisions.legalCount(slot);
      float[] values = new float[count];
      for (int i = 0; i < count; i++) {
        values[i] = -0.01f * i;
      }
      return values;
    }
  }

  private final Map<String, EastSeries> seriesById = new HashMap<>();
  private final BotPolicy bot;

  LiveMatchHost(BotPolicy bot) {
    this.bot = bot;
  }

  static LiveMatchHost scripted() {
    return new LiveMatchHost(new ScriptedBot());
  }

  public static void main(String[] args) throws Exception {
    // 라이브러리 로그가 stdout JSON 프로토콜을 오염시키지 않게 stderr로 돌린다.
    PrintStream protocolOut = System.out;
    System.setOut(System.err);
    if (args.length < 1) {
      throw new IllegalArgumentException("usage: LiveMatchHost scripted | prism <checkpoint> <device>");
    }
    String mode = args[0];
    BotPolicy bot;
    Map<String, Object> ready = new LinkedHashMap<>();
    ready.put("event", "ready");
    ready.put("bot", mode);
    if ("scripted".equals(mode)) {
      bot = new ScriptedBot();
    } else if ("prism".equals(mode) && args.length == 3) {
      bot = new PrismBot(Path.of(args[1]), args[2]);
      ready.put("device", args[2]);
    } else {
      throw new IllegalArgumentException("usage: LiveMatchHost scripted | prism <checkpoint> <device>");
    }

    var host = new LiveMatchHost(bot);
    var in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
    var out = new PrintWriter(new OutputStreamWriter(protocolOut, StandardCharsets.UTF_8), true);
    try (bot) {
      out.println(GSON.toJson(ready));
      String line;
      while ((line = in.readLine()) != null) {
        if (line.isBlank()) continue;
        JsonElement id = null;
        Map<String, Object> reply = new LinkedHashMap<>();
        try {
          JsonObject req = JsonParser.parseString(line).getAsJsonObject();
          id = req.get("id");
          reply.put("id", id);
          Object result = host.handle(req);
          reply.put("ok", true);
          reply.put("result", result);
        } catch (Exception e) {
          reply.put("id", id);
          reply.put("ok", false);
          reply.put("error", e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
        }
        out.println(GSON.toJson(reply));
      }
    }
  }

  Object handle(JsonObject req) {
    String cmd = req.get("cmd").getAsString();
    String roomId = req.get("roomId").getAsString();
    return switch (cmd) {
      case "create_series" -> {
        if (seriesById.containsKey(roomId)) {
          throw new IllegalStateException("series already exists: " + roomId);
        }
        seriesById.put(roomId, new EastSeries());
        yield pendingInfo(matchOf(roomId, 1), 1);
      }
      case "start_replay" -> pendingInfo(matchOf(roomId, 2), 2);
      case "delete_series" -> {
        seriesById.remove(roomId);
        yield Map.of("deleted", true);
      }
      case "pending" -> {
        int matchIndex = optInt(req, "matchIndex", 1);
        yield pendingInfo(matchOf(roomId, matchIndex), matchIndex);
      }
      case "seat_state" -> {
        int matchIndex = optInt(req, "matchIndex", 1);
        int seat = req.get("seat").getAsInt();
        yield seatState(matchOf(roomId, matchIndex), matchIndex, seat);
      }
      case "ai_choice" -> {
        int matchIndex = optInt(req, "matchIndex", 1);
        int seat = req.get("seat").getAsInt();
        yield aiChoice(matchOf(roomId, matchIndex), seat);
      }
      case "discard_hint" -> {
        int matchIndex = optInt(req, "matchIndex", 1);
        int seat = req.get("seat").getAsInt();
        yield discardHint(roomId, seat, matchIndex);
      }
      case "commit" -> {
        int matchIndex = optInt(req, "matchIndex", 1);
        long revision = req.get("revision").getAsLong();
        JsonObject actionsObj = req.getAsJsonObject("actions");
        Map<Integer, Integer> actions = new LinkedHashMap<>();
        for (var entry : actionsObj.entrySet()) {
          actions.put(Integer.parseInt(entry.getKey()), entry.getValue().getAsInt());
        }
        EastMatch match = matchOf(roomId, matchIndex);
        match.commit(revision, actions);
        yield pendingInfo(match, matchIndex);
      }
      case "next_round" -> {
        int matchIndex = optInt(req, "matchIndex", 1);
        int kyoku = req.get("kyoku").getAsInt();
        int honba = req.get("honba").getAsInt();
        EastMatch match = matchOf(roomId, matchIndex);
        match.nextRound(new EastMatch.Round(kyoku, honba));
        yield pendingInfo(match, matchIndex);
      }
      default -> throw new IllegalArgumentException("unknown command: " + cmd);
    };
  }

  private EastMatch matchOf(String roomId, int matchIndex) {
    EastSeries series = seriesById.get(roomId);
    if (series == null) throw new IllegalArgumentException("unknown room series: " + roomId);
    return switch (matchIndex) {
      case 1 -> series.first();
      case 2 -> series.startReplay();
      default -> throw new IllegalArgumentException("invalid matchIndex: " + matchIndex);
    };
  }

  private static int optInt(JsonObject req, String key, int fallback) {
    return req.has(key) && !req.get(key).isJsonNull() ? req.get(key).getAsInt() : fallback;
  }

  private Map<String, Object> aiChoice(EastMatch match, int seat) {
    if (match.phase() != EastMatch.Phase.DECISIONS) {
      throw new IllegalStateException("the match is not waiting for decisions");
    }
    Decisions decisions = match.decisions();
    int slot = decisions.slotOf(seat);
    if (slot == -1) {
      throw new IllegalStateException("seat " + seat + " has no pending decision");
    }
    int action = decisions.legalCount(slot) == 1
        ? decisions.legal(slot, 0)
        : bot.choose(match.seatView(seat), decisions, slot);
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("revision", match.revision());
    out.put("action", action);
    return out;
  }

  private Map<String, Object> discardHint(String roomId, int seat, int matchIndex) {
    if (matchIndex != 2) {
      throw new IllegalArgumentException("discard hints are only available in the second match");
    }
    EastMatch match = matchOf(roomId, matchIndex);
    if (match.phase() != EastMatch.Phase.DECISIONS) {
      throw new IllegalStateException("the match is not waiting for decisions");
    }
    Decisions decisions = match.decisions();
    int slot = decisions.slotOf(seat);
    if (slot == -1) {
      throw new IllegalStateException("seat " + seat + " has no pending decision");
    }
    List<Integer> discardIndices = new ArrayList<>();
    for (int i = 0; i < decisions.legalCount(slot); i++) {
      if (Action.of(decisions.legal(slot, i)).type() == Action.Type.DAHAI) {
        discardIndices.add(i);
      }
    }
    if (discardIndices.isEmpty()) {
      throw new IllegalStateException("no legal discard actions for seat " + seat);
    }
    float[] values = decisions.legalCount(slot) == 1
        ? new float[] {0.0f}
        : bot.evaluateValues(match.seatView(seat), decisions, slot);
    float best = Float.NEGATIVE_INFINITY;
    for (int i : discardIndices) {
      float v = values[i];
      if (!Float.isFinite(v)) {
        throw new IllegalStateException("non-finite policy value for discard candidate");
      }
      if (v > best) best = v;
    }

    record CandidateScore(String tile, int code, boolean tsumogiri, float value) {}
    Map<String, CandidateScore> merged = new LinkedHashMap<>();
    for (int i : discardIndices) {
      int code = decisions.legal(slot, i);
      Action a = Action.of(code);
      String tile = TileCode.of(a.tile(), a.selection().red());
      float v = values[i];
      CandidateScore prev = merged.get(tile);
      if (prev == null || v > prev.value()) {
        merged.put(tile, new CandidateScore(tile, code, a.selection().tsumogiri(), v));
      }
    }

    List<Map<String, Object>> candidates = new ArrayList<>(merged.size());
    for (CandidateScore c : merged.values()) {
      double loss = Math.max(0.0, (double) best - (double) c.value());
      if (!Double.isFinite(loss)) {
        throw new IllegalStateException("non-finite discard loss");
      }
      Map<String, Object> item = new LinkedHashMap<>();
      item.put("tile", c.tile());
      item.put("code", c.code());
      item.put("tsumogiri", c.tsumogiri());
      item.put("value", (double) c.value());
      item.put("loss", loss);
      candidates.add(item);
    }
    candidates.sort(
        Comparator.<Map<String, Object>>comparingDouble(m -> (Double) m.get("loss"))
            .thenComparingInt(m -> TileCode.order((String) m.get("tile"))));

    Map<String, Object> out = new LinkedHashMap<>();
    out.put("matchNumber", 2);
    out.put("revision", match.revision());
    out.put("bestTile", candidates.getFirst().get("tile"));
    out.put("candidates", candidates);
    return out;
  }

  static Map<String, Object> pendingInfo(EastMatch match, int matchIndex) {
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("matchNumber", matchIndex);
    out.put("phase", match.phase().name());
    out.put("revision", match.revision());
    out.put("round", roundMap(match.round()));
    List<Integer> pendingSeats = new ArrayList<>();
    if (match.phase() == EastMatch.Phase.DECISIONS) {
      Decisions decisions = match.decisions();
      for (int slot = 0; slot < decisions.size(); slot++) {
        pendingSeats.add(decisions.seat(slot));
      }
    }
    out.put("pendingSeats", pendingSeats);
    out.put("settlement", match.settlement() == null ? null : settlementMap(match.settlement()));
    return out;
  }

  static Map<String, Object> seatState(EastMatch match, int matchIndex, int seat) {
    // 반드시 지정 좌석의 SeatView와 해당 좌석의 Decision만 읽는다.
    SeatView view = match.seatView(seat);
    EastMatch.Phase phase = match.phase();
    EastMatch.Round round = match.round();

    TurnEvent last = view.last();
    String draw = null;
    if (phase == EastMatch.Phase.DECISIONS
        && last.kind() == TurnEvent.Kind.DRAW
        && last.seat() == seat
        && last.tile() != TurnEvent.NONE) {
      draw = TileCode.of(last.tile(), last.red());
    }

    List<String> hand = new ArrayList<>(14);
    var tileSet = RULES.tiles();
    for (int index = 0; index < tileSet.size(); index++) {
      int pid = tileSet.physicalId(index);
      if (view.holds(pid)) {
        hand.add(TileCode.of(tileSet, pid));
      }
    }
    hand.sort(Comparator.comparingInt(TileCode::order));
    if (draw != null) {
      boolean removed = hand.remove(draw);
      if (!removed) {
        throw new IllegalStateException("drawn tile missing from SeatView hand: " + draw);
      }
    }

    List<String> dora = new ArrayList<>(view.indicatorCount());
    for (int i = 0; i < view.indicatorCount(); i++) {
      dora.add(TileCode.of(tileSet, view.indicator(i)));
    }

    List<Map<String, Object>> players = new ArrayList<>(RULES.seats());
    EventLog events = view.events();
    boolean activeMayHoldExtra = phase == EastMatch.Phase.DECISIONS
        && (last.kind() == TurnEvent.Kind.DRAW || last.kind() == TurnEvent.Kind.NONE);
    for (int s = 0; s < RULES.seats(); s++) {
      Map<String, Object> player = new LinkedHashMap<>();
      player.put("seat", s);
      player.put("score", view.score(s));
      RiichiState riichi = view.riichi(s);
      player.put("riichi", riichi != RiichiState.NONE);
      player.put("doubleRiichi", riichi == RiichiState.DOUBLE_RIICHI);

      List<Map<String, Object>> river = new ArrayList<>(events.riverSize(s));
      for (int i = 0; i < events.riverSize(s); i++) {
        int ev = events.river(s, i);
        Map<String, Object> rt = new LinkedHashMap<>();
        rt.put("tile", TileCode.of(events.tile(ev), events.red(ev)));
        if (events.tsumogiri(ev)) rt.put("tsumogiri", true);
        if (events.riichi(ev)) rt.put("riichi", true);
        if (events.calledBy(ev) != EventLog.NONE) rt.put("called", true);
        river.add(rt);
      }
      player.put("river", river);

      List<MeldView> melds = new ArrayList<>(view.meldCount(s));
      for (int i = 0; i < view.meldCount(s); i++) {
        melds.add(MeldLayout.of(view.meld(s, i), s, RULES.seats()));
      }
      player.put("melds", melds);

      int concealed = s == seat
          ? hand.size() + (draw != null ? 1 : 0)
          : 13 - 3 * view.meldCount(s) + (activeMayHoldExtra && s == view.current() ? 1 : 0);
      player.put("concealed", concealed);
      players.add(player);
    }

    Map<String, Object> lastEvent = new LinkedHashMap<>();
    lastEvent.put("kind", last.kind().name());
    lastEvent.put("seat", last.seat());
    // SeatView가 이미 타인 쯔모를 NONE으로 가리지만, 직렬화에서도 한 번 더 보장한다.
    String lastTile = (last.kind() == TurnEvent.Kind.DRAW && last.seat() != seat) || last.tile() == TurnEvent.NONE
        ? null
        : TileCode.of(last.tile(), last.red());
    lastEvent.put("tile", lastTile);
    lastEvent.put("kanKind", last.kanKind() == null ? null : last.kanKind().name());

    Map<String, Object> table = new LinkedHashMap<>();
    table.put("seat", seat);
    table.put("dealer", view.dealer());
    table.put("activeSeat", view.current());
    table.put("honba", view.honba());
    table.put("kyotaku", view.kyotaku());
    table.put("remaining", view.liveLeft());
    table.put("furiten", view.furiten());
    table.put("dora", dora);
    table.put("hand", hand);
    table.put("draw", draw);
    table.put("lastEvent", lastEvent);
    table.put("players", players);

    EastMatch.Decision decision = match.decision(seat);
    Map<String, Object> decisionMap = null;
    if (decision != null) {
      decisionMap = new LinkedHashMap<>();
      decisionMap.put("revision", decision.revision());
      decisionMap.put("kind", decision.kind().name());
      List<Map<String, Object>> actions = new ArrayList<>(decision.actions().size());
      for (int code : decision.actions()) {
        actions.add(describeAction(code));
      }
      decisionMap.put("actions", actions);
    }

    Map<String, Object> out = new LinkedHashMap<>();
    out.put("matchNumber", matchIndex);
    out.put("phase", phase.name());
    out.put("round", roundMap(round));
    out.put("roundLabel", "E" + round.kyoku());
    out.put("revision", match.revision());
    out.put("table", table);
    out.put("decision", decisionMap);
    out.put("settlement", match.settlement() == null ? null : settlementMap(match.settlement()));
    return out;
  }

  private static Map<String, Object> describeAction(int code) {
    Action action = Action.of(code);
    Map<String, Object> map = new LinkedHashMap<>();
    map.put("code", code);
    map.put("type", action.type().name());
    switch (action.type()) {
      case DAHAI, RIICHI_DAHAI -> {
        map.put("tile", TileCode.of(action.tile(), action.selection().red()));
        map.put("tsumogiri", action.selection().tsumogiri());
      }
      case CHI -> {
        boolean handRed = action.selection().red();
        map.put("tile", TileCode.of(action.tile(), false));
        List<String> tiles = new ArrayList<>(3);
        List<String> consumed = new ArrayList<>(2);
        for (int k = action.base(); k < action.base() + 3; k++) {
          boolean red = k != action.tile() && handRed && Tiles.contains(Tiles.FIVES, k);
          String t = TileCode.of(k, red);
          tiles.add(t);
          if (k != action.tile()) consumed.add(t);
        }
        map.put("tiles", tiles);
        map.put("consumed", consumed);
        map.put("red", handRed);
      }
      case PON -> {
        map.put("tile", TileCode.of(action.tile(), false));
        map.put("red", action.selection().red());
      }
      case DAIMINKAN, ANKAN, KAKAN -> map.put("tile", TileCode.of(action.tile(), false));
      default -> {}
    }
    return map;
  }

  private static Map<String, Object> roundMap(EastMatch.Round round) {
    return Map.of("kyoku", round.kyoku(), "honba", round.honba());
  }

  private static Map<String, Object> settlementMap(EastMatch.Settlement s) {
    Map<String, Object> map = new LinkedHashMap<>();
    map.put("round", roundMap(s.round()));
    map.put("outcome", s.outcome().name());
    map.put("abortive", s.abortive() == null ? null : s.abortive().name());
    map.put("winners", s.winners());
    map.put("loser", s.loser());
    map.put("scores", s.scores());
    map.put("changes", s.changes());
    map.put("deposits", s.deposits());
    map.put("ranks", s.ranks());
    map.put("nextRound", s.nextRound() == null ? null : roundMap(s.nextRound()));
    return map;
  }
}
