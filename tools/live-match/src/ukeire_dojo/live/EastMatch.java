package ukeire_dojo.live;

import com.epsilon.mahjong.engine.Boundary;
import com.epsilon.mahjong.engine.Decisions;
import com.epsilon.mahjong.engine.Engine;
import com.epsilon.mahjong.engine.EngineDecisionKind;
import com.epsilon.mahjong.engine.RuleWorkspace;
import com.epsilon.mahjong.engine.SeatView;
import com.epsilon.mahjong.rules.AbortiveDraw;
import com.epsilon.mahjong.rules.EndKind;
import com.epsilon.mahjong.rules.Placement;
import com.epsilon.mahjong.rules.RoundOutcome;
import com.epsilon.mahjong.rules.RoundStart;
import com.epsilon.mahjong.rules.TableState;
import com.epsilon.mahjong.rules.Variant;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.IntStream;

/** 동풍전 한 경기의 진행을 맡는다. 방의 작업 스레드에서만 호출한다. */
public final class EastMatch {
  private static final Variant RULES = Variant.TENHOU_4P;

  public enum Phase { DECISIONS, ROUND_END, MATCH_END }

  /** kyoku는 동1국부터 동4국까지의 1 기반 국 번호다. */
  public record Round(int kyoku, int honba) {}

  public record Decision(long revision, EngineDecisionKind kind, List<Integer> actions) {
    public Decision { actions = List.copyOf(actions); }
  }

  /** 정산 내역과 순위는 기점 친을 0번으로 하는 좌석 순서다. 순위는 1위부터 센다. */
  public record Settlement(
      Round round, EndKind outcome, AbortiveDraw abortive, List<Integer> winners, int loser,
      List<Integer> scores, List<Integer> changes, int deposits, List<Integer> ranks,
      Round nextRound) {
    public Settlement {
      winners = List.copyOf(winners);
      scores = List.copyOf(scores);
      changes = List.copyOf(changes);
      ranks = List.copyOf(ranks);
    }
  }

  private final Engine engine = new Engine(RULES);
  private final RuleWorkspace workspace = new RuleWorkspace(RULES);
  private final Function<Round, int[]> walls;
  private RoundStart start;
  private Phase phase;
  private long revision;
  private Settlement settlement;

  EastMatch(Function<Round, int[]> walls) {
    this.walls = walls;
    deal(RoundStart.first(RULES));
  }

  public Phase phase() { return phase; }

  public Round round() { return new Round(start.round() + 1, start.honba()); }

  public Settlement settlement() { return settlement; }

  /** 해당 좌석의 응답 차례가 아니면 null을 반환한다. */
  public Decision decision(int seat) {
    Objects.checkIndex(seat, RULES.seats());
    if (phase != Phase.DECISIONS) return null;
    Decisions decisions = engine.decisions();
    int slot = decisions.slotOf(seat);
    if (slot == -1) return null;
    var actions = IntStream.range(0, decisions.legalCount(slot))
        .map(index -> decisions.legal(slot, index)).boxed().toList();
    return new Decision(revision, decisions.kind(slot), actions);
  }

  /** 방에서 모은 해당 시점의 좌석별 행동 코드를 한 번에 반영한다. */
  public void commit(long expectedRevision, Map<Integer, Integer> actionsBySeat) {
    if (phase != Phase.DECISIONS || expectedRevision != revision) {
      throw new IllegalStateException("the decision window is no longer current");
    }
    Decisions pending = engine.decisions();
    if (actionsBySeat.size() != pending.size()) {
      throw new IllegalArgumentException("a choice is required for each responding seat");
    }
    int[] choices = new int[pending.size()];
    for (int slot = 0; slot < pending.size(); slot++) {
      int seat = pending.seat(slot);
      Integer action = actionsBySeat.get(seat);
      choices[slot] = action == null ? -1 : indexOf(pending, slot, action);
      if (choices[slot] == -1) throw new IllegalArgumentException("illegal action for seat " + seat);
    }
    accept(engine.commit(choices, workspace));
  }

  /** 방에서 국말 화면의 진행을 요청할 때만 다음 국을 배패한다. */
  public void nextRound(Round settledRound) {
    if (phase != Phase.ROUND_END || !settlement.round().equals(settledRound)) {
      throw new IllegalStateException("the round settlement is no longer current");
    }
    Round next = settlement.nextRound();
    var scores = settlement.scores().stream().mapToInt(Integer::intValue).toArray();
    deal(new RoundStart(RULES, next.kyoku() - 1, next.honba(), settlement.deposits(), scores));
  }

  // 엔진 뷰는 다음 상태 전이까지만 유효하다. 방에서 좌석 응답을 만들 때 즉시 읽는다.
  SeatView seatView(int seat) { return engine.view(seat); }

  long revision() { return revision; }

  int dealer() { return engine.handStart().dealer(); }

  Decisions decisions() {
    if (phase != Phase.DECISIONS) {
      throw new IllegalStateException("the match is not waiting for decisions");
    }
    return engine.decisions();
  }

  private void deal(RoundStart next) {
    start = next;
    settlement = null;
    engine.startRecordedRound(start, walls.apply(round()));
    accept(engine.advance(workspace));
  }

  private void accept(Boundary boundary) {
    revision++;
    if (boundary == Boundary.DECISIONS) {
      phase = Phase.DECISIONS;
      return;
    }
    var end = engine.roundEnd();
    settlement = settle(start, end.outcome(), end.next());
    phase = settlement.nextRound() == null ? Phase.MATCH_END : Phase.ROUND_END;
  }

  static Settlement settle(RoundStart start, RoundOutcome outcome, TableState next) {
    int[] scores = IntStream.range(0, RULES.seats()).map(next::score).toArray();
    int deposits = next.kyotaku();
    // 엔진의 반장 종료 대신 동4국 친이 넘어가면 끝낸다. 토비 정산은 엔진이 끝낸 상태를 받는다.
    boolean finished = next.gameOver() || next.round() >= RULES.seats();
    if (finished && deposits > 0) {
      Placement.awardDeposits(scores, deposits);
      deposits = 0;
    }
    int[] changes = IntStream.range(0, RULES.seats()).map(seat -> scores[seat] - start.score(seat)).toArray();
    int[] ranks = IntStream.range(0, RULES.seats()).map(seat -> Placement.rank(scores, seat) + 1).toArray();
    var winners = IntStream.range(0, outcome.winnerCount()).map(outcome::winner).boxed().toList();
    return new Settlement(
        new Round(start.round() + 1, start.honba()), outcome.kind(), outcome.abortive(), winners,
        outcome.loser(), boxed(scores), boxed(changes), deposits, boxed(ranks),
        finished ? null : new Round(next.round() + 1, next.honba()));
  }

  private static int indexOf(Decisions pending, int slot, int action) {
    for (int index = 0; index < pending.legalCount(slot); index++) {
      if (pending.legal(slot, index) == action) return index;
    }
    return -1;
  }

  private static List<Integer> boxed(int[] values) {
    return Arrays.stream(values).boxed().toList();
  }
}
