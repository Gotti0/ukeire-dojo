package ukeire_dojo.oracle;

import com.epsilon.calculate.table.ShantenTable;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;

/**
 * Python 샨텐 계산기를 검증하는 정답지. 한 줄에 "34개 종류별 장수 + 목표 면자 수"를 받아 Epsilon(Nyanten 계열 테이블)의 일반형 샨텐을
 * 한 줄로 돌려준다.
 */
public final class ShantenOracle {
  private ShantenOracle() {}

  public static void main(String[] args) throws IOException {
    var in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
    var out = new PrintWriter(System.out, false, StandardCharsets.UTF_8);
    int[] counts = new int[34];
    for (String line = in.readLine(); line != null; line = in.readLine()) {
      String[] parts = line.trim().split("\\s+");
      for (int i = 0; i < 34; i++) counts[i] = Integer.parseInt(parts[i]);
      int target = Integer.parseInt(parts[34]);
      out.println(
          ShantenTable.standardShanten(
              ShantenTable.suitKey(counts, 0),
              ShantenTable.suitKey(counts, 9),
              ShantenTable.suitKey(counts, 18),
              ShantenTable.honorKey(counts),
              target));
    }
    out.flush();
  }
}
