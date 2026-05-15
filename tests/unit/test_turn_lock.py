"""
Unit tests for the per-wa_id turn serialization lock.

Postgres advisory locks need a real DB to test their actual blocking
behavior, but the contract we care about is:

1. Hash function is deterministic, fits in a signed bigint, and
   returns 0 for empty input.
2. Happy path (no contention): pg_try_advisory_lock succeeds
   immediately, pg_advisory_unlock issued on exit, connection closed.
3. Contention path: pg_try_advisory_lock returns False, then
   lock_timeout is set and pg_advisory_lock blocks until acquired.
4. The lock is released even when the wrapped block raises.
5. If acquisition fails (DB unreachable, timeout), the context manager
   logs and falls back to running the block without serialization
   instead of raising — we never want to drop a customer's message.
6. Empty wa_id short-circuits without touching the DB at all.
7. TurnLockResult.waited is True only when contention was detected.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.turn_lock import _wa_id_to_lock_key, wa_id_turn_lock


# ---------------------------------------------------------------------------
# Hash function
# ---------------------------------------------------------------------------

class TestHashFunction:
    def test_deterministic(self):
        assert _wa_id_to_lock_key("+573001234567") == _wa_id_to_lock_key("+573001234567")

    def test_different_wa_ids_yield_different_keys(self):
        a = _wa_id_to_lock_key("+573001234567")
        b = _wa_id_to_lock_key("+573009999999")
        assert a != b

    def test_fits_in_signed_bigint(self):
        # Postgres bigint is signed 64-bit: -2**63 .. 2**63 - 1
        for wa_id in ("+573001234567", "573001234567", "whatsapp:+1234", ""):
            key = _wa_id_to_lock_key(wa_id)
            assert -(2 ** 63) <= key <= 2 ** 63 - 1

    def test_empty_returns_zero(self):
        assert _wa_id_to_lock_key("") == 0
        assert _wa_id_to_lock_key(None) == 0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Lock context manager
# ---------------------------------------------------------------------------

def _make_mock_engine(try_lock_result=True):
    """Build a mock SQLAlchemy engine whose raw_connection() returns
    cursors we can inspect.

    The implementation creates multiple cursors via conn.cursor().
    We track them all so tests can inspect the SQL each cursor received.

    Args:
        try_lock_result: what pg_try_advisory_lock returns (True = no
            contention, False = another turn holds the lock).
    """
    cursors = []

    def make_cursor():
        cur = MagicMock(name=f"cursor-{len(cursors)}")
        # pg_try_advisory_lock returns a single-row result
        cur.fetchone.return_value = (try_lock_result,)
        cursors.append(cur)
        return cur

    conn = MagicMock(name="conn")
    conn.cursor.side_effect = make_cursor
    engine = MagicMock(name="engine")
    engine.raw_connection.return_value = conn
    return engine, conn, cursors


def _all_sql(cursors):
    """Collect all SQL strings executed across all cursors."""
    sqls = []
    for cur in cursors:
        for c in cur.execute.call_args_list:
            sqls.append(c.args[0])
    return sqls


class TestWaIdTurnLockHappyPath:
    """Happy path: pg_try_advisory_lock succeeds immediately (no contention)."""

    def test_acquire_and_release_on_success(self):
        engine, conn, cursors = _make_mock_engine(try_lock_result=True)
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                pass

        sqls = _all_sql(cursors)
        # Try-lock issued
        assert any("pg_try_advisory_lock" in sql for sql in sqls)
        # Unlock issued on exit
        assert any("pg_advisory_unlock" in sql for sql in sqls)
        # Connection returned to the pool
        conn.close.assert_called_once()

    def test_lock_uses_consistent_key(self):
        key = _wa_id_to_lock_key("+573001234567")
        engine, conn, cursors = _make_mock_engine(try_lock_result=True)
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                pass

        # Find the try-lock and unlock calls across all cursors
        lock_keys = []
        unlock_keys = []
        for cur in cursors:
            for c in cur.execute.call_args_list:
                sql = c.args[0]
                if "pg_try_advisory_lock" in sql:
                    lock_keys.append(c.args[1])
                elif "pg_advisory_unlock" in sql:
                    unlock_keys.append(c.args[1])

        assert len(lock_keys) == 1
        assert len(unlock_keys) == 1
        # Same key for lock and unlock
        assert lock_keys[0] == unlock_keys[0]
        assert lock_keys[0] == (key,)

    def test_no_lock_timeout_when_no_contention(self):
        """When try-lock succeeds, SET lock_timeout is never issued."""
        engine, conn, cursors = _make_mock_engine(try_lock_result=True)
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567", timeout_seconds=10):
                pass

        sqls = _all_sql(cursors)
        assert not any("lock_timeout" in sql for sql in sqls)

    def test_waited_is_false(self):
        engine, conn, cursors = _make_mock_engine(try_lock_result=True)
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567") as result:
                pass
        assert result.waited is False


class TestWaIdTurnLockContention:
    """Contention path: pg_try_advisory_lock returns False, then blocks."""

    def test_contention_sets_timeout_before_blocking_lock(self):
        engine, conn, cursors = _make_mock_engine(try_lock_result=False)
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567", timeout_seconds=10):
                pass

        sqls = _all_sql(cursors)
        # lock_timeout IS set in the contention path
        assert any("lock_timeout" in sql for sql in sqls)
        timeout_sql = next(s for s in sqls if "lock_timeout" in s)
        assert "10000ms" in timeout_sql
        # Blocking pg_advisory_lock issued after timeout
        timeout_idx = sqls.index(timeout_sql)
        lock_idx = next(i for i, s in enumerate(sqls) if s == "SELECT pg_advisory_lock(%s)")
        assert timeout_idx < lock_idx

    def test_waited_is_true(self):
        engine, conn, cursors = _make_mock_engine(try_lock_result=False)
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567") as result:
                pass
        assert result.waited is True

    def test_unlock_still_issued(self):
        engine, conn, cursors = _make_mock_engine(try_lock_result=False)
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                pass

        sqls = _all_sql(cursors)
        assert any("pg_advisory_unlock" in sql for sql in sqls)
        conn.close.assert_called_once()


class TestWaIdTurnLockEmptyWaId:
    def test_empty_string_short_circuits_without_db(self):
        engine, conn, cursors = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock(""):
                pass

        # No DB connection touched at all
        engine.raw_connection.assert_not_called()
        conn.close.assert_not_called()

    def test_none_short_circuits_without_db(self):
        engine, conn, cursors = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock(None):  # type: ignore[arg-type]
                pass

        engine.raw_connection.assert_not_called()


class TestWaIdTurnLockExceptionPath:
    def test_lock_released_when_block_raises(self):
        engine, conn, cursors = _make_mock_engine(try_lock_result=True)
        with patch("app.services.turn_lock.engine", engine):
            with pytest.raises(RuntimeError):
                with wa_id_turn_lock("+573001234567"):
                    raise RuntimeError("boom")

        # pg_advisory_unlock and conn.close still ran
        sqls = _all_sql(cursors)
        assert any("pg_advisory_unlock" in sql for sql in sqls)
        conn.close.assert_called_once()


class TestWaIdTurnLockFallback:
    def test_acquisition_failure_falls_back_to_unlocked_processing(self):
        """
        If pg_try_advisory_lock raises (DB unreachable, lock_timeout, …),
        the context manager logs and yields anyway. We never want to
        drop a customer message — one stale reply is preferable.
        """
        cursors_created = []

        def make_failing_cursor():
            cur = MagicMock(name=f"cursor-{len(cursors_created)}")
            # pg_try_advisory_lock itself raises
            cur.execute.side_effect = Exception("connection refused")
            cursors_created.append(cur)
            return cur

        conn = MagicMock(name="conn")
        conn.cursor.side_effect = make_failing_cursor
        engine = MagicMock(name="engine")
        engine.raw_connection.return_value = conn

        block_ran = {"value": False}
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                block_ran["value"] = True

        # The block ran despite the lock failing
        assert block_ran["value"] is True
        # Connection still closed in the finally
        conn.close.assert_called_once()
        # We did NOT try to release a lock we never held (locked=False)
        all_sqls = _all_sql(cursors_created)
        assert not any("pg_advisory_unlock" in sql for sql in all_sqls)

    def test_engine_unreachable_falls_back_without_raising(self):
        """If raw_connection() itself fails, we still yield."""
        engine = MagicMock(name="engine")
        engine.raw_connection.side_effect = Exception("could not connect")

        block_ran = {"value": False}
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                block_ran["value"] = True

        assert block_ran["value"] is True
