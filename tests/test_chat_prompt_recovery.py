"""The chat never promises a recovery channel that does not exist (TASK-50).

The AI-persona run on the demo (26.10) had the chat tell a 74-year-old patient
without an email «te va a enviar un código por WhatsApp». No code is sent by
WhatsApp or SMS anywhere (notifications/otp_send.py: WAHA only on loopback,
SMS a stub, Meta refused without an approved template); the reset link goes
to the account's email only. The canonical chat instructions say exactly that
and name the way out that works today: the front desk adds an email.
"""
import unittest

from app.ai import prompts


class TestChatRecoveryPromise(unittest.TestCase):
    def _texts(self):
        return {clave: prompts.original(clave) for clave in ("chat_sistema", "chat_anonimo")}

    def test_no_code_by_whatsapp_or_sms_is_promised(self):
        for clave, text in self._texts().items():
            low = text.lower()
            self.assertNotIn("whatsapp code", low, clave)
            self.assertNotIn("sms code", low, clave)
            self.assertNotIn("code by whatsapp", low, clave)

    def test_recovery_names_email_and_the_desk(self):
        text = prompts.original("chat_sistema")
        start = text.index("Password recovery")
        sentence = text[start:start + 600]
        self.assertIn("email", sentence)
        self.assertIn("{hospital}", sentence)


if __name__ == "__main__":
    unittest.main()
