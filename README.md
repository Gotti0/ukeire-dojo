# ukeire-dojo

유효패 수만으로는 정답이 나오지 않는 리치 마작 국면을 AI로 채점하고, 실시간 동풍전까지 플레이할 수 있는 개인 학습용 리치 마작 트레이너(진행 중).

> **라이브 사이트:** https://ukeire-dojo.me

- **패효율 트레이너:** 기존 패효율 트레이너는 샨텐과 유효패 수로만 정답을 정한다. 실전에서는 도라, 역, 점수 상황, 상대 리치와 울기 때문에 정답이 자주 달라진다. 이 프로젝트는 [Epsilon-Prism](https://github.com/KoutaChan/Epsilon) v760(가중치 MIT)의 자기대국으로 국면을 만들고, 효율 기준(샨텐·유효패)과 AI 기준(최선수 대비 순위 효용 손실)을 함께 계산해 약점 우선 출제와 모놀리식 해설 프롬프트 복사를 제공한다.
- **동풍전 멀티플레이:** 1\~4명의 사람과 AI([Epsilon-Prism](https://github.com/KoutaChan/Epsilon) v760)가 함께 치는 실시간 동풍전. 싱글플레이(1명 + AI 3명)는 한 경기만 치르며 첫 타패 차례부터 AI 추천을 요청할 수 있다. 사람 2\~4명은 같은 패산으로 두 경기를 치르며 2경기에서 AI 추천을 요청하고 최종 결과를 비교할 수 있다.

## 방문자 추이

<a href="https://ukeire-dojo.me">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://ukeire-dojo.me/api/stats/chart.svg?theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://ukeire-dojo.me/api/stats/chart.svg?theme=light" />
    <img alt="ukeire-dojo Visitor History" src="https://ukeire-dojo.me/api/stats/chart.svg" width="680" />
  </picture>
</a>

## 현재 상태 (2026-11-27)
- 문제은행: 374반장, 184,293문제(로컬 `data/bank.sqlite`, 저장소에는 넣지 않음).
- 효율 최선과 AI 최선이 갈리는 문제는 36.8%다. 그중 손실 0.01 이상인 것은 전체의 5.1%이고, 상대 리치가 있으면 16.7%로 올라간다.
- 트레이너 화면(T4): 문제를 풀면 효율 기준과 AI 기준으로 함께 채점하고, 버릴 수 있는 모든 패의 AI 손실, 샨텐, 유효패를 보여 준다.
- 오답 기록(T5): 상대 리치 대응, 울기 손, 문전 손의 풀이 수와 오답률을 보여 준다. 기본 출제 방식은 약점 우선이며 균등 출제로 바꿀 수 있다.
- 해설(T6): 채점 후 국면 정보와 전체 후보 타패 표를 담은 단일 완성형(모놀리식) 프롬프트를 원클릭 복사해 ChatGPT·Claude·Gemini 등 외부 AI에 붙여넣어 해설을 받을 수 있다.
- 동풍전 멀티플레이(T7~T11): 초대 코드 방 생성·입장·준비, 1~4인(솔로 1인+3AI 포함) 시작, SSE 실시간 동기화와 좌석 격리, 이탈 유예 후 AI 대타 전환·재접속 복귀, 싱글플레이와 멀티플레이 재대국을 지원한다. 타패 추천(`/hint`)은 싱글플레이와 멀티플레이 2경기에서 요청할 수 있다. Playwright 다중 브라우저 E2E 검증 완료([검증 보고서](tools/live-e2e/README.md)).
- 공개 운영 및 통계: `https://ukeire-dojo.me`에서 상시 운영 중이며(OCI ARM64 CPU + Caddy HTTPS, `--live-bot prism --live-device cpu`), 자가 호스팅 방문자·풀이 통계(`/api/stats`, `/api/stats/chart.svg`, `/api/badge/visitors.svg`, `/api/badge/solved.svg`)를 제공한다.
- 테스트 자동화 및 OCI 배포 흐름은 [운영 배포 문서](docs/production-deployment.md)에 정리했다.
- 설계와 결정 기록은 [AI 패효율 트레이너](docs/brainstorm/2026-11-26-ai-efficiency-trainer.md), [동풍전 멀티플레이](docs/brainstorm/2026-11-26-multiplayer-east-series.md)에 있다.

## 로컬 실행
```powershell
cd web; npm install; npm run build; cd ..
python -m ukeire_dojo.server --db data/bank.sqlite   # http://127.0.0.1:8765
```
화면을 고치는 중이라면 서버를 띄운 채로 `cd web; npm run dev`를 실행한다(http://localhost:5173, `/api`는 8765로 프록시된다).

주요 서버 옵션:
- `--practice-db`: 풀이·방문자 기록 SQLite 경로(기본값: 문제은행 옆 `practice.sqlite`). 같은 주소와 브라우저 프로필에서 쿠키를 유지하면 서버를 다시 시작한 뒤에도 통계를 이어서 볼 수 있다.
- `--live-bot prism|scripted` (기본 `prism`), `--live-device gpu0|cpu` (기본 `gpu0`), `--disconnect-grace` (기본 `15.0`초): GPU·체크포인트 없이 UI나 방 흐름만 빠르게 시험할 때는 `--live-bot scripted`를 쓸 수 있고, CPU 환경에서는 `--live-device cpu`를 지정한다(JDK는 `vendor/jdk` → `JAVA_HOME` → `PATH` 순으로 자동 탐색한다).

## 구성
| 경로 | 내용 |
| --- | --- |
| `ukeire_dojo/epsilon.py` | Epsilon 리뷰어를 상주 자식 프로세스로 두고 패보를 분석하는 클라이언트(CPU v760 / GPU main) |
| `ukeire_dojo/positions.py` | 리뷰어 결과 문서에서 판단 직전 국면을 복원 |
| `ukeire_dojo/efficiency.py` | 샨텐·유효패 계산(Epsilon 샨텐 테이블과 무작위 손패 2만 개 대조, 불일치 0) |
| `ukeire_dojo/bank.py` | 문제 추출과 SQLite 문제은행 |
| `ukeire_dojo/practice.py` | 브라우저별 SQLite 풀이 기록, 약점 출제 가중치, 일별 누적 방문자·풀이 집계 |
| `ukeire_dojo/rooms.py` | 실시간 멀티플레이 동풍전 방 관리(초대·좌석 격리·SSE·이탈 유예/AI 대타·재대국·힌트)와 Java 대국 호스트 클라이언트 |
| `ukeire_dojo/chart.py` | GitHub README용 손그림(Star History / xkcd 스타일) 누적 추이 SVG 차트 생성기 |
| `ukeire_dojo/server.py` | 문제 출제·채점, 멀티플레이 방 API/SSE, 통계·SVG 차트/배지 HTTP 서버(표준 라이브러리) |
| `web/` | 트레이너·동풍전 멀티플레이 화면(Vite + React)과 AI 해설 프롬프트 생성기. 패 이미지는 Riichi-Trainer의 CC BY 4.0 에셋 |
| `tools/live-match/` | 동풍전 엔진(`EastMatch`), 동일 패산 2경기 관리(`EastSeries`), stdio JSON-RPC 호스트(`LiveMatchHost`) 및 Java 테스트 |
| `tools/live-e2e/` | Playwright Chromium 다중 브라우저 종합 검증 및 응답 시간 실측(`run.ps1`, `verify.mjs`) |
| `tools/live-spike/` | 실시간 대국 엔진 연동 사전 실험(`LiveSpike.java`) |
| `tools/batch-review/` | 여러 반장을 한 GPU 배치로 분석하는 Java 도구(초당 7,681판단) |
| `tools/selfplay/` | v760 릴리스 jar용 자기대국 mjai 기록기(CPU) |
| `tools/shanten-oracle/` | 샨텐 계산 검증용 정답지 |
| `tools/*.py` | 문제은행 생성(`build_bank.py`), 통계(`bank_stats.py`), 벤치마크(`bench_epsilon.py`), 샨텐 검증(`check_shanten.py`) 스크립트 |
| `scripts/` | JDK 사본, vendor 받기, Epsilon main CUDA 빌드, DDNS 공개 실행(`serve-public.ps1`) |
| `spike/` | 초기 Epsilon 연동 실험 기록 |

## 준비 및 검증
```powershell
.\scripts\setup-jdk.ps1            # Windows 선택: 시스템 JDK를 복사해 VC++ 런타임을 바꾼다(libtorch 초기화 문제)
.\scripts\fetch-vendor.ps1         # Epsilon 소스와 v760 릴리스(체크섬 확인), v760 리뷰어 컴파일
.\scripts\build-epsilon-cuda.ps1   # 선택: GPU. CUDA 12.8, VS 2022 Build Tools, gh 토큰의 read:packages 필요
python -m pytest -m "not gpu"      # Python 단위·서버·방·차트 테스트
node --test web/src/prompt.test.js # 프런트엔드 해설 프롬프트 테스트
.\tools\live-match\run-tests.ps1   # 선택: Java 동풍전 엔진 테스트
.\tools\live-e2e\run.ps1           # 선택: 다중 브라우저 E2E 검증
```

## 라이선스
GPL-3.0. 도구들이 Epsilon(GPL-3.0)의 클래스를 직접 쓴다. Epsilon 코드와 모델 가중치는 저장소에 넣지 않으며, 각각 원래 라이선스(코드 GPL-3.0, 가중치 MIT)를 따른다. Epsilon 저자는 온라인 대국, 대회, 도박에 쓰지 말고 AI의 타패를 사람의 플레이처럼 내세우지 말라고 요청했다. 이 프로젝트는 그 요청을 따른다.
