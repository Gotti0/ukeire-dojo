package ukeire_dojo.live;

import com.epsilon.agent.observe.Observation;
import com.epsilon.agent.observe.ObservationBatch;
import com.epsilon.agent.observe.ObservationWriter;
import com.epsilon.agent.policy.Evaluation;
import com.epsilon.agent.runtime.Kernels;
import com.epsilon.agent.runtime.NetworkDevices;
import com.epsilon.agent.runtime.NetworkRuntime;
import com.epsilon.agent.runtime.Precision;
import com.epsilon.mahjong.action.Action;
import com.epsilon.mahjong.engine.Boundary;
import com.epsilon.mahjong.engine.Decisions;
import com.epsilon.mahjong.engine.Engine;
import com.epsilon.mahjong.engine.RuleWorkspace;
import com.epsilon.mahjong.engine.SeatView;
import com.epsilon.mahjong.engine.TurnEvent;
import com.epsilon.mahjong.engine.Wall;
import com.epsilon.mahjong.rules.Placement;
import com.epsilon.mahjong.rules.Progression;
import com.epsilon.mahjong.rules.RoundOutcome;
import com.epsilon.mahjong.rules.RoundStart;
import com.epsilon.mahjong.rules.Settlement;
import com.epsilon.mahjong.rules.TableState;
import com.epsilon.mahjong.rules.Variant;
import com.epsilon.mahjong.tile.Tiles;
import com.epsilon.prism.policy.PrismPolicy;
import com.epsilon.prism.run.Snapshot;
import com.epsilon.prism.settings.BehaviourSettings;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.SplittableRandom;
import java.util.stream.IntStream;

/** 실제 체크포인트로 사람 입력을 흉내 낸 두 좌석과 PRISM 두 좌석을 한 엔진에서 진행한다. */
public final class LiveSpike {
  private static final Variant RULES = Variant.TENHOU_4P;
  private static final int EAST_ROUNDS = RULES.seats();
  private static final long FIXTURE_SEED = 0x75E52026L;

  private LiveSpike() {}

  public static void main(String[] args) throws Exception {
    if (args.length != 2) throw new IllegalArgumentException("usage: LiveSpike SNAPSHOT cpu|gpu0");
    var device = NetworkDevices.device(args[1]);
    boolean gpu = device.isGpu();
    var workspace = new RuleWorkspace(RULES);
    checkSeatIsolation(workspace);
    checkEastBoundary();
    long started = System.nanoTime();
    try (var runtime = new NetworkRuntime(1, 1, 1);
        var policy = new PrismPolicy(
            Snapshot.open(Path.of(args[0]), List.of(device), runtime, 1,
                gpu ? Precision.BFLOAT16 : Precision.FLOAT32, gpu ? Kernels.NATIVE : Kernels.EAGER),
            RULES, "live-spike", null, BehaviourSettings.defaults().twinDiscard())) {
      System.out.printf(Locale.ROOT, "device=%s open_ms=%.1f%n", args[1], elapsedMs(started));
      var live = new Runner(workspace, policy);
      Match first = live.play(1);
      Match second = live.play(2);
      int common = 0;
      for (var entry : first.walls().entrySet()) {
        Deal replay = second.walls().get(entry.getKey());
        if (replay == null) continue;
        check(Arrays.equals(entry.getValue().wall(), replay.wall()), "same round and honba need the same wall");
        check(entry.getValue().hands().equals(replay.hands()), "starting hands changed");
        common++;
      }
      check(common > 1, "the matches must share more than the initial deal");
      check(first.initialDiscard() != second.initialDiscard(), "scripted first discard must branch");
      check(live.humans[0] > 0 && live.humans[1] > 0, "both human input seats must act");
      check(live.bots[2] > 0 && live.bots[3] > 0, "both AI seats must act");
      check(live.multiSeatWindows > 0, "a simultaneous response window must be exercised");
      check(!live.hints.isEmpty(), "the assisted match must evaluate discard candidates");
      System.out.printf(
          "comparison shared_walls=%d first_only=%d second_only=%d first_discard=%d/%d "
              + "human_inputs=%s ai_inputs=%s multi_seat_windows=%d%n",
          common, first.walls().size() - common, second.walls().size() - common,
          first.initialDiscard(), second.initialDiscard(), Arrays.toString(live.humans),
          Arrays.toString(live.bots), live.multiSeatWindows);
      report("bot", live.aiTimes);
      report("discard_hint", live.hints);
    }
  }

  private record Key(int round, int honba) {}
  private record Deal(int[] wall, List<List<Integer>> hands) {}
  private record Match(Map<Key, Deal> walls, int initialDiscard) {}

  private static final class Runner {
    final int[] humans = new int[RULES.seats()];
    final int[] bots = new int[RULES.seats()];
    final List<Double> hints = new ArrayList<>();
    final List<Double> aiTimes = new ArrayList<>();
    int multiSeatWindows;

    private final RuleWorkspace workspace;
    private final PrismPolicy policy;
    private final ObservationWriter writer = new ObservationWriter(RULES);
    private final ObservationBatch batch = new ObservationBatch(RULES, 64);
    private final Observation observation = new Observation(RULES);

    Runner(RuleWorkspace workspace, PrismPolicy policy) {
      this.workspace = workspace;
      this.policy = policy;
    }

    Match play(int game) {
      Map<Key, Deal> deals = new HashMap<>();
      var engine = new Engine(RULES);
      RoundStart start = RoundStart.first(RULES);
      int initialDiscard = -1;
      while (true) {
        var key = new Key(start.round(), start.honba());
        int[] wall = wall(key);
        engine.startRecordedRound(start, wall);
        Boundary boundary = engine.advance(workspace);
        checkDeal(engine, wall);
        check(deals.put(key, new Deal(wall, hands(engine))) == null, "round and honba repeated");
        while (boundary == Boundary.DECISIONS) {
          Decisions decisions = engine.decisions();
          int[] choices = new int[decisions.size()];
          if (decisions.size() > 1) multiSeatWindows++;
          for (int slot = 0; slot < decisions.size(); slot++) {
            int seat = decisions.seat(slot);
            if (seat < 2) {
              boolean initialTurn = initialDiscard == -1;
              choices[slot] = scriptedChoice(decisions, slot, game == 2);
              humans[seat]++;
              if (initialTurn) initialDiscard = decisions.legal(slot, choices[slot]);
              if (game == 2 && decisions.legalCount(slot) > 1
                  && Action.of(decisions.legal(slot, choices[slot])).type() == Action.Type.DAHAI) {
                hint(engine, decisions, slot);
              }
            } else {
              bots[seat]++;
              if (decisions.legalCount(slot) > 1) {
                long began = System.nanoTime();
                choices[slot] = evaluate(engine, decisions, slot).greedy();
                aiTimes.add(elapsedMs(began));
              }
            }
          }
          boundary = engine.commit(choices, workspace);
        }

        var end = engine.roundEnd();
        var next = end.next();
        int[] scores = IntStream.range(0, RULES.seats()).map(next::score).toArray();
        check(Arrays.stream(scores).sum() + Variant.DEPOSIT * next.kyotaku()
            == RULES.startPoints() * RULES.seats(), "points and deposits must balance");
        System.out.printf(
            "game=%d hand=E%d-%d outcome=%s next_round=%d next_honba=%d deposits=%d scores=%s%n",
            game, key.round() + 1, key.honba(), end.outcome().kind(), next.round() + 1,
            next.honba(), next.kyotaku(), Arrays.toString(scores));
        // 반장 엔진의 정산은 유지하고, 실험에서는 동4국에서 친이 넘어가면 동풍전을 끝낸다.
        if (!continuesEast(next)) {
          Placement.awardDeposits(scores, next.kyotaku());
          System.out.printf("game=%d finished final=%s%n", game, Arrays.toString(scores));
          return new Match(deals, initialDiscard);
        }
        start = new RoundStart(RULES, next.round(), next.honba(), next.kyotaku(), scores);
      }
    }

    private Evaluation evaluate(Engine engine, Decisions decisions, int slot) {
      batch.clear();
      int index = writer.capture(engine.view(decisions.seat(slot)), decisions, slot, batch);
      return policy.evaluate(observation.bind(batch, index));
    }

    private void hint(Engine engine, Decisions decisions, int slot) {
      long decisionId = decisions.id(slot);
      List<Integer> before = hand(engine.view(decisions.seat(slot)));
      long started = System.nanoTime();
      float[] values = evaluate(engine, decisions, slot).values();
      check(values != null && values.length == decisions.legalCount(slot), "missing action values");
      int[] discards = IntStream.range(0, values.length).filter(
          i -> Action.of(decisions.legal(slot, i)).type() == Action.Type.DAHAI).toArray();
      float best = values[discards[0]];
      for (int i : discards) best = Math.max(best, values[i]);
      var candidates = new ArrayList<String>();
      for (int i : discards) {
        float loss = best - values[i];
        check(Float.isFinite(loss) && loss >= 0, "invalid discard loss");
        candidates.add(String.format(Locale.ROOT, "%d:%.5f/%.5f", decisions.legal(slot, i), values[i], loss));
      }
      hints.add(elapsedMs(started));
      check(engine.decisions().id(slot) == decisionId
          && before.equals(hand(engine.view(decisions.seat(slot)))), "hint changed the decision");
      if (hints.size() == 1) {
        System.out.printf("hint seat=%d decision=%d action:value/loss=%s%n",
            decisions.seat(slot), decisionId, candidates);
      }
    }
  }

  private static int scriptedChoice(Decisions decisions, int slot, boolean alternateDiscard) {
    int first = -1;
    int alternate = -1;
    for (int i = 0; i < decisions.legalCount(slot); i++) {
      Action action = Action.of(decisions.legal(slot, i));
      switch (action.type()) {
        case RON_AGARI, TSUMO_AGARI -> { return i; }
        case PASS -> {
          if (first == -1) first = i;
        }
        case DAHAI -> {
          if (first == -1) first = i;
          else if (alternateDiscard && action.tile() != Action.of(decisions.legal(slot, first)).tile()) alternate = i;
        }
        default -> {}
      }
    }
    if (alternate != -1) return alternate;
    return first == -1 ? 0 : first;
  }

  private static void checkDeal(Engine engine, int[] wall) {
    int dealer = engine.handStart().dealer();
    for (int offset = 0; offset < RULES.seats(); offset++) {
      SeatView view = engine.view((dealer + offset) % RULES.seats());
      for (int i = 0; i < Variant.HAND; i++) {
        check(view.holds(wall[Wall.dealtPosition(RULES, offset, i)]), "a starting tile differs from the wall");
      }
      check(view.indicator(0) == wall[Wall.indicatorPosition(RULES, 0)], "dora indicator differed");
    }
    int firstDraw = wall[Wall.livePosition(RULES, 0)];
    check(engine.view(dealer).holds(firstDraw)
        && engine.view(dealer).last().tile() == Tiles.kind(firstDraw), "dealer drew outside the wall order");
  }

  private static boolean continuesEast(TableState next) {
    return !next.gameOver() && next.round() < EAST_ROUNDS;
  }

  private static void checkEastBoundary() {
    var start = new TableState(RULES);
    var next = new TableState(RULES);
    var outcome = new RoundOutcome(RULES);
    var delta = new int[RULES.seats()];
    start.set(3, 1, 1, new int[] {25000, 25000, 25000, 24000});
    outcome.setExhaustive(1 << 3, 0, 0);
    Settlement.apply(RULES, start, outcome, delta);
    Progression.next(start, outcome, delta, next);
    check(continuesEast(next) && next.round() == 3 && next.honba() == 2, "dealer tenpai must repeat East 4");
    outcome.setExhaustive(1, 0, 0);
    Settlement.apply(RULES, start, outcome, delta);
    Progression.next(start, outcome, delta, next);
    check(!next.gameOver() && !continuesEast(next) && next.round() == 4, "East end must override hanchan");
    int[] scores = IntStream.range(0, RULES.seats()).map(next::score).toArray();
    check(scores[0] == 28000 && next.kyotaku() == 1, "draw settlement or deposits changed");
    Placement.awardDeposits(scores, next.kyotaku());
    check(scores[0] == 29000 && Arrays.stream(scores).sum() == 100000, "ended game must award deposits once");
    System.out.println("checks=east-4-dealer-repeat,east-finish,final-deposit-award");
  }

  private static int[] wall(Key key) {
    int[] tiles = IntStream.range(0, RULES.tiles().size()).map(RULES.tiles()::physicalId).toArray();
    var rng = new SplittableRandom(FIXTURE_SEED ^ ((long) key.round() << 32) ^ key.honba());
    for (int i = tiles.length - 1; i > 0; i--) swap(tiles, i, rng.nextInt(i + 1));
    return tiles;
  }

  private static void checkSeatIsolation(RuleWorkspace workspace) {
    int[] firstWall = wall(new Key(0, 0));
    var altered = firstWall.clone();
    int opponent = Wall.dealtPosition(RULES, 1, 0);
    int unseen = Wall.livePosition(RULES, 1);
    while (Tiles.kind(altered[opponent]) == Tiles.kind(altered[unseen])) unseen++;
    swap(altered, opponent, unseen);
    var a = new Engine(RULES);
    var b = new Engine(RULES);
    for (Engine engine : List.of(a, b)) {
      engine.startRecordedRound(RoundStart.first(RULES), engine == a ? firstWall : altered);
      check(engine.advance(workspace) == Boundary.DECISIONS, "first deal must request a decision");
      TurnEvent otherDraw = engine.view(1).last();
      check(otherDraw.kind() == TurnEvent.Kind.DRAW && otherDraw.tile() == TurnEvent.NONE, "other draw was visible");
    }
    check(!hand(a.view(1)).equals(hand(b.view(1))), "fixture did not change the other hand");
    check(hand(a.view(0)).equals(hand(b.view(0))), "the acting seat hand changed");
    check(Arrays.equals(observation(a), observation(b)), "hidden tiles changed the acting seat's model input");
    Decisions decisions = a.decisions();
    long decisionId = decisions.id(0);
    try {
      a.commit(new int[] {decisions.legalCount(0)}, workspace);
      throw new IllegalStateException("invalid choice was accepted");
    } catch (IllegalArgumentException expected) {
      check(a.decisions().id(0) == decisionId && Arrays.equals(observation(a), observation(b)), "invalid choice mutated the engine");
    }
    System.out.println("checks=seat-observation-isolation,other-draw-masked,illegal-choice-atomic");
  }

  private static long[] observation(Engine engine) {
    var batch = new ObservationBatch(RULES, 64);
    int row = new ObservationWriter(RULES).capture(engine.view(0), engine.decisions(), 0, batch);
    return Arrays.copyOfRange(batch.words(), batch.start(row), batch.start(row) + batch.longs(row));
  }

  private static List<List<Integer>> hands(Engine engine) {
    return IntStream.range(0, RULES.seats()).mapToObj(seat -> hand(engine.view(seat))).toList();
  }

  private static List<Integer> hand(SeatView view) {
    return IntStream.range(0, RULES.tiles().size()).filter(view::holds).boxed().toList();
  }

  private static void swap(int[] tiles, int i, int j) {
    int tile = tiles[i];
    tiles[i] = tiles[j];
    tiles[j] = tile;
  }

  private static void report(String kind, List<Double> times) {
    check(!times.isEmpty(), "no timings for " + kind);
    times.sort(Double::compare);
    System.out.printf(Locale.ROOT, "%s n=%d p50_ms=%.2f p95_ms=%.2f max_ms=%.2f%n",
        kind, times.size(), times.get((times.size() - 1) / 2),
        times.get((int) Math.ceil(times.size() * 0.95) - 1), times.getLast());
  }

  private static double elapsedMs(long start) {
    return (System.nanoTime() - start) / 1_000_000.0;
  }

  private static void check(boolean condition, String message) {
    if (!condition) throw new IllegalStateException(message);
  }
}
