"""엔진 프로세스 수별 분석 처리량을 잰다.

사용: python -I tools/bench_epsilon.py data/selfplay 1 2 4
      python -I tools/bench_epsilon.py data/gpu-test/out16 --gpu 1 4 8
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ukeire_dojo.epsilon import EngineConfig, EpsilonEngine  # noqa: E402


def run(records: list[bytes], processes: int, config: EngineConfig) -> None:
    started = time.perf_counter()
    engines = [EpsilonEngine(config) for _ in range(processes)]
    boot = time.perf_counter() - started
    try:
        # 엔진마다 몫을 나눠 스레드 하나가 엔진 하나를 맡는다. 엔진 안에서는 분석이 직렬이다.
        shares = [records[i::processes] for i in range(processes)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(processes) as pool:
            counts = list(pool.map(lambda pair: sum(len(pair[0].analyze(r).decisions) for r in pair[1]),
                                   zip(engines, shares)))
        elapsed = time.perf_counter() - t0
    finally:
        for engine in engines:
            engine.close()
    decisions = sum(counts)
    print(f"processes={processes} games={len(records)} boot={boot:.1f}s analyze={elapsed:.1f}s "
          f"decisions={decisions} decisions/s={decisions / elapsed:.0f} s/game={elapsed / len(records):.2f}")


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    args = sys.argv[2:]
    gpu = "--gpu" in args
    config = EngineConfig.gpu_main() if gpu else EngineConfig.cpu_v760()
    files = sorted(folder.glob("*.mjson")) + sorted(folder.glob("*.xml.gz"))
    records = [p.read_bytes() for p in files][:16]
    print(f"engine={'gpu_main' if gpu else 'cpu_v760'}")
    for n in (int(a) for a in args if a != "--gpu"):
        run(records, n, config)
