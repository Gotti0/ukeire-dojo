# ukeire-dojo

유효패 수만으로는 정답이 나오지 않는 장면을 AI로 채점하는 리치 마작 타패 트레이너(개인 학습용, 진행 중).

> **라이브 사이트:** https://ukeire-dojo.me

[![Visitors](https://ukeire-dojo.me/api/badge/visitors.svg)](https://ukeire-dojo.me)
[![Solved](https://ukeire-dojo.me/api/badge/solved.svg)](https://ukeire-dojo.me)

기존 패효율 트레이너는 샨텐과 유효패 수로만 정답을 정한다. 실전에서는 도라, 역, 점수 상황, 상대 리치와 울기 때문에 정답이 자주 달라진다. 이 프로젝트는 [Epsilon-Prism](https://github.com/KoutaChan/Epsilon) v760(가중치 MIT)의 자기대국으로 국면을 만들고, 타패마다 두 기준을 함께 계산해 문제은행에 담는다.

- 효율 기준: 샨텐과, 보이는 정보로 센 유효패 수
- AI 기준: 최선수 대비 순위 효용 손실

## 현재 상태 (2026-11-26)
- 문제은행: 374반장, 184,293문제(로컬 `data/bank.sqlite`, 저장소에는 넣지 않음).
- 효율 최선과 AI 최선이 갈리는 문제는 36.8%다. 그중 손실 0.01 이상인 것은 전체의 5.1%이고, 상대 리치가 있으면 16.7%로 올라간다.
- 트레이너 화면(T4): 문제를 풀면 효율 기준과 AI 기준으로 함께 채점하고, 버릴 수 있는 모든 패의 AI 손실, 샨텐, 유효패를 보여 준다.
- 오답 기록(T5): 상대 리치 대응, 울기 손, 문전 손의 풀이 수와 오답률을 보여 준다. 기본 출제 방식은 약점 우선이며 균등 출제로 바꿀 수 있다.
- 해설(T6): 채점 후 국면 정보와 전체 후보 타패 표를 담은 단일 완성형(모놀리식) 프롬프트를 원클릭 복사해 ChatGPT·Claude·Gemini 등 외부 AI에 붙여넣어 해설을 받을 수 있다.
- 계획과 결정 기록은 [docs/brainstorm](docs/brainstorm/2026-11-26-ai-efficiency-trainer.md)에 있다.

## 트레이너 실행
```powershell
cd web; npm install; npm run build; cd ..
python -m ukeire_dojo.server --db data/bank.sqlite   # http://127.0.0.1:8765
```
화면을 고치는 중이라면 서버를 띄운 채로 `cd web; npm run dev`를 실행한다(http://localhost:5173, `/api`는 8765로 프록시된다).

풀이 기록은 문제은행 옆 `practice.sqlite`에 저장한다. 경로를 바꾸려면 서버에 `--practice-db`를 넘긴다. 같은 주소와 브라우저 프로필에서 쿠키를 유지하면 서버를 다시 시작한 뒤에도 통계를 이어서 볼 수 있다.

## 지인에게 공개 (DDNS)
```powershell
.\scripts\serve-public.ps1   # 0.0.0.0:5173, 암호는 data\server-password.txt(처음 실행할 때 무작위로 만든다)
```
- 탐색기에서 `serve-public.bat`을 더블클릭해도 같다. 창을 닫거나 `Ctrl+C`를 누르면 서버가 멈춘다. 오류로 끝나면 창이 닫히지 않고 기다린다.
- 기존 화면 빌드가 있는 상태에서 업데이트했다면 `web`에서 `npm run build`를 실행한 뒤 서버를 시작한다.
- 이미 서버가 떠 있으면 두 번째 실행은 "이미 사용 중"으로 바로 실패한다.
- 외부 주소로 열 때는 서버가 암호(Basic 인증, 12자 이상) 없이는 시작하지 않는다.
- API 요청과 인증 실패에 IP별 속도 제한(초당 5회, 순간 30회), 요청 본문 4KB 제한, 소켓 시간 제한이 걸린다. 접속 기록은 `data\server-access.log`에 남는다.
- HTTP라서 암호가 평문으로 오간다. 다른 곳에 쓰는 암호를 쓰지 않는다. HTTPS가 필요하면 앞에 리버스 프록시를 둔다.
- 공유기 포트 포워딩(TCP 5173 → 이 PC)과 Windows 방화벽 인바운드 허용은 직접 설정한다.

## 구성
| 경로 | 내용 |
| --- | --- |
| `ukeire_dojo/epsilon.py` | Epsilon 리뷰어를 상주 자식 프로세스로 두고 패보를 분석하는 클라이언트(CPU v760 / GPU main) |
| `ukeire_dojo/positions.py` | 리뷰어 결과 문서에서 판단 직전 국면을 복원 |
| `ukeire_dojo/efficiency.py` | 샨텐·유효패 계산(Epsilon 샨텐 테이블과 무작위 손패 2만 개 대조, 불일치 0) |
| `ukeire_dojo/bank.py` | 문제 추출과 SQLite 문제은행 |
| `ukeire_dojo/server.py` | 문제 출제·채점 HTTP 서버(표준 라이브러리) |
| `ukeire_dojo/practice.py` | 브라우저별 SQLite 풀이 기록과 약점 출제 가중치 |
| `web/` | 트레이너 화면(Vite + React)과 AI 해설 프롬프트 생성기. 패 이미지는 Riichi-Trainer의 CC BY 4.0 에셋 |
| `tools/batch-review` | 여러 반장을 한 GPU 배치로 분석하는 Java 도구(초당 7,681판단) |
| `tools/selfplay` | v760 릴리스 jar용 자기대국 mjai 기록기(CPU) |
| `tools/shanten-oracle` | 샨텐 계산 검증용 정답지 |
| `scripts/` | JDK 사본, vendor 받기, Epsilon main CUDA 빌드 |
| `spike/` | Epsilon 연동 실험 기록 |

## 준비 (Windows, NVIDIA GPU는 선택)
```powershell
.\scripts\setup-jdk.ps1        # 시스템 JDK를 복사해 VC++ 런타임을 바꾼다(libtorch 초기화 문제)
.\scripts\fetch-vendor.ps1     # Epsilon 소스와 v760 릴리스(체크섬 확인), v760 리뷰어 컴파일
.\scripts\build-epsilon-cuda.ps1   # 선택: GPU. CUDA 12.8, VS 2022 Build Tools, gh 토큰의 read:packages 필요
python -m pytest -m "not gpu"
```

## 라이선스
GPL-3.0. 도구들이 Epsilon(GPL-3.0)의 클래스를 직접 쓴다. Epsilon 코드와 모델 가중치는 저장소에 넣지 않으며, 각각 원래 라이선스(코드 GPL-3.0, 가중치 MIT)를 따른다. Epsilon 저자는 온라인 대국, 대회, 도박에 쓰지 말고 AI의 타패를 사람의 플레이처럼 내세우지 말라고 요청했다. 이 프로젝트는 그 요청을 따른다.
