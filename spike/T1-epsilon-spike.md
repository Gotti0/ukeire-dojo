# T1 스파이크: Epsilon-Prism v760 연동 (2026-11-26)

## 결론
- Epsilon 리뷰어 백엔드를 stdio 자식 프로세스로 띄우면 판단마다 **전체 후보**의 가치 손실을 받을 수 있다. 화면에는 상위 3개만 나오지만 결과 문서에는 전부 들어 있다.
- 별도의 Java 브리지를 새로 만들 필요가 없다. 리뷰어의 stdio 호스트를 그대로 쓴다.
- 입력은 **처음부터 끝까지 합법적으로 재생되는 완전한 패보**(천봉 XML, mjai, 작혼)여야 한다. 국면 하나만 넣는 API는 없다. 로더가 반장 전체를 재생해 기록과 일치하는지 검사한다.

## 재현
```powershell
& .\scripts\setup-jdk.ps1
python -I spike\stdio_client.py spike\sample-double-ron-a.xml.gz
```
- 전제 1: `vendor\epsilon-release`에 v760 릴리스(zip을 풀어 둔 `epsilon-prism-v760\`와 jar)가 있어야 한다.
- 전제 2: `vendor\reviewer-v760\classes`에 컴파일된 리뷰어 백엔드가 있어야 한다. 만드는 방법은 아래 "환경 문제와 해결" 2번 항목에 있다.

## 측정 (CPU, RTX 4070 Ti 미사용)
- 엔진 시작부터 `models` 응답까지 0.7초 걸렸다.
- 반장 1개(13국, 판단 824개)를 분석하는 데 16.6초 걸렸다. 판단 하나에 약 20ms다.
- 판단 하나당 후보 수는 1~27개였다. 리치 선언 타패도 별도 후보로 나온다.
- 값은 최선수 대비 손실이고 최선수가 0이다. 단위는 순위 효용(1~4위 = 2/3, 1/3, 0, −1)이다. 처음에 "2, 1/3, 0, −1"로 잘못 적었다가 모델 zip의 README로 정정했다. 결과 문서의 `temperature`는 0.002다.

## 프로토콜 요점
- 프레임은 `[헤더 길이 u32 BE][UTF-8 JSON][본문 길이 u32 BE][본문]` 형식이다. 엔진은 시작하면 먼저 `{"event":"ready"}`를 보낸다.
- 흐름: `importBytes`(본문은 패보 바이트) → `startAnalysis {recordId, modelId, revision}` → `job {jobId}` 폴링 → `result {resultId}`. `result`의 본문은 gzip으로 압축한 결과 문서다.
- 후보 행은 `[actionId, 손실]`이다. actionId 배치: 타패 0..73, 치 74..154, 퐁 155..191, 대명깡 192..225, 안깡 226..259, 가깡 260..293, 리치 타패 294..367, 쯔모 368, 론 369, 패스 370, 구종구패 371.
- `scenario` 명령이 있어 손패·버림패·도라를 바꾼 가정 국면을 다시 분석할 수 있다. 아직 시험하지 않았다.

## 환경 문제와 해결
1. **libtorch 초기화 실패.** 증상은 `c10.dll: DLL 초기화 루틴을 실행할 수 없습니다`였다.
   - 원인: 시스템 JDK 23이 bin에 VC++ 런타임 14.36을 묶어 두는데, libtorch 2.11은 이 버전으로 초기화되지 않는다. 시스템 런타임은 14.51이다.
   - 해결: `scripts\setup-jdk.ps1`로 JDK 사본을 만들고 런타임 DLL 3개를 시스템 것으로 바꿨다.
   - CPU 강제: DJL은 GPU를 감지하면 cu128 판을 받는다. `PYTORCH_FLAVOR=cpu`로 CPU 판을 쓰게 한다.
2. **리뷰어 백엔드 Maven 빌드 불가.**
   - 원인: 저자가 포크한 DJL(`0.40.x-rocm-SNAPSHOT`)이 GitHub Packages에만 있어서 인증 없이는 401이 난다.
   - 해결: 저장소 `main` 대신 태그 `epsilon-prism-v760` 시점의 `epsilon-reviewer/backend/src/main`을 꺼냈다. 이걸 릴리스 fat jar를 classpath로 두고 javac로 컴파일했다(오류 0개).
   - 주의: `main`은 패키지 구조가 바뀌어 릴리스 jar와 맞지 않는다.
3. **엔진 시작 시 `Unable to establish loopback connection`.**
   - 원인: 8.3 단축 경로의 TEMP에서는 Unix 도메인 소켓을 만들 수 없다.
   - 해결: `-Djdk.net.unixdomain.tmpdir`로 소켓 폴더를 일반 경로로 옮겼다.

## T2 이후에 미치는 영향
- T2는 "Java 브리지 작성"이 아니라 "리뷰어 stdio 호스트를 감싸는 Python 클라이언트 정리"로 줄어든다.
- 문제은행(T3)에는 자기대국 **패보 파일**이 필요하다. 그런데 릴리스 jar에는 Prism 자기대국을 패보로 남기는 명령이 없다. `evaluate`는 통계만 낸다.
- v760 소스에는 `TenhouLogWriter`(GameRecorder 구현)가 있다. 그래서 Prism 4석 자기대국에 이 기록기를 붙이는 작은 Java 도구를 같은 방식(javac + 릴리스 jar)으로 만들어야 한다.

## 미확인
- `scenario`로 상대 리치·울기 같은 가정 국면을 만들 수 있는 범위.
- 같은 엔진 프로세스에 분석을 동시에 여러 개 넣었을 때의 처리량. 호스트는 요청 스레드를 8개 쓴다.
- 손실값을 사람이 읽을 수 있는 단위(예: 평균 순위 차)로 바꾸는 방법.
