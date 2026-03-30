from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ._hash import compute_content_hash
from .cache_exceptions import CacheCorruptError
from .cache_index import delete_entry, eviction_candidates, get_entry, init_db, total_size_bytes, touch, upsert_entry
from .cache_lock import file_lock
from .client import ZenoClient
from .manifest import try_parse_manifest

try:
    import zstandard as zstd
except Exception:  # pragma: no cover
    zstd = None


def _default_cache_root() -> Path:
    return Path(os.path.expanduser("~/.chimera/cache")).resolve()


@dataclass(frozen=True)
class CacheConfig:
    root_dir: Path = field(default_factory=_default_cache_root)
    max_bytes: int = 50 * 1024 * 1024 * 1024  # 50 GiB
    verify_content_hash: bool = True
    # Backward-compatible alias; if set, overrides verify_content_hash.
    verify_sha256: Optional[bool] = None
    verify_size: bool = True
    db_path: Optional[Path] = None
    lock_timeout_s: float = 60.0

    def resolved_db_path(self) -> Path:
        return (self.db_path or (self.root_dir / "index.sqlite3")).resolve()

    def should_verify_hash(self) -> bool:
        return self.verify_content_hash if self.verify_sha256 is None else bool(self.verify_sha256)


class LocalCache:
    def __init__(self, config: CacheConfig | None = None):
        self.config = config or CacheConfig()
        self.config.root_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.config.resolved_db_path())

    def ensure_uri_cached(self, uri: str, *, client: ZenoClient) -> Path:
        r = client.resolve(uri)
        content_id = str(r.get("content_id") or "").strip().lower()
        filename = str(r.get("filename") or "").strip()
        size = r.get("size")
        if not content_id or not filename:
            raise CacheCorruptError("Resolver returned missing content_id/filename", content_id=content_id or "unknown")
        return self.ensure_cached(content_id, filename, size=size, client=client)

    def ensure_cached(self, content_id: str, filename: str, *, size: int | None, client: ZenoClient) -> Path:
        cid = content_id.strip().lower()
        fname = filename.strip()

        # First try legacy cache hit path (no network).
        legacy_dir = self.config.root_dir / cid
        legacy_path = legacy_dir / fname
        legacy_rel = f"{cid}/{fname}"
        if self._is_valid_hit(legacy_path, cid, size=size):
            self._touch_index(cid, fname, legacy_rel, legacy_path)
            return legacy_path

        # Manifest-aware path:
        # - Use HEAD to decide if this CAS blob is likely a manifest (small)
        # - If small, download bytes once, try parse manifest; if parse fails, treat as raw blob (avoid double fetch)
        head_size = None
        try:
            head_size = client.head_blob(cid)
        except Exception:
            head_size = None
        if head_size is not None and head_size <= 256 * 1024:
            blob_bytes = client.get_blob_bytes(cid)
            m = try_parse_manifest(blob_bytes)
            if m is not None and m.whole_file_blake3:
                return self._ensure_from_manifest(manifest_id=cid, client=client)
            # Not a manifest; write this raw blob to cache using the bytes we already fetched.
            return self._ensure_raw_blob_from_bytes(content_id=cid, filename=fname, size=size, body=blob_bytes)

        content_dir = (self.config.root_dir / cid)
        final_path = (content_dir / fname)
        rel_path = f"{cid}/{fname}"
        lock_path = content_dir / ".lock"

        # Fast-path hit check (no locking)
        if self._is_valid_hit(final_path, cid, size=size):
            self._touch_index(cid, fname, rel_path, final_path)
            return final_path

    def _ensure_from_manifest(self, *, manifest_id: str, client: ZenoClient) -> Path:
        mid = manifest_id.strip().lower()
        manifests_dir = self.config.root_dir / "manifests"
        chunks_dir = self.config.root_dir / "chunks"
        manifests_dir.mkdir(parents=True, exist_ok=True)
        chunks_dir.mkdir(parents=True, exist_ok=True)

        # Download full manifest blob and parse
        manifest_bytes = client.get_blob_bytes(mid)
        m = try_parse_manifest(manifest_bytes)
        if m is None:
            raise CacheCorruptError("Manifest parse failed", content_id=mid)

        whole = m.whole_file_blake3
        if not whole:
            raise CacheCorruptError("Manifest missing whole_file_blake3", content_id=mid)

        # Persist manifest for inspection
        manifest_path = manifests_dir / f"{mid}.json"
        try:
            if not manifest_path.exists():
                tmp = manifest_path.with_suffix(".tmp")
                tmp.write_bytes(manifest_bytes)
                tmp.replace(manifest_path)
        except OSError:
            pass

        out_dir = self.config.root_dir / whole
        out_path = out_dir / (m.filename or whole)
        rel_path = f"{whole}/{out_path.name}"
        lock_path = out_dir / ".lock"

        # Fast hit check
        if self._is_valid_hit(out_path, whole, size=m.size_bytes if m.size_bytes else None):
            self._touch_index(whole, out_path.name, rel_path, out_path)
            return out_path

        with file_lock(lock_path, timeout_s=self.config.lock_timeout_s):
            if self._is_valid_hit(out_path, whole, size=m.size_bytes if m.size_bytes else None):
                self._touch_index(whole, out_path.name, rel_path, out_path)
                return out_path

            # Ensure all raw chunks present
            for ch in m.chunks:
                h = ch.hash
                # shard like CAS: first2/next2/hash
                chunk_path = chunks_dir / h[:2] / h[2:4] / h
                if chunk_path.is_file() and (not self.config.verify_size or chunk_path.stat().st_size == ch.size):
                    continue
                chunk_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = chunk_path.with_suffix(".tmp")
                client.get_blob(h, tmp)
                if self.config.verify_size and ch.size is not None:
                    if tmp.stat().st_size != ch.size:
                        try:
                            tmp.unlink()
                        except OSError:
                            pass
                        raise CacheCorruptError("Chunk size mismatch", content_id=h)
                if self.config.should_verify_hash():
                    got = compute_content_hash(tmp)
                    if got != h:
                        try:
                            tmp.unlink()
                        except OSError:
                            pass
                        raise CacheCorruptError("Chunk hash mismatch", content_id=h)
                tmp.replace(chunk_path)

            # Assemble file
            out_dir.mkdir(parents=True, exist_ok=True)
            tmp_out = out_path.with_suffix(out_path.suffix + ".assemble.tmp")
            if tmp_out.exists():
                try:
                    tmp_out.unlink()
                except OSError:
                    pass

            with tmp_out.open("wb") as w:
                if m.schema == "chimera.manifest.v3" and m.segments is not None:
                    for seg in m.segments:
                        kind = getattr(seg, "kind", "")
                        if kind == "raw_chunk":
                            h = getattr(seg, "hash")
                            chunk_path = chunks_dir / h[:2] / h[2:4] / h
                            with chunk_path.open("rb") as r:
                                while True:
                                    b = r.read(1024 * 1024)
                                    if not b:
                                        break
                                    w.write(b)
                            continue
                        if kind == "zstd_dict_patch":
                            if zstd is None:
                                raise CacheCorruptError("zstandard is required for patch segment materialization", content_id=mid)
                            dict_hash = getattr(seg, "dict_hash")
                            patch_hash = getattr(seg, "patch_hash")
                            dbytes = client.get_blob_bytes(dict_hash)
                            pbytes = client.get_blob_bytes(patch_hash)
                            dctx = zstd.ZstdDecompressor(dict_data=zstd.ZstdCompressionDict(dbytes))
                            out_bytes = dctx.decompress(
                                pbytes, max_output_size=max(1, int(getattr(seg, "uncompressed_size")))
                            )
                            w.write(out_bytes)
                            continue
                        raise CacheCorruptError(f"Unsupported manifest v3 segment kind: {kind}", content_id=mid)
                else:
                    for ch in m.chunks:
                        h = ch.hash
                        chunk_path = chunks_dir / h[:2] / h[2:4] / h
                        with chunk_path.open("rb") as r:
                            while True:
                                b = r.read(1024 * 1024)
                                if not b:
                                    break
                                w.write(b)

            # Verify assembled
            if self.config.verify_size and m.size_bytes:
                if tmp_out.stat().st_size != m.size_bytes:
                    try:
                        tmp_out.unlink()
                    except OSError:
                        pass
                    raise CacheCorruptError("Assembled size mismatch", content_id=whole)
            if self.config.should_verify_hash():
                got = compute_content_hash(tmp_out)
                if got != whole:
                    try:
                        tmp_out.unlink()
                    except OSError:
                        pass
                    raise CacheCorruptError("Assembled hash mismatch", content_id=whole)

            tmp_out.replace(out_path)

            self._touch_index(whole, out_path.name, rel_path, out_path)
            self.evict_if_needed(exclude_content_ids={whole})
            return out_path

        with file_lock(lock_path, timeout_s=self.config.lock_timeout_s):
            # Re-check after acquiring lock (another process may have filled it)
            if self._is_valid_hit(final_path, cid, size=size):
                self._touch_index(cid, fname, rel_path, final_path)
                return final_path

            content_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = final_path.with_suffix(final_path.suffix + ".download.tmp")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

            # Download to tmp (ZenoClient writes via its own tmp file; we then verify and move)
            client.get_blob(cid, tmp_path)

            # Verify size/hash before exposing
            if self.config.verify_size and size is not None:
                st = tmp_path.stat().st_size
                if int(st) != int(size):
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise CacheCorruptError(
                        f"Downloaded size mismatch for {cid[:12]}…: expected {size}, got {st}",
                        content_id=cid,
                    )
            if self.config.should_verify_hash():
                got = compute_content_hash(tmp_path)
                if got != cid:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise CacheCorruptError(
                        f"Downloaded hash mismatch for {cid[:12]}…: expected {cid[:12]}…, got {got[:12]}…",
                        content_id=cid,
                    )

            # Atomic move into place (if another process managed to create it between download and move,
            # keep the existing file and delete our tmp)
            try:
                if final_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                else:
                    tmp_path.replace(final_path)
            except OSError:
                # If replace fails for any reason, clean up tmp and re-raise
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
                raise

            # Update index and evict
            self._touch_index(cid, fname, rel_path, final_path)
            self.evict_if_needed(exclude_content_ids={cid})

            return final_path

    def _ensure_raw_blob_from_bytes(self, *, content_id: str, filename: str, size: int | None, body: bytes) -> Path:
        cid = content_id.strip().lower()
        fname = filename.strip()
        content_dir = self.config.root_dir / cid
        final_path = content_dir / fname
        rel_path = f"{cid}/{fname}"
        lock_path = content_dir / ".lock"

        if self._is_valid_hit(final_path, cid, size=size):
            self._touch_index(cid, fname, rel_path, final_path)
            return final_path

        with file_lock(lock_path, timeout_s=self.config.lock_timeout_s):
            if self._is_valid_hit(final_path, cid, size=size):
                self._touch_index(cid, fname, rel_path, final_path)
                return final_path

            content_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = final_path.with_suffix(final_path.suffix + ".download.tmp")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            tmp_path.write_bytes(body)

            if self.config.verify_size and size is not None:
                st = tmp_path.stat().st_size
                if int(st) != int(size):
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise CacheCorruptError("Downloaded size mismatch", content_id=cid)
            if self.config.should_verify_hash():
                got = compute_content_hash(tmp_path)
                if got != cid:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                    raise CacheCorruptError("Downloaded hash mismatch", content_id=cid)
            tmp_path.replace(final_path)
            self._touch_index(cid, fname, rel_path, final_path)
            self.evict_if_needed(exclude_content_ids={cid})
            return final_path

    def touch(self, content_id: str) -> None:
        touch(self.config.resolved_db_path(), content_id=content_id.strip().lower())

    def evict_if_needed(self, *, exclude_content_ids: set[str] | None = None) -> None:
        exclude = exclude_content_ids or set()
        db = self.config.resolved_db_path()
        total = total_size_bytes(db)
        if total <= self.config.max_bytes:
            return

        # Avoid spinning forever if max_bytes is misconfigured
        if self.config.max_bytes <= 0:
            return

        # Evict oldest entries until under cap
        while total > self.config.max_bytes:
            candidates = eviction_candidates(db, exclude_content_ids=exclude, limit=25)
            if not candidates:
                return
            for e in candidates:
                if total <= self.config.max_bytes:
                    break
                abs_path = (self.config.root_dir / e.rel_path).resolve()
                # delete file (best-effort)
                try:
                    if abs_path.exists():
                        abs_path.unlink()
                except OSError:
                    pass
                # delete directory if empty
                try:
                    d = abs_path.parent
                    if d.exists() and d.is_dir():
                        d.rmdir()
                except OSError:
                    pass
                delete_entry(db, content_id=e.content_id)
                total = total_size_bytes(db)

    def _touch_index(self, cid: str, fname: str, rel_path: str, final_path: Path) -> None:
        db = self.config.resolved_db_path()
        size_bytes = int(final_path.stat().st_size) if final_path.exists() else 0
        existing = get_entry(db, content_id=cid)
        if existing is None:
            upsert_entry(db, content_id=cid, filename=fname, rel_path=rel_path, size_bytes=size_bytes)
        else:
            # upsert updates last_accessed_at; keep in sync if filename/path/size changed
            upsert_entry(db, content_id=cid, filename=fname, rel_path=rel_path, size_bytes=size_bytes)

    def _is_valid_hit(self, path: Path, content_id: str, *, size: int | None) -> bool:
        if not path.is_file():
            return False
        if self.config.verify_size and size is not None:
            try:
                if int(path.stat().st_size) != int(size):
                    return False
            except OSError:
                return False
        # For MVP: do not re-hash on hit (expensive). We verify on download.
        return True

