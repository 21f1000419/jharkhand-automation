from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from services.sms_otp_client import SmsOtpClient


class SmsOtpClientTests(unittest.TestCase):
    def test_get_otp_uses_the_certifi_ssl_context(self) -> None:
        client = SmsOtpClient("https://sms-server.example")
        response = MagicMock()
        response.read.return_value = b'{"found": true, "otp": "123456"}'
        context_manager = MagicMock()
        context_manager.__enter__.return_value = response

        with patch("services.sms_otp_client.urlopen", return_value=context_manager) as open_url:
            self.assertEqual(client._get_otp("https://sms-server.example/otp"), "123456")

        open_url.assert_called_once_with(
            "https://sms-server.example/otp", timeout=8, context=client._ssl_context
        )


if __name__ == "__main__":
    unittest.main()
