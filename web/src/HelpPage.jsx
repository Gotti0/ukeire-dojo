import { useEffect, useRef } from "react";
import "./help.css";

export default function HelpPage({ onNavigate }) {
  const heading = useRef(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  return (
    <main className="help-page" aria-labelledby="help-title">
      <div className="help-intro">
        <h2 id="help-title" tabIndex={-1} ref={heading}>도움말</h2>
        <p>한 장을 버리고 판단을 비교하거나, AI와 친구를 상대로 동풍전을 플레이하세요.</p>
        <div className="help-actions">
          <button type="button" className="tab active" onClick={() => onNavigate("trainer")}>패효율 연습 시작</button>
          <button type="button" className="tab" onClick={() => onNavigate("multiplayer")}>동풍전으로 이동</button>
        </div>
      </div>

      <div className="help-guides">
        <section aria-labelledby="help-trainer">
          <h3 id="help-trainer">패효율 트레이너</h3>
          <ol>
            <li>손패에서 버릴 패를 고릅니다.</li>
            <li>결과에서 내 선택, AI 최선, 효율 최선과 후보별 샨텐·유효패를 비교합니다.</li>
            <li><strong>다음 문제</strong> 또는 Enter를 눌러 계속합니다.</li>
          </ol>
          <p><strong>약점 우선</strong>은 자주 틀리는 유형의 출제 비중을 높입니다. 기록은 <strong>내 유형별 풀이 통계</strong>에서 확인할 수 있습니다.</p>
        </section>

        <section aria-labelledby="help-solo">
          <h3 id="help-solo">혼자 동풍전</h3>
          <ol>
            <li><strong>동풍전 멀티플레이</strong>에서 이름을 입력하고 <strong>새 방 만들기</strong>를 누릅니다.</li>
            <li><strong>동풍전 시작</strong>을 누르면 AI 3명과 한 경기를 치릅니다.</li>
            <li>첫 타패 차례부터 <strong>AI 타패 추천 보기</strong>를 누를 수 있습니다.</li>
          </ol>
          <p>한 국이 끝나면 <strong>다음 국 진행</strong>을 누릅니다. 경기 종료 후 최종 점수와 순위가 표시됩니다.</p>
        </section>

        <section aria-labelledby="help-multiplayer">
          <h3 id="help-multiplayer">친구와 동풍전</h3>
          <ol>
            <li>초대 링크나 코드를 공유해 2~4명이 모입니다. 빈 좌석은 AI가 맡습니다.</li>
            <li>전원이 <strong>준비 완료</strong>를 누르면 방장이 시작합니다. 매 국 정산 후 참가자들이 <strong>다음 국 진행</strong>을 누릅니다.</li>
            <li>1경기 후 방장이 같은 패산으로 2경기를 시작합니다. 2경기부터 추천을 요청할 수 있고, 두 경기가 끝나면 점수와 순위를 비교합니다.</li>
          </ol>
        </section>
      </div>

      <section className="help-questions" aria-labelledby="help-questions">
        <h3 id="help-questions">자주 묻는 질문</h3>
        <details>
          <summary>AI 손실과 유효패는 어떻게 읽나요?</summary>
          <p>AI 손실은 AI가 평가한 최선 타패와 해당 후보의 순위 효용 차이입니다. 0에 가까울수록 평가가 비슷합니다. 유효패는 샨텐을 줄이는 패 중 내 손패와 공개된 패를 제외한 장수입니다.</p>
        </details>
        <details>
          <summary>AI 해설 프롬프트는 어디에서 복사하나요?</summary>
          <p>트레이너 채점 결과나 동풍전 추천 패널에서 <strong>AI 해설 프롬프트 복사</strong>를 누른 뒤, ChatGPT 등 외부 AI에 붙여넣으면 됩니다.</p>
        </details>
        <details>
          <summary>같은 패산인데 왜 쯔모가 달라지나요?</summary>
          <p>두 경기에서 같은 국·본장의 배패와 패산 순서는 같습니다. 울기나 깡이 달라지면 어느 좌석이 어느 패를 가져가는지도 달라질 수 있습니다.</p>
        </details>
        <details>
          <summary>대국 중 연결이 끊기면 어떻게 하나요?</summary>
          <p>원래 탭에서 새로고침하면 같은 좌석으로 돌아갈 수 있습니다. 연결이 끊긴 채 방에 표시된 유예 시간(기본 15초)이 지나면 AI가 대신 두고, 복귀 후 다음 선택부터 직접 플레이할 수 있습니다.</p>
        </details>
      </section>
    </main>
  );
}
