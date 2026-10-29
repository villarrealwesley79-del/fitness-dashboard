"""PER-1588 N3: the store-size check gives up (keeps its last answer) only when the fd table is full."""
import errno
import os
import stat

import pytest

import server


def _store(tmp_path, files=6):
    root = tmp_path / "store"
    (root / "locked").mkdir(parents=True)
    for i in range(files):
        (root / f"f{i}").write_bytes(b"x")
    return root


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unreadable_subdirectory_does_not_blind_the_cap(tmp_path):
    root = _store(tmp_path)
    (root / "locked").chmod(0)
    try:
        guard = server.LogRejectedRedirects(None, store=str(root), store_max_files=5)
        assert guard._store_is_full() is True  # counts the six readable files; used to keep "not full"
    finally:
        (root / "locked").chmod(stat.S_IRWXU)


@pytest.mark.parametrize("code", [errno.EMFILE, errno.ENFILE])
def test_a_full_fd_table_keeps_the_last_answer(tmp_path, monkeypatch, code):
    root = _store(tmp_path)

    def walk(top, onerror=None):
        onerror(OSError(code, "too many open files"))
        return iter(())

    guard = server.LogRejectedRedirects(None, store=str(root), store_max_files=5)
    guard.store_full = True
    monkeypatch.setattr(server.os, "walk", walk)
    assert guard._store_is_full() is True
    assert guard.store_checked == -60.0  # not marked as checked: the next request tries again
