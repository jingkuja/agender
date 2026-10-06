from agender.browser.lock import FileLock, LockHeld


def test_lock_exclusive(tmp_path):
    path = tmp_path / "x.lock"
    a = FileLock(path)
    a.acquire()
    b = FileLock(path)
    try:
        b.acquire()
        raise AssertionError("second lock should fail")
    except LockHeld:
        pass
    a.release()
    b.acquire()
    b.release()
