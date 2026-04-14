# USD Dedup Validation Notes

This note records a synthetic benchmark run for the USD canonicalization path.

## Setup

- Canonicalizer:
  - USDA: pass-through
  - USDC: pass-through
  - USDZ: deterministic extract + canonicalize inner USD + repack (`CHIMERA_USDZ_REPACK=1`)
- Chunking config used for this benchmark:
  - `avg=512`
  - `min=256`
  - `max=2048`

## Synthetic Benchmark Results

- USDC pair (`v2` adds a small tail payload)
  - `USDC_DEDUP_PCT=94.97`
  - `CHUNKS_V1=15`
  - `CHUNKS_V2=15`
  - `REUSED_BYTES=28672`
  - `SIZE_V2=30190`

- USDZ pair (`v2` changes only small texture tail bytes)
  - `USDZ_DEDUP_PCT=92.33`
  - `CHUNKS_V1=22`
  - `CHUNKS_V2=22`
  - `REUSED_BYTES=40960`
  - `SIZE_V2=44364`

## Recommendation

- Keep `CHIMERA_USDZ_REPACK` feature-flagged during initial rollout.
- Enable by default in dev/staging first, then promote to production after:
  - representative studio asset sampling, and
  - viewer-open QA checks (usdview/Blender) on repacked USDZ outputs.
