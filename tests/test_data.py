from __future__ import annotations

import io
import json
import unittest
from unittest.mock import call, patch
from urllib.error import HTTPError, URLError

from htx_bot.data import HTXNetworkError, fetch_htx_candles


def response(payload: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


OK_PAYLOAD = {
    "status": "ok",
    "data": [
        {
            "id": 1_700_000_000,
            "open": 1800,
            "high": 1810,
            "low": 1790,
            "close": 1805,
            "vol": 123,
        }
    ],
}


class FetchHTXCandlesTests(unittest.TestCase):
    @patch("htx_bot.data.time.sleep")
    @patch("htx_bot.data.urlopen")
    def test_tls_handshake_timeout_retries_on_official_fallback(self, open_mock, sleep_mock) -> None:
        open_mock.side_effect = [
            URLError(TimeoutError("_ssl.c:990: The handshake operation timed out")),
            response(OK_PAYLOAD),
        ]

        candles = fetch_htx_candles(size=1)

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].close, 1805)
        self.assertIn("api.huobi.pro", open_mock.call_args_list[0].args[0].full_url)
        self.assertIn("api-aws.huobi.pro", open_mock.call_args_list[1].args[0].full_url)
        sleep_mock.assert_called_once_with(1.0)

    @patch("htx_bot.data.time.sleep")
    @patch("htx_bot.data.urlopen")
    def test_network_error_is_clear_after_bounded_retries(self, open_mock, sleep_mock) -> None:
        error = URLError(TimeoutError("_ssl.c:990: The handshake operation timed out"))
        open_mock.side_effect = [error, error, error]

        with self.assertRaisesRegex(HTXNetworkError, "after 3 attempts.*handshake operation timed out"):
            fetch_htx_candles(size=1)

        self.assertEqual(open_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_args_list, [call(1.0), call(2.0)])

    @patch("htx_bot.data.time.sleep")
    @patch("htx_bot.data.urlopen")
    def test_http_errors_are_not_retried(self, open_mock, sleep_mock) -> None:
        open_mock.side_effect = HTTPError(
            "https://api.huobi.pro/market/history/kline",
            400,
            "Bad Request",
            {},
            None,
        )

        with self.assertRaises(HTTPError):
            fetch_htx_candles(size=1)

        open_mock.assert_called_once()
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
