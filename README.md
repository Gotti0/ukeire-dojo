# ukeire-dojo

유효패 수만으로는 정답이 나오지 않는 장면을 AI로 채점하는 리치 마작 타패 트레이너(개인 학습용, 진행 중).

기존 패효율 트레이너는 샨텐과 유효패 수로만 정답을 정한다. 실전에서는 도라, 역, 점수 상황, 상대 리치와 울기 때문에 정답이 자주 달라진다. 이 프로젝트는 [Epsilon-Prism](https://github.com/KoutaChan/Epsilon) v760(가중치 MIT)의 자기대국으로 국면을 만들고, 타패마다 두 기준을 함께 계산해 문제은행에 담는다.

- 효율 기준: 샨텐과, 보이는 정보로 센 유효패 수
- AI 기준: 최선수 대비 순위 효용 손실

## 현재 상태 (2026-11-26)
- 문제은행: 374반장, 184,293문제(로컬 `data/bank.sqlite`, 저장소에는 넣지 않음).
- 효율 최선과 AI 최선이 갈리는 문제는 36.8%다. 그중 손실 0.01 이상인 것은 전체의 5.1%이고, 상대 리치가 있으면 16.7%로 올라간다.
- 트레이너 화면(T4): 문제를 풀면 효율 기준과 AI 기준으로 함께 채점하고, 버릴 수 있는 모든 패의 AI 손실, 샨텐, 유효패를 보여 준다. 오답 기록(T5)과 해설(T6)은 아직 없다.
- 계획과 결정 기록은 [docs/brainstorm](docs/brainstorm/2026-11-26-ai-efficiency-trainer.md)에 있다.

## 트레이너 실행
```powershell
cd web; npm install; npm run build; cd ..
python -m ukeire_dojo.server --db data/bank.sqlite   # http://127.0.0.1:8765
```
화면을 고치는 중이라면 서버를 띄운 채로 `cd web; npm run dev`를 실행한다(http://localhost:5173, `/api`는 8765로 프록시된다).

## 구성
| 경로 | 내용 |
| --- | --- |
| `ukeire_dojo/epsilon.py` | Epsilon 리뷰어를 상주 자식 프로세스로 두고 패보를 분석하는 클라이언트(CPU v760 / GPU main) |
| `ukeire_dojo/positions.py` | 리뷰어 결과 문서에서 판단 직전 국면을 복원 |
| `ukeire_dojo/efficiency.py` | 샨텐·유효패 계산(Epsilon 샨텐 테이블과 무작위 손패 2만 개 대조, 불일치 0) |
| `ukeire_dojo/bank.py` | 문제 추출과 SQLite 문제은행 |
| `ukeire_dojo/server.py` | 문제 출제·채점 HTTP 서버(표준 라이브러리, localhost 전용) |
| `web/` | 트레이너 화면(Vite + React). 패 이미지는 Riichi-Trainer의 CC BY 4.0 에셋 |
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
