package ukeire_dojo.selfplay;

import com.epsilon.engine.GameRecorder;
import com.epsilon.engine.RoundResult;
import com.epsilon.engine.WinClaim;
import com.epsilon.replay.TileNotation;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;

/**
 * 엔진의 GameRecorder 콜백을 mjai JSONL로 옮긴다. v760의 패보 리더는 천봉 XML·mjai·작혼만 읽고, 엔진에 들어 있는
 * TenhouLogWriter의 tensoul JSON은 읽지 못하기 때문에 따로 만든다.
 *
 * <p>hora·ryukyoku·end_game의 점수 변동은 쓰지 않는다. 리더에서 선택 필드이고, 재생 엔진이 직접 다시 계산하므로 우리가 계산한
 * 값과 어긋나 패보가 거부될 위험만 생긴다.
 */
final class MjaiRecorder implements GameRecorder {
  private static final String HONORS = "ESWNPFC";

  private final List<String> lines = new ArrayList<>();

  // start_kyoku는 배패와 도라 표시패가 모두 와야 쓸 수 있는데, 엔진이 이 셋을 부르는 순서를 계약으로 보장하지 않아 모았다가 쓴다.
  private JsonObject pendingStart;
  private final String[][] haipai = new String[4][];
  private String doraMarker;

  MjaiRecorder(String[] names) {
    JsonObject start = event("start_game");
    JsonArray nameArray = new JsonArray();
    for (String name : names) nameArray.add(name);
    start.add("names", nameArray);
    emit(start);
  }

  List<String> finish(int[] finalScores) {
    JsonObject end = event("end_game");
    end.add("scores", ints(finalScores));
    emit(end);
    return lines;
  }

  @Override
  public void startRound(int roundIndex, int honba, int kyotakuCount, int[] startingScores) {
    pendingStart = event("start_kyoku");
    pendingStart.addProperty("bakaze", String.valueOf(HONORS.charAt(roundIndex / 4)));
    pendingStart.addProperty("kyoku", roundIndex % 4 + 1);
    pendingStart.addProperty("honba", honba);
    pendingStart.addProperty("kyotaku", kyotakuCount);
    pendingStart.addProperty("oya", roundIndex % 4);
    pendingStart.add("scores", ints(startingScores));
    for (int seat = 0; seat < 4; seat++) haipai[seat] = null;
    doraMarker = null;
  }

  @Override
  public void recordHaipai(int player, int[] concealedTileCounts, int concealedAkaMask) {
    List<String> tiles = new ArrayList<>(13);
    for (int type = 0; type < concealedTileCounts.length; type++) {
      for (int copy = 0; copy < concealedTileCounts[type]; copy++) {
        boolean aka = copy == 0 && isFive(type) && (concealedAkaMask & akaBit(type)) != 0;
        tiles.add(tile(type, aka));
      }
    }
    haipai[player] = tiles.toArray(String[]::new);
  }

  @Override
  public void recordDoraIndicators(int[] physicalTileIds) {
    doraMarker = TileNotation.formatPhysicalTile(physicalTileIds[0]);
  }

  @Override
  public void recordNewDora(int physicalTileId) {
    flushStart();
    JsonObject dora = event("dora");
    dora.addProperty("dora_marker", TileNotation.formatPhysicalTile(physicalTileId));
    emit(dora);
  }

  @Override
  public void recordDraw(int player, int tileType, boolean isAkaTile) {
    flushStart();
    JsonObject tsumo = actorEvent("tsumo", player);
    tsumo.addProperty("pai", tile(tileType, isAkaTile));
    emit(tsumo);
  }

  @Override
  public void recordDahai(
      int player, int tileType, boolean isAkaTile, boolean tsumogiri, boolean riichi) {
    flushStart();
    if (riichi) emit(actorEvent("reach", player));
    JsonObject dahai = actorEvent("dahai", player);
    dahai.addProperty("pai", tile(tileType, isAkaTile));
    dahai.addProperty("tsumogiri", tsumogiri);
    emit(dahai);
  }

  @Override
  public void recordRiichiAccepted(int player) {
    emit(actorEvent("reach_accepted", player));
  }

  @Override
  public void recordChi(
      int player,
      int[] chiTileTypes,
      int calledTileType,
      boolean calledTileIsAka,
      boolean consumedHandTileIsAka) {
    JsonArray consumed = new JsonArray();
    boolean skippedCalled = false;
    for (int type : chiTileTypes) {
      if (type == calledTileType && !skippedCalled) {
        skippedCalled = true;
        continue;
      }
      consumed.add(tile(type, consumedHandTileIsAka && isFive(type)));
    }
    // 상가에서만 치할 수 있다.
    emit(call("chi", player, (player + 3) % 4, tile(calledTileType, calledTileIsAka), consumed));
  }

  @Override
  public void recordPon(
      int player,
      int tileType,
      int sourcePlayerOffset,
      boolean calledTileIsAka,
      boolean consumedHandTileIsAka) {
    emit(
        call(
            "pon",
            player,
            (player + sourcePlayerOffset) % 4,
            tile(tileType, calledTileIsAka),
            sameTiles(tileType, 2, consumedHandTileIsAka)));
  }

  @Override
  public void recordDaiminkan(
      int player,
      int tileType,
      int sourcePlayerOffset,
      boolean calledTileIsAka,
      boolean consumedHandTileIsAka) {
    emit(
        call(
            "daiminkan",
            player,
            (player + sourcePlayerOffset) % 4,
            tile(tileType, calledTileIsAka),
            sameTiles(tileType, 3, consumedHandTileIsAka)));
  }

  @Override
  public void recordAnkan(int player, int tileType, boolean containsAkaTile) {
    JsonObject ankan = actorEvent("ankan", player);
    ankan.add("consumed", sameTiles(tileType, 4, containsAkaTile));
    emit(ankan);
  }

  @Override
  public void recordKakan(int player, int tileType, boolean addedTileIsAka) {
    JsonObject kakan = actorEvent("kakan", player);
    kakan.addProperty("pai", tile(tileType, addedTileIsAka));
    emit(kakan);
  }

  @Override
  public void recordRoundResult(RoundResult result) {
    switch (result) {
      case RoundResult.Winning winning -> {
        for (WinClaim claim : winning.claims()) {
          JsonObject hora = actorEvent("hora", claim.winner());
          hora.addProperty("target", claim.from());
          emit(hora);
        }
      }
      case RoundResult.ExhaustiveDraw draw -> {
        JsonObject ryukyoku = event("ryukyoku");
        ryukyoku.addProperty("reason", "fanpai");
        emit(ryukyoku);
      }
      case RoundResult.AbortiveDraw draw -> {
        JsonObject ryukyoku = event("ryukyoku");
        ryukyoku.addProperty(
            "reason",
            switch (draw.reason()) {
              case NINE_TERMINALS_AND_HONORS -> "kyushukyuhai";
              case TRIPLE_RON -> "sanchaho";
              case FOUR_WINDS -> "sufonrenta";
              case FOUR_RIICHI -> "suchareach";
              case FOUR_KANS -> "sukaikan";
            });
        emit(ryukyoku);
      }
    }
  }

  @Override
  public void endRound(GameRecorder.RoundEnd roundEnd) {
    flushStart();
    emit(event("end_kyoku"));
  }

  private void flushStart() {
    if (pendingStart == null) return;
    if (doraMarker == null) throw new IllegalStateException("start_kyoku without a dora marker");
    JsonArray tehais = new JsonArray();
    for (int seat = 0; seat < 4; seat++) {
      if (haipai[seat] == null || haipai[seat].length != 13)
        throw new IllegalStateException("start_kyoku without a 13-tile hand for seat " + seat);
      JsonArray hand = new JsonArray();
      for (String t : haipai[seat]) hand.add(t);
      tehais.add(hand);
    }
    pendingStart.addProperty("dora_marker", doraMarker);
    pendingStart.add("tehais", tehais);
    emit(pendingStart);
    pendingStart = null;
  }

  private static JsonObject call(
      String type, int actor, int target, String pai, JsonArray consumed) {
    JsonObject meld = actorEvent(type, actor);
    meld.addProperty("target", target);
    meld.addProperty("pai", pai);
    meld.add("consumed", consumed);
    return meld;
  }

  /** 같은 종류 n장. 5이고 적5를 포함하면 첫 장만 적5로 쓴다. 한 종류에 적5는 한 장뿐이다. */
  private static JsonArray sameTiles(int type, int count, boolean containsAka) {
    JsonArray tiles = new JsonArray();
    for (int i = 0; i < count; i++) tiles.add(tile(type, i == 0 && containsAka && isFive(type)));
    return tiles;
  }

  private static boolean isFive(int type) {
    return type < 27 && type % 9 == 4;
  }

  private static int akaBit(int type) {
    return 1 << (type / 9);
  }

  private static String tile(int type, boolean aka) {
    if (type >= 27) return String.valueOf(HONORS.charAt(type - 27));
    return (type % 9 + 1) + String.valueOf("mps".charAt(type / 9)) + (aka ? "r" : "");
  }

  private static JsonObject event(String type) {
    JsonObject object = new JsonObject();
    object.addProperty("type", type);
    return object;
  }

  private static JsonObject actorEvent(String type, int actor) {
    JsonObject object = event(type);
    object.addProperty("actor", actor);
    return object;
  }

  private static JsonArray ints(int[] values) {
    JsonArray array = new JsonArray();
    for (int v : values) array.add(v);
    return array;
  }

  private void emit(JsonObject object) {
    lines.add(object.toString());
  }
}
