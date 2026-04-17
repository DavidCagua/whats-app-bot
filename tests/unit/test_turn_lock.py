"""
Unit tests for the per-wa_id turn serialization lock.

Postgres advisory locks need a real DB to test their actual blocking
behavior, but the contract we care about is:

1. Hash function is deterministic, fits in a signed bigint, and
   returns 0 for empty input.
2. Acquiring the lock issues `pg_advisory_lock(key)` and the matching
   `pg_advisory_unlock(key)` on exit (success path).
3. The lock is released even when the wrapped block raises.
4. If acquisition fails (DB unreachable, timeout), the context manager
   logs and falls back to running the block without serialization
   instead of raising — we never want to drop a customer's message.
5. Empty wa_id short-circuits without touching the DB at all.
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

def _make_mock_engine():
    """Build a mock SQLAlchemy engine whose raw_connection() returns a
    cursor we can inspect. Returns (engine, conn, cursor) for assertions."""
    cursor = MagicMock(name="cursor")
    conn = MagicMock(name="conn")
    conn.cursor.return_value = cursor
    engine = MagicMock(name="engine")
    engine.raw_connection.return_value = conn
    return engine, conn, cursor


class TestWaIdTurnLockHappyPath:
    def test_acquire_and_release_on_success(self):
        engine, conn, cursor = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                pass

        # Lock acquired with the right key
        sql_calls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("pg_advisory_lock" in sql for sql in sql_calls)
        assert any("pg_advisory_unlock" in sql for sql in sql_calls)
        # Connection returned to the pool
        conn.close.assert_called_once()

    def test_lock_uses_consistent_key_for_same_wa_id(self):
        engine, conn, cursor = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                pass

        # Find the pg_advisory_lock call and capture its key arg
        lock_args = [
            c.args[1]
            for c in cursor.execute.call_args_list
            if "pg_advisory_lock" in c.args[0]
        ]
        unlock_args = [
            c.args[1]
            for c in cursor.execute.call_args_list
            if "pg_advisory_unlock" in c.args[0]
        ]
        assert len(lock_args) == 1
        assert len(unlock_args) == 1
        # Same key for lock and unlock
        assert lock_args[0] == unlock_args[0]
        # Key matches the deterministic hash
        assert lock_args[0] == (_wa_id_to_lock_key("+573001234567"),)

    def test_lock_timeout_set_before_acquire(self):
        engine, conn, cursor = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567", timeout_seconds=10):
                pass

        sql_calls = [c.args[0] for c in cursor.execute.call_args_list]
        # SET lock_timeout fires BEFORE pg_advisory_lock so the wait is bounded.
        timeout_idx = next(i for i, s in enumerate(sql_calls) if "lock_timeout" in s)
        lock_idx = next(i for i, s in enumerate(sql_calls) if "pg_advisory_lock" in s)
        assert timeout_idx < lock_idx
        # 10 seconds → 10000 ms
        assert "10000ms" in sql_calls[timeout_idx]


class TestWaIdTurnLockEmptyWaId:
    def test_empty_string_short_circuits_without_db(self):
        engine, conn, cursor = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock(""):
                pass

        # No DB connection touched at all
        engine.raw_connection.assert_not_called()
        conn.close.assert_not_called()

    def test_none_short_circuits_without_db(self):
        engine, conn, cursor = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock(None):  # type: ignore[arg-type]
                pass

        engine.raw_connection.assert_not_called()


class TestWaIdTurnLockExceptionPath:
    def test_lock_released_when_block_raises(self):
        engine, conn, cursor = _make_mock_engine()
        with patch("app.services.turn_lock.engine", engine):
            with pytest.raises(RuntimeError):
                with wa_id_turn_lock("+573001234567"):
                    raise RuntimeError("boom")

        # pg_advisory_unlock and conn.close still ran
        sql_calls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("pg_advisory_unlock" in sql for sql in sql_calls)
        conn.close.assert_called_once()


class TestWaIdTurnLockFallback:
    def test_acquisition_failure_falls_back_to_unlocked_processing(self):
        """
        If pg_advisory_lock raises (DB unreachable, lock_timeout, …),
        the context manager logs and yields anyway. We never want to
        drop a customer message — one stale reply is preferable.
        """
        cursor = MagicMock(name="cursor")
        # First execute (SET lock_timeout) succeeds; second (pg_advisory_lock) raises
        cursor.execute.side_effect = [None, Exception("connection refused")]
        conn = MagicMock(name="conn")
        conn.cursor.return_value = cursor
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
        # We did NOT try to release a lock we never held
        unlock_calls = [
            c for c in cursor.execute.call_args_list
            if "pg_advisory_unlock" in c.args[0]
        ]
        assert unlock_calls == []

    def test_engine_unreachable_falls_back_without_raising(self):
        """If raw_connection() itself fails, we still yield."""
        engine = MagicMock(name="engine")
        engine.raw_connection.side_effect = Exception("could not connect")

        block_ran = {"value": False}
        with patch("app.services.turn_lock.engine", engine):
            with wa_id_turn_lock("+573001234567"):
                block_ran["value"] = True

        assert block_ran["value"] is True
