#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maya.canonicalize import canonicalize
from zeno_client.chunking import ChunkingConfig, chunk_file


def _chunks_for_bytes(data: bytes, cfg: ChunkingConfig) -> list:
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ma") as tmp:
        tmp.write(data)
        path = Path(tmp.name)
    try:
        return chunk_file(path, cfg=cfg)
    finally:
        path.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark Maya ASCII dedup between two versions.")
    ap.add_argument("--left", required=True, help="Path to older .ma")
    ap.add_argument("--right", required=True, help="Path to newer .ma")
    ap.add_argument("--avg", type=int, default=1024 * 1024)
    ap.add_argument("--min", dest="min_", type=int, default=256 * 1024)
    ap.add_argument("--max", type=int, default=4 * 1024 * 1024)
    ap.add_argument("--output", default="", help="Optional report path (.json)")
    args = ap.parse_args()

    left_p = Path(args.left)
    right_p = Path(args.right)
    left_raw = left_p.read_bytes()
    right_raw = right_p.read_bytes()

    left_canon = canonicalize(left_raw)
    right_canon = canonicalize(right_raw)

    cfg = ChunkingConfig(avg=args.avg, min=args.min_, max=args.max)
    left_chunks = _chunks_for_bytes(left_canon, cfg)
    right_chunks = _chunks_for_bytes(right_canon, cfg)

    h_left = {c.content_hash: c.size for c in left_chunks}
    h_right = {c.content_hash: c.size for c in right_chunks}
    reused = set(h_left).intersection(h_right)
    reused_bytes = sum(h_right[h] for h in reused)
    dedup_pct = (reused_bytes / max(1, len(right_canon))) * 100.0

    report = {
        "left": {"path": str(left_p), "raw_bytes": len(left_raw), "canonical_bytes": len(left_canon), "chunks": len(left_chunks)},
        "right": {"path": str(right_p), "raw_bytes": len(right_raw), "canonical_bytes": len(right_canon), "chunks": len(right_chunks)},
        "chunking": {"avg": cfg.avg, "min": cfg.min, "max": cfg.max},
        "reuse": {"reused_chunk_hashes": len(reused), "reused_bytes_in_right": reused_bytes, "dedup_pct_of_right": dedup_pct},
    }

    print(json.dumps(report, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
