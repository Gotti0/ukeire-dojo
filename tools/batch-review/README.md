# batch-review

여러 패보를 Epsilon 리뷰어와 같은 방식으로 분석해, 패보마다 리뷰어 결과 문서(`<이름>.result.json.gz`)를 쓴다.

리뷰어는 한 반장의 판단을 하나씩 1행 배치로 평가해서 GPU가 놀았다. 이 도구는 리뷰어의 분석 루프와 문서 작성기를 그대로 쓰고, 반장마다 스레드를 하나씩 둔다. 스레드들이 동시에 낸 평가 요청은 모아서 한 배치로 네트워크에 보낸다.

## 빌드
먼저 `scripts\build-epsilon-cuda.ps1`로 main 리뷰어 jar를 만든다.

```powershell
$jar = "vendor\epsilon-src\epsilon-reviewer\backend\target\epsilon-reviewer-1.0-SNAPSHOT-all.jar"
vendor\jdk\bin\javac.exe --release 21 -encoding UTF-8 -cp $jar -d tools\batch-review\classes tools\batch-review\src\ukeire_dojo\review\BatchReview.java
```

## 실행
```powershell
$env:PATH = "$HOME\.djl.ai\pytorch\2.11.0-cu128-win-x86_64;$env:PATH"
$env:PYTORCH_FLAVOR = "cu128"
$jar = "vendor\epsilon-src\epsilon-reviewer\backend\target\epsilon-reviewer-1.0-SNAPSHOT-all.jar"
vendor\jdk\bin\java.exe "-Djdk.net.unixdomain.tmpdir=$PWD\data\tmp" -cp "tools\batch-review\classes;$jar" ukeire_dojo.review.BatchReview --checkpoint vendor\epsilon-release\epsilon-prism-v760 --out data\review --threads 256 data\records\*.xml.gz
python -I tools\build_bank.py --results data\review data\bank.sqlite --source-prefix gpu-
```

- `--threads`는 동시에 분석할 반장 수다. 배치 크기는 대략 이 값까지 커진다. 패보 수보다 크게 줘도 패보 수만큼만 쓴다.
- `--device cpu`로 주면 FP32 EAGER로 돈다. GPU에서는 BF16 NATIVE다.

## 측정 (2026-11-26, RTX 4070 Ti)
| 입력 | 스레드 | 배치 평균 | 시간 | 초당 판단 |
| --- | --- | --- | --- | --- |
| 16반장 | 16 | 9.2행 | 20.4초 | 543 |
| 256반장 | 64 | 50.7행 | 52.8초 | 3,137 |
| 256반장 | 256 | 141.0행 | 21.6초 | 7,681 |

- 같은 패보 3개(2,295판단)를 리뷰어 GPU 엔진으로 분석한 결과와 대조했다. 판단 순서, 후보, 손실값이 모두 일치했다(최대 차이 0).
- 판단 수에는 합법수가 하나뿐이라 네트워크를 부르지 않은 판단이 들어가지 않는다.
