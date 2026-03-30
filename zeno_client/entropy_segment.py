from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal


SegmentMode = Literal["low", "high", "mid"]


@dataclass(frozen=True)
class EntropyConfig:
    window_size: int = 16 * 1024
    low_threshold: float = 4.9
    high_threshold: float = 7.2


@dataclass(frozen=True)
class EntropySegment:
    start: int
    end: int
    mode: SegmentMode
    entropy: float


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = float(len(data))
    h = 0.0
    for c in counts:
        if c == 0:
            continue
        p = c / n
        h -= p * math.log2(p)
    return h


def classify_entropy(h: float, cfg: EntropyConfig) -> SegmentMode:
    if h <= cfg.low_threshold:
        return "low"
    if h >= cfg.high_threshold:
        return "high"
    return "mid"


def scan_entropy_segments(path: str | Path, cfg: EntropyConfig | None = None) -> list[EntropySegment]:
    cfg = cfg or EntropyConfig()
    p = Path(path)
    segments: list[EntropySegment] = []
    with p.open("rb") as f:
        offset = 0
        while True:
            buf = f.read(cfg.window_size)
            if not buf:
                break
            h = shannon_entropy(buf)
            mode = classify_entropy(h, cfg)
            segments.append(EntropySegment(start=offset, end=offset + len(buf), mode=mode, entropy=h))
            offset += len(buf)
    return merge_adjacent_segments(segments)


def merge_adjacent_segments(segments: list[EntropySegment]) -> list[EntropySegment]:
    if not segments:
        return []
    out: list[EntropySegment] = [segments[0]]
    for seg in segments[1:]:
        prev = out[-1]
        if prev.mode == seg.mode and prev.end == seg.start:
            merged = EntropySegment(
                start=prev.start,
                end=seg.end,
                mode=prev.mode,
                entropy=(prev.entropy + seg.entropy) / 2.0,
            )
            out[-1] = merged
        else:
            out.append(seg)
    return out
