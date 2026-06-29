"""
test_alerts_notifiers.py
======================
Coverage-focused tests for the notification channels in
stock_toolkit/alerts.py — email (SMTP), Pushover, and Slack — across
their skip (missing config), success, and failure branches. All network
and SMTP I/O is mocked; nothing is actually sent.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")

import pathlib  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from stock_toolkit import alerts  # noqa: E402

CTX = {"price": 150.25, "rsi14": 65.0, "change_pct": 1.2, "missing": None}

EMAIL_CFG = {
    "ALERT_SMTP_HOST": "smtp.example.com", "ALERT_SMTP_PORT": "587",
    "ALERT_SMTP_USER": "u@example.com", "ALERT_SMTP_PASS": "secret",
    "ALERT_EMAIL": "to@example.com",
}


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class TestNotifiers(unittest.TestCase):

    def _cfg(self, **kw):
        return mock.patch.object(alerts, "_cfg", dict(kw))

    # ── email ─────────────────────────────────────────────────────────────

    def test_email_missing_config_skips(self):
        with self._cfg(), mock.patch.object(alerts.smtplib, "SMTP") as smtp:
            _quiet(alerts.notify_email, "AAPL", "rsi14 > 70", CTX)
            smtp.assert_not_called()

    def test_email_success_sends(self):
        with self._cfg(**EMAIL_CFG), \
             mock.patch.object(alerts.smtplib, "SMTP") as smtp:
            _quiet(alerts.notify_email, "AAPL", "rsi14 > 70", CTX)
            smtp.assert_called_once()
            # context-manager handle → login + send_message called
            handle = smtp.return_value.__enter__.return_value
            handle.login.assert_called_once()
            handle.send_message.assert_called_once()

    def test_email_failure_is_caught(self):
        with self._cfg(**EMAIL_CFG), \
             mock.patch.object(alerts.smtplib, "SMTP",
                               side_effect=OSError("connection refused")):
            # must not raise — failure is logged
            _quiet(alerts.notify_email, "AAPL", "c", CTX)

    # ── pushover ──────────────────────────────────────────────────────────

    def test_pushover_missing_config_skips(self):
        with self._cfg(), mock.patch("requests.post") as post:
            _quiet(alerts.notify_pushover, "AAPL", "c", CTX)
            post.assert_not_called()

    def test_pushover_success(self):
        cfg = {"PUSHOVER_USER_KEY": "u", "PUSHOVER_APP_TOKEN": "t"}
        resp = mock.Mock(status_code=200, text="ok")
        with self._cfg(**cfg), mock.patch("requests.post", return_value=resp) as post:
            _quiet(alerts.notify_pushover, "AAPL", "c", CTX)
            post.assert_called_once()

    def test_pushover_non_200(self):
        cfg = {"PUSHOVER_USER_KEY": "u", "PUSHOVER_APP_TOKEN": "t"}
        resp = mock.Mock(status_code=500, text="server error")
        with self._cfg(**cfg), mock.patch("requests.post", return_value=resp):
            _quiet(alerts.notify_pushover, "AAPL", "c", CTX)

    def test_pushover_request_exception(self):
        cfg = {"PUSHOVER_USER_KEY": "u", "PUSHOVER_APP_TOKEN": "t"}
        with self._cfg(**cfg), \
             mock.patch("requests.post", side_effect=Exception("timeout")):
            _quiet(alerts.notify_pushover, "AAPL", "c", CTX)

    # ── slack ─────────────────────────────────────────────────────────────

    def test_slack_missing_config_skips(self):
        with self._cfg(), mock.patch("requests.post") as post:
            _quiet(alerts.notify_slack, "AAPL", "c", CTX)
            post.assert_not_called()

    def test_slack_success(self):
        resp = mock.Mock(status_code=200, text="ok")
        with self._cfg(SLACK_WEBHOOK_URL="https://hooks.slack/x"), \
             mock.patch("requests.post", return_value=resp) as post:
            _quiet(alerts.notify_slack, "AAPL", "c", CTX)
            post.assert_called_once()

    def test_slack_non_200(self):
        resp = mock.Mock(status_code=404, text="not found")
        with self._cfg(SLACK_WEBHOOK_URL="https://hooks.slack/x"), \
             mock.patch("requests.post", return_value=resp):
            _quiet(alerts.notify_slack, "AAPL", "c", CTX)

    def test_slack_request_exception(self):
        with self._cfg(SLACK_WEBHOOK_URL="https://hooks.slack/x"), \
             mock.patch("requests.post", side_effect=Exception("boom")):
            _quiet(alerts.notify_slack, "AAPL", "c", CTX)


if __name__ == "__main__":
    unittest.main()
