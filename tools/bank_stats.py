"""문제은행의 효율/AI 불일치 비율과 손실 분포를 요약한다. 사용: python -I tools/bank_stats.py data/bank.sqlite"""

import sqlite3
import sys

BUCKETS = (0.0, 0.002, 0.005, 0.01, 0.02, 0.05, float("inf"))


def main(path: str) -> None:
    # python -I는 PYTHONIOENCODING을 무시하므로, Windows 콘솔(cp949)에서 한글이 깨지지 않게 직접 정한다.
    sys.stdout.reconfigure(encoding="utf-8")
    db = sqlite3.connect(path)
    games, rounds, decisions = db.execute("SELECT COUNT(*), SUM(rounds), SUM(decisions) FROM sources").fetchone()
    total, disagree = db.execute("SELECT COUNT(*), SUM(disagree) FROM problems").fetchone()
    print(f"games={games} rounds={rounds} decisions={decisions} problems={total} "
          f"disagree={disagree} ({disagree / total:.1%})")

    print("\n불일치 문제의 eff_loss 분포 (효율 최선을 골랐을 때 AI가 매기는 손실)")
    for low, high in zip(BUCKETS, BUCKETS[1:]):
        n = db.execute("SELECT COUNT(*) FROM problems WHERE disagree=1 AND eff_loss>=? AND eff_loss<?",
                       (low, high)).fetchone()[0]
        print(f"  [{low:.3f}, {high:.3f}): {n:5d} ({n / disagree:.1%})")

    print("\n상황별 불일치 비율 / 손실≥0.01 비율")
    for label, where in (
        ("전체", "1=1"),
        ("문전", "menzen=1"),
        ("울기 있음", "menzen=0"),
        ("상대 리치 있음", "opp_riichi=1"),
        ("상대 리치 없음", "opp_riichi=0"),
        ("텐파이 가능(효율 샨텐 0)", "shanten=0"),
        ("1샨텐 이상", "shanten>=1"),
        ("AI 최선이 리치", "ai_best_riichi=1"),
    ):
        n, d, big = db.execute(
            f"SELECT COUNT(*), SUM(disagree), SUM(disagree AND eff_loss>=0.01) FROM problems WHERE {where}"
        ).fetchone()
        print(f"  {label:24s} n={n:6d} 불일치={d / n:6.1%} 손실≥0.01={big / n:6.1%}")


if __name__ == "__main__":
    main(sys.argv[1])
