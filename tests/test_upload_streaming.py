"""Uploads that never hold the whole file in memory.

An RDB is 40-140 MB and the ceiling is 500 (`library.RDB_MAX_BYTES`); an SCD's
ceiling is 200. The upload handler used to do `self.rfile.read(length)`, which
made that whole size resident -- and then hashed it and wrote it, two more
passes over the same bytes. `ThreadingHTTPServer` puts no cap on concurrent
uploads, so two engineers at the ceiling meant a gigabyte of RSS on a field
laptop.

Measured on a 283 MB synthetic RDB through the real HTTP path: peak RSS grew
291 MB before, 9 MB after.

What these tests pin is the behaviour that buys it -- chunked reads, a hash
that grows with the read, an atomic rename at the end, and no debris when a
transfer dies partway.
"""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import cfbwrite as cfb
import pytest

from selfiles import rdb, rdb_cache

#: A GLE is not decoration here. `_scan_existing` builds a `RelayEntry` only
#: from `.gle` streams, and `process_upload` sets `reused = bool(relays)` -- so
#: an RDB whose relays own no diagram (a SEL-2440 concentrator, say) is
#: re-extracted on every single upload. Pre-existing, out of scope here, but it
#: is why this fixture carries one.
_GLE = (b'<?xml version="1.0" encoding="utf-8"?>\r\n'
        b'<editor version="1.0"><page name="GL1"><elements /></page></editor>\r\n')


def _ole(tmp_path: Path, marker: bytes = b"hello") -> bytes:
    """A small but genuine Compound File, so `process_upload` really extracts."""
    path = tmp_path / "src.rdb"
    cfb.write_ole(path, [
        cfb.Entry(name="Relays", is_storage=True, size=0, read=None, children=[
            cfb.Entry(name="QPC1", is_storage=True, size=0, read=None, children=[
                cfb.Entry(name="SET_1.TXT", is_storage=False, size=len(marker),
                          read=lambda: marker, children=[]),
                cfb.Entry(name="Misc", is_storage=True, size=0, read=None,
                          children=[
                    cfb.Entry(name="GL1.gle", is_storage=False, size=len(_GLE),
                              read=lambda: _GLE, children=[]),
                ]),
            ]),
        ]),
    ])
    return path.read_bytes()


class _CountingReader(io.BytesIO):
    """Records the size of every `read()` the code under test asks for."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.sizes: list[int] = []

    def read(self, n=-1):
        chunk = super().read(n)
        self.sizes.append(len(chunk))
        return chunk


class TestStreamToFile:

    def test_the_hash_matches_hashing_the_whole_thing(self, tmp_path):
        """The hash is built chunk by chunk; it has to equal the one-shot one,
        because it is the cache key every visitor and every restart shares.

        Fails if the incremental update is dropped or reordered."""
        data = bytes(range(256)) * 900
        dest = tmp_path / "out.bin"
        got = rdb.stream_to_file(io.BytesIO(data), len(data), dest)
        assert got == hashlib.sha256(data).hexdigest()
        assert dest.read_bytes() == data

    def test_it_reads_in_bounded_chunks_not_all_at_once(self, tmp_path):
        """THE point of the change. Fails if anyone replaces the loop with a
        single `source.read(length)` -- which is what it used to be."""
        data = b"x" * (5 * rdb.UPLOAD_CHUNK + 12345)
        src = _CountingReader(data)
        rdb.stream_to_file(src, len(data), tmp_path / "out.bin")
        assert max(src.sizes) <= rdb.UPLOAD_CHUNK
        assert len(src.sizes) >= 6

    def test_it_never_reads_past_the_declared_length(self, tmp_path):
        """Content-Length bounds the body. Reading beyond it would consume the
        next pipelined request off the socket."""
        src = _CountingReader(b"A" * 100 + b"B" * 100)
        rdb.stream_to_file(src, 100, tmp_path / "out.bin")
        assert (tmp_path / "out.bin").read_bytes() == b"A" * 100

    def test_a_short_body_is_an_error_not_a_truncated_file(self, tmp_path):
        """A dropped connection mid-upload must not produce a file that looks
        complete. Fails if the loop stops on a falsy chunk instead of raising."""
        with pytest.raises(ValueError, match="interrompido"):
            rdb.stream_to_file(io.BytesIO(b"short"), 5000, tmp_path / "out.bin")

    def test_progress_is_reported_while_receiving(self, tmp_path):
        """Server-side receipt of 140 MB used to be invisible -- the client's
        XHR bar covered the send, then the page sat on 'processando'."""
        seen = []
        data = b"y" * (3 * rdb.UPLOAD_CHUNK)
        rdb.stream_to_file(io.BytesIO(data), len(data), tmp_path / "out.bin",
                           on_progress=lambda d, t, s: seen.append((d, t, s)))
        assert len(seen) >= 3
        assert seen[-1][0] == seen[-1][1] == len(data)
        assert all(s == "Recebendo arquivo" for _, _, s in seen)

    def test_an_empty_upload_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            rdb.stream_to_file(io.BytesIO(b""), 0, tmp_path / "out.bin")


class TestProcessUploadStream:

    def test_it_agrees_with_the_bytes_version(self, tmp_path):
        """`process_upload(data)` is now a wrapper over the streaming path.
        Both must produce the same cache entry, or the two ways a file can
        enter the project would disagree about its identity."""
        data = _ole(tmp_path)
        a = rdb.process_upload(data, "a.rdb", cache_root=tmp_path / "c1")
        b = rdb.process_upload_stream(io.BytesIO(data), len(data), "a.rdb",
                                      cache_root=tmp_path / "c2")
        assert a.sha256 == b.sha256
        assert a.display_name == b.display_name
        assert [r.name for r in a.relays] == [r.name for r in b.relays]

    def test_a_second_upload_of_the_same_bytes_reuses_the_extraction(self, tmp_path):
        """The duplicate check moved AFTER the read, because the sha256 is of
        content that has only just arrived. This is what keeps that cheap: the
        second upload re-reads the file but does not re-extract it."""
        data = _ole(tmp_path)
        cache = tmp_path / "cache"
        first = rdb.process_upload_stream(io.BytesIO(data), len(data), "a.rdb",
                                          cache_root=cache)
        second = rdb.process_upload_stream(io.BytesIO(data), len(data), "b.rdb",
                                           cache_root=cache)
        assert first.reused is False
        assert second.reused is True
        assert second.display_name == "b.rdb"      # the name is this upload's

    def test_an_interrupted_upload_leaves_nothing_behind(self, tmp_path):
        """A dropped connection must not strand a part file. `cache/rdb/` is
        NOT wiped at boot -- surviving a restart is the point of it -- so a
        leak there is permanent."""
        cache = tmp_path / "cache"
        with pytest.raises(ValueError):
            rdb.process_upload_stream(io.BytesIO(b"tooshort"), 999_999,
                                      "x.rdb", cache_root=cache)
        incoming = cache / rdb_cache.INCOMING_DIRNAME
        assert list(incoming.iterdir()) == []

    def test_a_corrupt_rdb_leaves_nothing_behind(self, tmp_path):
        """Reaches the extractor and fails there, after the temp file exists."""
        cache = tmp_path / "cache"
        junk = b"nao sou um OLE2" * 100
        # olefile raises NotOleFileError, which IS an OSError -- naming the
        # base is precise enough to be a real assertion and loose enough to
        # survive an olefile that renames its own exception.
        with pytest.raises(OSError):
            rdb.process_upload_stream(io.BytesIO(junk), len(junk), "x.rdb",
                                      cache_root=cache)
        incoming = cache / rdb_cache.INCOMING_DIRNAME
        assert not incoming.is_dir() or list(incoming.iterdir()) == []

    def test_the_incoming_dir_is_not_mistaken_for_a_cache_entry(self, tmp_path):
        """`_incoming` sits inside the cache root. `sweep` filters on a sha256
        name, so it is skipped -- pinned because a sweeper that treated it as
        an entry would delete an upload in flight."""
        data = _ole(tmp_path)
        cache = tmp_path / "cache"
        rdb.process_upload_stream(io.BytesIO(data), len(data), "a.rdb",
                                  cache_root=cache)
        import logging
        removed = rdb_cache.sweep(logging.getLogger("t"), max_gb=0.0,
                                  max_age_days=0.0, min_age_seconds=0.0,
                                  root=cache)
        assert (cache / rdb_cache.INCOMING_DIRNAME).is_dir()
        assert removed == 1      # the entry went, the _incoming dir did not


class TestIncomingSweeper:

    def test_a_stale_part_file_is_removed(self, tmp_path):
        """`process_upload_stream`'s `finally` covers errors and disconnects.
        It cannot cover kill -9, OOM or a power cut mid-receive, and up to
        500 MB per dead upload would sit there for good."""
        import logging
        incoming = tmp_path / rdb_cache.INCOMING_DIRNAME
        incoming.mkdir(parents=True)
        stale = incoming / "old.rdb-part"
        stale.write_bytes(b"x" * 1024)
        old = time.time() - 7 * 3600
        import os
        os.utime(stale, (old, old))

        rdb_cache._sweep_incoming(tmp_path, time.time(), logging.getLogger("t"))
        assert not stale.exists()

    def test_an_upload_in_flight_is_left_alone(self, tmp_path):
        """The sweeper runs every 15 minutes, alongside the session sweeper. A
        140 MB upload is still arriving; deleting its part file would fail the
        transfer for no reason."""
        import logging
        incoming = tmp_path / rdb_cache.INCOMING_DIRNAME
        incoming.mkdir(parents=True)
        fresh = incoming / "live.rdb-part"
        fresh.write_bytes(b"x" * 1024)

        rdb_cache._sweep_incoming(tmp_path, time.time(), logging.getLogger("t"))
        assert fresh.exists()
