package ukeire_dojo.live;

import com.epsilon.mahjong.action.Action;
import com.epsilon.mahjong.rules.Progression;
import com.epsilon.mahjong.rules.RoundOutcome;
import com.epsilon.mahjong.rules.RoundStart;
import com.epsilon.mahjong.rules.Settlement;
import com.epsilon.mahjong.rules.TableState;
import com.epsilon.mahjong.rules.Variant;
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
    System.out.println("EastMatchTest: 3 checks passed");
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
