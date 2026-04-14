from __future__ import annotations

from pathlib import Path

from zeno_client.chunking import ChunkingConfig, iter_chunks_in_range


def test_forced_cut_applies_when_within_window(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes((b"A" * 1024) + (b"B" * 1024) + (b"C" * 1024))

    cfg = ChunkingConfig(avg=1024, min=256, max=4096)
    chunks = list(
        iter_chunks_in_range(
            p,
            start=0,
            length=p.stat().st_size,
            cfg=cfg,
            forced_cuts={1024},
        )
    )
    assert chunks
    assert chunks[0].size == 1024


def test_forced_cut_deterministic(tmp_path: Path):
    p = tmp_path / "y.bin"
    p.write_bytes(b"0123456789" * 500)
    cfg = ChunkingConfig(avg=512, min=128, max=2048)

    c1 = list(iter_chunks_in_range(p, start=0, length=p.stat().st_size, cfg=cfg, forced_cuts={777}))
    c2 = list(iter_chunks_in_range(p, start=0, length=p.stat().st_size, cfg=cfg, forced_cuts={777}))
    assert [(c.offset, c.size, c.content_hash) for c in c1] == [(c.offset, c.size, c.content_hash) for c in c2]


def test_forced_cut_ignored_when_below_min_size(tmp_path: Path):
    p = tmp_path / "z.bin"
    p.write_bytes(b"A" * 2000)
    cfg = ChunkingConfig(avg=512, min=256, max=2048)
    chunks = list(iter_chunks_in_range(p, start=0, length=p.stat().st_size, cfg=cfg, forced_cuts={32}))
    assert chunks
    assert chunks[0].size >= cfg.min
