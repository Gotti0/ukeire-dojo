"""ukeire_dojo.efficiency.standard_shanten을 Epsilon 샨텐 테이블(tools/shanten-oracle)과 무작위 손패로 대조한다.

사용: python -I tools/check_shanten.py [손패 수]
"""

import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ukeire_dojo.efficiency import standard_shanten  # noqa: E402


def random_hand(rng: random.Random, melds: int, extra: int) -> list[int]:
    """울기 melds개를 뺀 3(4−melds)+extra장. 같은 종류 4장까지. 한 색에 몰린 손도 섞는다."""
    size = 3 * (4 - melds) + extra
    pool = [t for t in range(34) for _ in range(4)]
    if rng.random() < 0.3:
        suit = rng.randrange(4)
        pool = [t for t in pool if t // 9 == suit or (rng.random() < 0.2)]
    counts = [0] * 34
    for t in rng.sample(pool, size):
        counts[t] += 1
    return counts


def main(n: int) -> None:
    rng = random.Random(20261126)
    cases = []
    for _ in range(n):
        melds = rng.choice((0, 0, 0, 1, 2, 3, 4))
        cases.append((random_hand(rng, melds, rng.choice((1, 2))), 4 - melds))
    stdin = "".join(" ".join(map(str, c)) + f" {t}\n" for c, t in cases)
    jar = ROOT / "vendor" / "epsilon-release" / "epsilon-prism-1.0-SNAPSHOT-all.jar"
    classpath = f"{ROOT / 'tools' / 'shanten-oracle' / 'classes'};{jar}"
    out = subprocess.run(
        [str(ROOT / "vendor" / "jdk" / "bin" / "java.exe"), "-cp", classpath, "ukeire_dojo.oracle.ShantenOracle"],
        input=stdin, capture_output=True, text=True, check=True,
    ).stdout.split()
    if len(out) != n:
        raise SystemExit(f"oracle returned {len(out)} answers for {n} cases")
    mismatches = [(c, t, int(o), standard_shanten(c, t)) for (c, t), o in zip(cases, out) if int(o) != standard_shanten(c, t)]
    print(f"cases={n} mismatches={len(mismatches)}")
    for c, t, oracle, mine in mismatches[:10]:
        print(f"  target={t} oracle={oracle} mine={mine} counts={c}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20000)
