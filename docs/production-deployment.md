# OCI 운영 배포

[GitHub Actions](https://github.com/Gotti0/ukeire-dojo/actions/workflows/ci.yml)는 `main` 대상 PR과 `main` 업데이트에서 Python·Java·프런트엔드 테스트와 빌드를 실행한다. GPU 테스트는 Windows GPU 환경에서 별도로 실행한다. CI의 대국 통합 테스트는 스크립트 봇을 쓰고, 패보 분석 테스트는 릴리스 v760 CPU 엔진을 쓴다.

`main` 검증을 통과하면 Actions가 해당 리비전을 부모로 두는 프런트엔드 전용 커밋을 `production-build` 브랜치에 게시한다. OCI 서버는 타이머로 확인한 뒤 그 리비전의 Python·Java 소스와 프런트엔드 빌드를 묶어 새 릴리스를 만든다. 데이터베이스, Prism 모델, 네이티브 라이브러리와 Caddy 설정은 기존 경로를 사용한다. Actions는 공개 HTTPS의 `/build-revision.txt`에서 해당 리비전이 응답하는지 확인한다.

## 기존 OCI 서버에 설치

`scripts/production/`를 OCI 서버로 복사하고 아래를 실행한다. 이 스크립트는 `/opt/ukeire-dojo`에 설치된 `ubuntu` 계정의 `ukeire-dojo.service`와 포트 `8765`를 사용한다. 실행 중인 서비스에 재시작을 요청하는 시점은 검증된 새 릴리스가 도착했을 때다. 대국 방은 메모리에 있으므로 이 재시작에서 현재 방이 종료된다.

```bash
sudo bash scripts/production/install.sh
systemctl list-timers ukeire-dojo-deploy.timer
journalctl -u ukeire-dojo-deploy.service -n 50 --no-pager
curl -fsS https://ukeire-dojo.me/build-revision.txt
```

서버는 Java 컴파일을 먼저 마치고 `current` 링크를 교체한다. 재시작 후 로컬 통계 API나 리비전 확인이 실패하면 이전 릴리스로 복귀하고 `/opt/ukeire-dojo/releases/<SHA>.failed`를 기록한다. 실패 이유를 해결한 뒤 같은 빌드를 다시 적용하려면 해당 표시 파일을 제거하고 `sudo systemctl start ukeire-dojo-deploy.service`를 실행한다. 자동 배포를 일시 중지하려면 `sudo systemctl stop ukeire-dojo-deploy.timer`를 실행한다.
