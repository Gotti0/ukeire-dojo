package ukeire_dojo.live;

import com.epsilon.mahjong.action.Action;
import com.epsilon.mahjong.rules.Progression;
import com.epsilon.mahjong.rules.RoundOutcome;
import com.epsilon.mahjong.rules.RoundStart;
import com.epsilon.mahjong.rules.Settlement;
import com.epsilon.mahjong.rules.TableState;
import com.epsilon.mahjong.rules.Variant;
import com.google.gson.JsonParser;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.SplittableRandom;
import java.util.random.RandomGenerator;
import java.util.stream.IntStream;

public final class EastMatchTest {
  private static final Variant RULES = Variant.TENHOU_4P;

  public static void main(String[] args) {
    rejectsStaleAndInvalidChoices();
    preservesRoundsAcrossDifferentPaths();
    settlesEastFourAndBust();
    hostsSeatIsolatedScriptedMatch();
    evaluatesReplayDiscardHintsAndRejectsMatchOne();
    System.out.println("EastMatchTest: 5 checks passed");
  }

  private static void rejectsStaleAndInvalidChoices() {
    var series = new EastSeries(new SplittableRandom(8));
    var match = series.first();
    expect(IllegalStateException.class, series::startReplay);
    var opening = match.decision(0);
    check(match.decision(1) == null, "only the dealer may act on the first draw");
    int legal = opening.actions().getFirst();
    expect(UnsupportedOperationException.class, () -> opening.actions().add(0));
    expect(IllegalArgumentException.class, () -> match.commit(opening.revision(), Map.of(1, legal)));
    expect(IllegalArgumentException.class, () -> match.commit(opening.revision(), Map.of(0, -1)));
    equal(opening, match.decision(0), "rejected actions changed the decision");
    match.commit(opening.revision(), Map.of(0, legal));
    expect(IllegalStateException.class, () -> match.commit(opening.revision(), Map.of(0, legal)));
  }

  private static void preservesRoundsAcrossDifferentPaths() {
    var random = new CountingRandom(2);
    var series = new EastSeries(random);
    var first = play(series.first(), random, false, Map.of());
    int firstDraws = random.draws;
    var replay = series.startReplay();
    equal(firstDraws, random.draws, "replay shuffled the opening wall");
    check(replay == series.startReplay(), "request retry created a new replay");
    for (int seat = 0; seat < RULES.seats(); seat++) {
      equal(25000, replay.seatView(seat).score(seat), "replay did not reset the scores");
    }
    var second = play(replay, random, true, first);

    var firstPath = new ArrayList<>(first.keySet());
    var secondPath = new ArrayList<>(second.keySet());
    boolean branchedAndRejoined = false;
    for (var round : firstPath) {
      if (second.containsKey(round)) {
        equal(first.get(round), second.get(round), "replay deal or dora differed");
        if (firstPath.indexOf(round) != secondPath.indexOf(round)) branchedAndRejoined = true;
      }
    }
    check(branchedAndRejoined, "fixture did not rejoin after a divergence: " + firstPath + " / " + secondPath);
    equal(new EastMatch.Round(1, 0), firstPath.getFirst(), "first match began at wrong round");
    equal(firstPath.getFirst(), secondPath.getFirst(), "replay began at wrong round");
    System.out.printf("paired_paths first=%s replay=%s shuffled_walls=%d%n",
        firstPath, secondPath, random.draws / (RULES.tiles().size() - 1));
  }

  private record Deal(List<List<Integer>> hands, int dora) {}

  private static Map<EastMatch.Round, Deal> play(
      EastMatch match, CountingRandom random, boolean call, Map<EastMatch.Round, Deal> known) {
    var dealt = new LinkedHashMap<EastMatch.Round, Deal>();
    while (true) {
      var round = match.round();
      var hands = IntStream.range(0, RULES.seats()).mapToObj(seat -> {
        var view = match.seatView(seat);
        return IntStream.range(0, RULES.tiles().size()).filter(view::holds).boxed().toList();
      }).toList();
      var view = match.seatView(0);
      dealt.put(round, new Deal(hands, view.indicator(0)));
      while (match.phase() == EastMatch.Phase.DECISIONS) {
        var actions = new LinkedHashMap<Integer, Integer>();
        long revision = -1;
        for (int seat = 0; seat < RULES.seats(); seat++) {
          var decision = match.decision(seat);
          if (decision != null) {
            actions.put(seat, choose(decision.actions(), call));
            revision = decision.revision();
          }
        }
        match.commit(revision, actions);
      }
      var settled = match.settlement();
      equal(100000, settled.scores().stream().mapToInt(Integer::intValue).sum()
          + Variant.DEPOSIT * settled.deposits(), "points and deposits did not balance");
      if (match.phase() == EastMatch.Phase.MATCH_END) {
        expect(IllegalStateException.class, () -> match.nextRound(round));
        check(settled.nextRound() == null && settled.deposits() == 0, "incomplete final settlement");
        return dealt;
      }
      int draws = random.draws;
      match.nextRound(round);
      expect(IllegalStateException.class, () -> match.nextRound(round));
      var nextView = match.seatView(0);
      equal(settled.deposits(), nextView.kyotaku(), "deposits did not carry over");
      for (int seat = 0; seat < RULES.seats(); seat++) {
        equal(settled.scores().get(seat), nextView.score(seat), "scores did not carry over");
      }
      int expected = known.containsKey(match.round()) ? 0 : RULES.tiles().size() - 1;
      equal(expected, random.draws - draws, "a common round was shuffled again or a new round was not shuffled");
    }
  }

  private static int choose(List<Integer> legal, boolean call) {
    Integer discard = null;
    Integer pass = null;
    Integer meld = null;
    for (int code : legal) {
      switch (Action.of(code).type()) {
        case RON_AGARI, TSUMO_AGARI -> { return code; }
        case RIICHI_DAHAI -> {
          if (call) return code;
        }
        case DAHAI -> {
          if (discard == null || call) discard = code;
        }
        case PASS -> pass = code;
        case CHI, PON, DAIMINKAN -> {
          if (meld == null) meld = code;
        }
        default -> {}
      }
    }
    if (call && meld != null) return meld;
    if (discard != null) return discard;
    return pass == null ? legal.getFirst() : pass;
  }

  private static void settlesEastFourAndBust() {
    var start = new RoundStart(RULES, 3, 1, 1, new int[] {25000, 25000, 25000, 24000});
    var result = new RoundOutcome(RULES);
    result.setExhaustive(1 << 3, 0, 0);
    var continuation = settle(start, result);
    equal(new EastMatch.Round(4, 2), continuation.nextRound(), "dealer tenpai did not repeat East 4");
    equal(1, continuation.deposits(), "deposits were lost between hands");
    result.setExhaustive(1, 0, 0);
    var finished = settle(start, result);
    equal(null, finished.nextRound(), "the match continued into South");
    equal(List.of(29000, 24000, 24000, 23000), finished.scores(), "final deposits or draw settlement incorrect");
    equal(List.of(4000, -1000, -1000, -1000), finished.changes(), "final deposit was absent from the score change");
    equal(List.of(1, 2, 3, 4), finished.ranks(), "tie order differs from starting seat order");

    var low = new RoundStart(RULES, 1, 0, 1, new int[] {100, 48900, 25000, 25000});
    result.setRon(2000, 2, 0, RoundOutcome.NO_SEAT, 0);
    var zero = new RoundStart(RULES, 1, 0, 1, new int[] {8000, 41000, 25000, 25000});
    var atZero = settle(zero, result);
    equal(0, atZero.scores().getFirst(), "zero point fixture was not exact");
    equal(new EastMatch.Round(3, 0), atZero.nextRound(), "zero points should not end the game");
    var busted = settle(low, result);
    equal(null, busted.nextRound(), "a negative score did not end the game");
    equal(0, busted.deposits(), "a finished game retained deposits");
    equal(100000, busted.scores().stream().mapToInt(Integer::intValue).sum(), "bust settlement paid deposits twice");
  }

  @SuppressWarnings("unchecked")
  private static void hostsSeatIsolatedScriptedMatch() {
    var host = LiveMatchHost.scripted();
    var created = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"create_series\",\"roomId\":\"ROOM01\"}").getAsJsonObject());
    equal("DECISIONS", created.get("phase"), "series did not start in DECISIONS");
    equal(List.of(0), created.get("pendingSeats"), "dealer must act first");

    var seat0 = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"seat_state\",\"roomId\":\"ROOM01\",\"seat\":0}").getAsJsonObject());
    var seat1 = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"seat_state\",\"roomId\":\"ROOM01\",\"seat\":1}").getAsJsonObject());
    var table0 = (Map<String, Object>) seat0.get("table");
    var table1 = (Map<String, Object>) seat1.get("table");
    check(table0.get("draw") != null, "dealer must see its own opening draw");
    check(table1.get("draw") == null, "non-dealer must not see a draw");
    var last1 = (Map<String, Object>) table1.get("lastEvent");
    equal("DRAW", last1.get("kind"), "lastEvent kind differed");
    equal(null, last1.get("tile"), "other seat saw dealer draw tile");
    var players1 = (List<Map<String, Object>>) table1.get("players");
    for (var p : players1) {
      check(!p.containsKey("hand") && !p.containsKey("draw"), "other player object leaked private fields");
    }
    equal(14, players1.get(0).get("concealed"), "dealer concealed count wrong before discard");
    check(seat1.get("decision") == null, "waiting seat should not receive decision");

    int steps = 0;
    Map<String, Object> pending = created;
    while (!"MATCH_END".equals(pending.get("phase"))) {
      if ("ROUND_END".equals(pending.get("phase"))) {
        var rnd = (Map<String, Object>) pending.get("round");
        pending = (Map<String, Object>) host.handle(JsonParser.parseString(
            String.format("{\"cmd\":\"next_round\",\"roomId\":\"ROOM01\",\"kyoku\":%s,\"honba\":%s}",
                rnd.get("kyoku"), rnd.get("honba"))).getAsJsonObject());
        continue;
      }
      var seats = (List<Integer>) pending.get("pendingSeats");
      var actions = new StringBuilder("{");
      for (int i = 0; i < seats.size(); i++) {
        int s = seats.get(i);
        var choice = (Map<String, Object>) host.handle(JsonParser.parseString(
            String.format("{\"cmd\":\"ai_choice\",\"roomId\":\"ROOM01\",\"seat\":%d}", s)).getAsJsonObject());
        if (i > 0) actions.append(',');
        actions.append('"').append(s).append("\":").append(choice.get("action"));
      }
      actions.append('}');
      pending = (Map<String, Object>) host.handle(JsonParser.parseString(
          String.format("{\"cmd\":\"commit\",\"roomId\":\"ROOM01\",\"revision\":%s,\"actions\":%s}",
              pending.get("revision"), actions)).getAsJsonObject());
      steps++;
      check(steps < 5000, "scripted match did not terminate");
    }
    var settlement = (Map<String, Object>) pending.get("settlement");
    var scores = (List<Integer>) settlement.get("scores");
    equal(100000, scores.stream().mapToInt(Integer::intValue).sum(), "final total points differed");
    host.handle(JsonParser.parseString("{\"cmd\":\"delete_series\",\"roomId\":\"ROOM01\"}").getAsJsonObject());
  }

  @SuppressWarnings("unchecked")
  private static void evaluatesReplayDiscardHintsAndRejectsMatchOne() {
    var host = LiveMatchHost.scripted();
    var created = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"create_series\",\"roomId\":\"ROOM02\"}").getAsJsonObject());
    // 1경기에서는 추천을 거부해야 한다.
    expect(IllegalArgumentException.class, () -> host.handle(JsonParser.parseString(
        "{\"cmd\":\"discard_hint\",\"roomId\":\"ROOM02\",\"seat\":0,\"matchIndex\":1}").getAsJsonObject()));
    // 1경기 완료 전에는 2경기를 시작할 수 없다.
    expect(IllegalStateException.class, () -> host.handle(JsonParser.parseString(
        "{\"cmd\":\"start_replay\",\"roomId\":\"ROOM02\"}").getAsJsonObject()));

    var firstSeat0 = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"seat_state\",\"roomId\":\"ROOM02\",\"seat\":0,\"matchIndex\":1}").getAsJsonObject());
    var firstTable0 = (Map<String, Object>) firstSeat0.get("table");

    // 1경기 완주
    int steps = 0;
    Map<String, Object> pending = created;
    while (!"MATCH_END".equals(pending.get("phase"))) {
      if ("ROUND_END".equals(pending.get("phase"))) {
        var rnd = (Map<String, Object>) pending.get("round");
        pending = (Map<String, Object>) host.handle(JsonParser.parseString(
            String.format("{\"cmd\":\"next_round\",\"roomId\":\"ROOM02\",\"matchIndex\":1,\"kyoku\":%s,\"honba\":%s}",
                rnd.get("kyoku"), rnd.get("honba"))).getAsJsonObject());
        continue;
      }
      var seats = (List<Integer>) pending.get("pendingSeats");
      var actions = new StringBuilder("{");
      for (int i = 0; i < seats.size(); i++) {
        int s = seats.get(i);
        var choice = (Map<String, Object>) host.handle(JsonParser.parseString(
            String.format("{\"cmd\":\"ai_choice\",\"roomId\":\"ROOM02\",\"matchIndex\":1,\"seat\":%d}", s)).getAsJsonObject());
        if (i > 0) actions.append(',');
        actions.append('"').append(s).append("\":").append(choice.get("action"));
      }
      actions.append('}');
      pending = (Map<String, Object>) host.handle(JsonParser.parseString(
          String.format("{\"cmd\":\"commit\",\"roomId\":\"ROOM02\",\"matchIndex\":1,\"revision\":%s,\"actions\":%s}",
              pending.get("revision"), actions)).getAsJsonObject());
      steps++;
      check(steps < 5000, "first match did not terminate");
    }

    // 2경기 시작 및 초기 국면·점수·배패·도라 일치 검증
    var replayPending = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"start_replay\",\"roomId\":\"ROOM02\"}").getAsJsonObject());
    equal(2, replayPending.get("matchNumber"), "replay matchNumber must be 2");
    equal("DECISIONS", replayPending.get("phase"), "replay did not start in DECISIONS");
    var replaySeat0 = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"seat_state\",\"roomId\":\"ROOM02\",\"seat\":0,\"matchIndex\":2}").getAsJsonObject());
    var replayTable0 = (Map<String, Object>) replaySeat0.get("table");
    equal(firstTable0.get("hand"), replayTable0.get("hand"), "replay opening hand differed from first match");
    equal(firstTable0.get("draw"), replayTable0.get("draw"), "replay opening draw differed from first match");
    equal(firstTable0.get("dora"), replayTable0.get("dora"), "replay opening dora differed from first match");
    for (var p : (List<Map<String, Object>>) replayTable0.get("players")) {
      equal(25000, p.get("score"), "replay did not reset scores to 25000");
    }

    // 2경기 동가 차례 추천 검증 및 상태 불변 확인
    var hint = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"discard_hint\",\"roomId\":\"ROOM02\",\"seat\":0,\"matchIndex\":2}").getAsJsonObject());
    equal(2, hint.get("matchNumber"), "hint matchNumber differed");
    equal(replaySeat0.get("revision"), hint.get("revision"), "hint revision differed");
    var candidates = (List<Map<String, Object>>) hint.get("candidates");
    check(!candidates.isEmpty(), "hint candidates empty");
    equal(0.0, candidates.getFirst().get("loss"), "best candidate loss must be 0.0");
    equal(candidates.getFirst().get("tile"), hint.get("bestTile"), "bestTile must match first candidate");
    for (var c : candidates) {
      double loss = ((Number) c.get("loss")).doubleValue();
      double val = ((Number) c.get("value")).doubleValue();
      check(Double.isFinite(loss) && loss >= 0.0 && Double.isFinite(val), "non-finite or negative hint value/loss");
    }
    var replayAfterHint = (Map<String, Object>) host.handle(JsonParser.parseString(
        "{\"cmd\":\"seat_state\",\"roomId\":\"ROOM02\",\"seat\":0,\"matchIndex\":2}").getAsJsonObject());
    equal(replaySeat0, replayAfterHint, "discard_hint mutated seat_state");

    // 대기 좌석의 추천 요청은 거부
    expect(IllegalStateException.class, () -> host.handle(JsonParser.parseString(
        "{\"cmd\":\"discard_hint\",\"roomId\":\"ROOM02\",\"seat\":1,\"matchIndex\":2}").getAsJsonObject()));
    host.handle(JsonParser.parseString("{\"cmd\":\"delete_series\",\"roomId\":\"ROOM02\"}").getAsJsonObject());
  }

  private static EastMatch.Settlement settle(RoundStart start, RoundOutcome result) {
    var table = new TableState(RULES);
    table.set(start);
    var next = new TableState(RULES);
    var delta = new int[RULES.seats()];
    Settlement.apply(RULES, table, result, delta);
    Progression.next(table, result, delta, next);
    return EastMatch.settle(start, result, next);
  }

  private static final class CountingRandom implements RandomGenerator {
    private final SplittableRandom random;
    int draws;

    CountingRandom(long seed) { random = new SplittableRandom(seed); }
    @Override public long nextLong() { return random.nextLong(); }
    @Override public int nextInt(int bound) {
      draws++;
      return random.nextInt(bound);
    }
  }

  private static void expect(Class<? extends RuntimeException> type, Runnable operation) {
    try {
      operation.run();
    } catch (RuntimeException error) {
      if (type.isInstance(error)) return;
      throw error;
    }
    throw new AssertionError("expected " + type.getSimpleName());
  }

  private static void equal(Object expected, Object actual, String message) {
    if (!java.util.Objects.equals(expected, actual)) {
      throw new AssertionError(message + ": expected " + expected + ", got " + actual);
    }
  }

  private static void check(boolean condition, String message) {
    if (!condition) throw new AssertionError(message);
  }
}
