package ukeire_dojo.live;

import com.epsilon.mahjong.rules.Variant;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;
import java.util.random.RandomGenerator;
import java.util.stream.IntStream;

/** 한 방의 두 동풍전과 국·본장별 패산을 소유한다. 방의 작업 스레드에서만 호출한다. */
public final class EastSeries {
  private final Map<EastMatch.Round, int[]> walls = new HashMap<>();
  private final RandomGenerator random;
  private final EastMatch first;
  private EastMatch replay;

  public EastSeries() {
    this(new SecureRandom());
  }

  EastSeries(RandomGenerator random) {
    this.random = Objects.requireNonNull(random);
    first = new EastMatch(this::wall);
  }

  public EastMatch first() {
    return first;
  }

  /** 같은 요청이 반복돼도 이미 만들어 둔 두 번째 경기를 돌려준다. */
  public EastMatch startReplay() {
    if (first.phase() != EastMatch.Phase.MATCH_END) {
      throw new IllegalStateException("the first match has not ended");
    }
    if (replay == null) replay = new EastMatch(this::wall);
    return replay;
  }

  private int[] wall(EastMatch.Round round) {
    return walls.computeIfAbsent(round, ignored -> {
      var tiles = Variant.TENHOU_4P.tiles();
      int[] wall = IntStream.range(0, tiles.size()).map(tiles::physicalId).toArray();
      for (int i = wall.length - 1; i > 0; i--) {
        int j = random.nextInt(i + 1);
        int tile = wall[i];
        wall[i] = wall[j];
        wall[j] = tile;
      }
      return wall;
    });
  }
}
