"""
Unit tests for app/handlers/whatsapp_handler.py — pre-send abort gate.

Regression context: the greeting fast-path returns a direct reply
without going through the agent (so the existing __ABORTED__ sentinel
check in _run_agent_and_send doesn't fire). When a customer sends "Hola"
followed quickly by "Para pedir", the greeting was already on its way
to Twilio by the time the abort signal arrived. The customer ended up
seeing the long greeting AND a generic chat reply for "Para pedir" —
two replies for what should be one coherent thread.

The pre-send abort gate (added in _run_agent_and_send) catches this:
right before send_message, it re-checks Redis for an active abort flag
on this customer's abort_key. If set, it drops the response (and
clears the flag so it can't strand future turns).
"""

from unittest.mock import patch, MagicMock

import pytest

from app.handlers import whatsapp_handler


class TestPreSendAbortGate:
    def _run_with(
        self,
        *,
        agent_response: str,
        abort_set: bool,
        abort_key: str = "abort:whatsapp:+14155238886:+573177000722",
    ):
        """Invoke _run_agent_and_send with mocked dependencies, return whether send fired."""
        send_called = MagicMock(return_value={"sid": "SMfake"})

        with patch.object(
            whatsapp_handler.conversation_manager,
            "process",
            return_value=agent_response,
        ), patch(
            "app.services.debounce.check_abort", return_value=abort_set,
        ) as check, patch(
            "app.services.debounce.clear_abort",
        ) as clear, patch.object(
            whatsapp_handler, "send_message", send_called,
        ):
            ok = whatsapp_handler._run_agent_and_send(
                wa_id="+573177000722",
                message_body="Hola",
                name="David",
                business_context={"business_id": "biela", "business": {"name": "Biela"}},
                message_id="SMfirst",
                abort_key=abort_key,
                stale_turn=False,
            )

        return ok, send_called, check, clear

    def test_drops_send_when_abort_flag_is_set(self):
        """The exact greeting-fast-path case: response is the rendered
        greeting, abort signal is set during the Twilio round-trip."""
        ok, send_called, check, clear = self._run_with(
            agent_response="Hola David. Gracias por comunicarte con Biela...",
            abort_set=True,
        )
        assert ok is False
        send_called.assert_not_called()
        # We MUST clear the flag so a stale signal doesn't strand the
        # next turn (which would never see check_abort=True if we left
        # it set forever).
        clear.assert_called_once()

    def test_sends_normally_when_abort_flag_is_not_set(self):
        ok, send_called, check, clear = self._run_with(
            agent_response="Tu pedido ya fue confirmado.",
            abort_set=False,
        )
        assert ok is True
        send_called.assert_called_once()
        clear.assert_not_called()

    def test_no_abort_key_skips_the_check_entirely(self):
        """When the caller didn't pass an abort_key (e.g. the voice-reply
        worker), the gate must be a no-op — never call check_abort."""
        send_called = MagicMock(return_value={"sid": "SMfake"})
        with patch.object(
            whatsapp_handler.conversation_manager,
            "process",
            return_value="hola",
        ), patch(
            "app.services.debounce.check_abort",
        ) as check, patch.object(
            whatsapp_handler, "send_message", send_called,
        ):
            whatsapp_handler._run_agent_and_send(
                wa_id="+573177000722",
                message_body="hola",
                name="David",
                business_context={"business_id": "biela", "business": {"name": "Biela"}},
                message_id="SMfirst",
                abort_key=None,
            )
        check.assert_not_called()
        send_called.assert_called_once()

    def test_aborted_sentinel_short_circuits_before_pre_send_check(self):
        """The agent-side __ABORTED__ sentinel still wins — it returns
        False without even reaching the pre-send gate (so check_abort
        isn't called, since the agent already handled it)."""
        send_called = MagicMock()
        with patch.object(
            whatsapp_handler.conversation_manager,
            "process",
            return_value="__ABORTED__",
        ), patch(
            "app.services.debounce.check_abort",
        ) as check, patch.object(
            whatsapp_handler, "send_message", send_called,
        ):
            ok = whatsapp_handler._run_agent_and_send(
                wa_id="+573177000722",
                message_body="hola",
                name="David",
                business_context={"business_id": "biela", "business": {"name": "Biela"}},
                message_id="SMfirst",
                abort_key="abort:whatsapp:+14155238886:+573177000722",
            )
        assert ok is False
        send_called.assert_not_called()
        # __ABORTED__ short-circuits before the pre-send check.
        check.assert_not_called()
