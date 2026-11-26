"""패보 폴더를 Epsilon으로 분석해 문제은행(SQLite)에 넣는다. 이미 넣은 패보는 건너뛴다.

사용: python -I tools/build_bank.py data/selfplay data/bank.sqlite --processes 4
      python -I tools/build_bank.py --results data/gpu-test/review256 data/bank.sqlite --source-prefix gpu-
두 번째 형식은 tools/batch-review가 이미 쓴 결과 문서(*.result.json.gz)를 엔진 없이 읽는다.
"""

import argparse
import gzip
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ukeire_dojo.bank import connect, has_source, problems_from, store  # noqa: E402
from ukeire_dojo.epsilon import EngineError, EpsilonEngine, parse_document  # noqa: E402

RESULT_SUFFIX = ".result.json.gz"


def ingest_results(folder: Path, db_path: Path, prefix: str) -> None:
    db = connect(db_path)
    started = time.perf_counter()
    stored = 0
    for path in sorted(folder.glob(f"*{RESULT_SUFFIX}")):
        # 다른 생성기의 반장 ID가 겹칠 수 있어(예: CPU 자기대국 1001과 GPU 자기대국 1001) 접두사로 출처를 가른다.
        source = prefix + path.name[: -len(RESULT_SUFFIX)]
        if has_source(db, source):
            continue
        analysis = parse_document(json.loads(gzip.decompress(path.read_bytes())))
        store(db, source, analysis, problems_from(analysis, source))
        stored += 1
    db.close()
    print(f"stored={stored} seconds={time.perf_counter() - started:.0f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", type=Path, nargs="?")
    parser.add_argument("db", type=Path)
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--results", type=Path, help="tools/batch-review 결과 문서 폴더")
    parser.add_argument("--source-prefix", default="")
    args = parser.parse_args()
    if args.results is not None:
        ingest_results(args.results, args.db, args.source_prefix)
        return
    if args.records is None:
        parser.error("records folder or --results is required")

    db = connect(args.db)
    files = [p for p in sorted(args.records.glob("*.mjson")) if not has_source(db, p.stem)]
    print(f"pending records: {len(files)}")
    if not files:
        return
    failures_path = args.db.with_suffix(".failures.txt")
    db_lock = threading.Lock()
    done = 0
    failures: list[str] = []
    started = time.perf_counter()

    def work(share: list[Path]) -> None:
        nonlocal done
        with EpsilonEngine() as engine:
            for path in share:
                try:
                    analysis = engine.analyze(path.read_bytes())
                except EngineError as e:
                    # 재생 실패는 기록기(MjaiRecorder)가 아직 못 본 이벤트를 잘못 썼다는 신호라 모아 둔다.
                    with db_lock:
                        failures.append(f"{path.name}\t{e}")
                    continue
                problems = problems_from(analysis, path.stem)
                with db_lock:
                    if has_source(db, path.stem):
                        continue
                    store(db, path.stem, analysis, problems)
                    done += 1
                    print(f"[{done}/{len(files)}] {path.name}: problems={len(problems)} "
                          f"elapsed={time.perf_counter() - started:.0f}s", flush=True)

    # sqlite 연결 하나를 스레드끼리 나눠 쓰므로 쓰기는 db_lock 안에서만 한다.
    shares = [files[i :: args.processes] for i in range(args.processes)]
    with ThreadPoolExecutor(args.processes) as pool:
        for future in [pool.submit(work, s) for s in shares if s]:
            future.result()
    db.close()
    if failures:
        failures_path.write_text("\n".join(failures) + "\n", encoding="utf-8")
    print(f"stored={done} failed={len(failures)} seconds={time.perf_counter() - started:.0f}"
          + (f" failures={failures_path}" if failures else ""))


if __name__ == "__main__":
    main()
