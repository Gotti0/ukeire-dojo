# selfplay-mjai

Epsilon-Prism v760 4석 자기대국을 반장마다 mjai 패보(`<반장ID>.mjson`)로 쓴다. 결과물은 Epsilon 리뷰어의 재생 검증을 통과하는 완전한 패보다.

## 빌드
`scripts\setup-jdk.ps1`로 `vendor\jdk`를 먼저 만들어 둔다. 그 다음 아래를 실행한다.

```powershell
$jar = "vendor\epsilon-release\epsilon-prism-1.0-SNAPSHOT-all.jar"
vendor\jdk\bin\javac.exe --release 21 -encoding UTF-8 -cp $jar -d tools\selfplay\classes tools\selfplay\src\ukeire_dojo\selfplay\*.java
```

## 실행
`data\selfplay-run`에서 실행한다. 이 폴더에 `config\epsilon-prism.toml`과 `logs\`가 생긴다.

```powershell
$env:PYTORCH_FLAVOR = "cpu"
$rel = "..\..\vendor\epsilon-release"
..\..\vendor\jdk\bin\java.exe "-Djdk.net.unixdomain.tmpdir=$PWD\tmp" -cp "..\..\tools\selfplay\classes;$rel\epsilon-prism-1.0-SNAPSHOT-all.jar" ukeire_dojo.selfplay.SelfPlayMain selfplay-mjai --snapshot "$rel\epsilon-prism-v760" --out ..\selfplay --games 16 --first-game 101
```

- `--first-game`: 패산은 설정의 seed와 반장 ID로만 정해진다. 실행할 때마다 겹치지 않는 범위를 준다.
- `PYTORCH_FLAVOR=cpu`, `-Djdk.net.unixdomain.tmpdir`: 이유는 `spike\T1-epsilon-spike.md`의 "환경 문제와 해결"에 있다.

## 측정 (2026-11-26, CPU)
- 16반장을 만드는 데 183초 걸렸다. 반장 하나에 약 11.4초다.
- 판단 처리량은 초당 약 50회였다. 동시 대국 수를 늘려도 이 값은 그대로였다.
- 생성한 18반장 모두 리뷰어 `importBytes`의 재생 검증을 통과했다. 리뷰어가 재생해서 계산한 최종 점수는 `end_game`과 일치했다.
- 기록된 이벤트: 치, 퐁, 안깡, 가깡, 깡도라, 리치, 화료, 유국(황패), 적5.
- 아직 나오지 않은 이벤트: 더블 론, 도중 유국(구종구패 등), 대명깡. 이 경로의 기록은 검증되지 않았다.

## 설계 메모
- v760의 패보 리더는 엔진에 있는 `TenhouLogWriter`의 tensoul JSON을 읽지 못한다. 그래서 mjai 기록기를 따로 만들었다.
- `hora`, `ryukyoku`의 점수 변동(`deltas`)은 쓰지 않는다. 리더에서 선택 필드이고 재생 엔진이 다시 계산하므로, 써 봐야 어긋날 위험만 생긴다.
