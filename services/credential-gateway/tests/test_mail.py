"""Unit tests for the read-only IMAP connector (stdlib unittest, no server)."""
from __future__ import annotations

import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mail  # noqa: E402


def _message(with_attachment: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Billing <billing@example.com>"
    msg["To"] = "taskforge@example.com"
    msg["Subject"] = "Invoice 42"
    msg["Message-ID"] = "<invoice-42@example.com>"
    msg["Date"] = "Mon, 03 Nov 2026 10:00:00 +0000"
    msg.set_content("Hello from the invoice mailbox.")
    if with_attachment:
        msg.add_attachment(
            b"%PDF-1.4 fake invoice",
            maintype="application",
            subtype="pdf",
            filename="invoice-42.pdf",
        )
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.select_calls = []
        self.uid_calls = []
        self.logged_out = False

    def login(self, username, password):
        return "OK", [b"logged in"]

    def select(self, folder, readonly=False):
        self.select_calls.append((folder, readonly))
        return "OK", [b"1"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Archive"']

    def uid(self, command, *args):
        self.uid_calls.append((command, args))
        if command == "search":
            return "OK", [b"1 2"]
        if command == "fetch":
            uid = str(args[0])
            criteria = str(args[1]) if len(args) > 1 else ""
            if "HEADER.FIELDS" in criteria:
                header = _message(False).split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                meta = f"1 (FLAGS (\\Seen) RFC822.SIZE 200 BODY[HEADER] {len(header)}".encode()
                return "OK", [(meta, header), b")"]
            meta = f"1 (FLAGS (\\Seen) RFC822.SIZE {len(self.raw)} BODY[] {{{len(self.raw)}}}".encode()
            return "OK", [(meta, self.raw), b")"]
        return "OK", [b""]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"bye"]

    def shutdown(self):
        pass


class MailConnectorTests(unittest.TestCase):
    def setUp(self):
        self.cred = {
            "kind": "imap",
            "host": "mail.example.com",
            "port": 993,
            "tls": True,
            "username": "taskforge@example.com",
            "password": "secret",
            "allowed_folders": ["INBOX"],
        }

    def _run_with(self, raw: bytes, fn):
        fake = FakeIMAP(raw)
        with patch.object(mail.imaplib, "IMAP4_SSL", return_value=fake):
            result = fn()
        return result, fake

    def test_folders_only_allowed(self):
        folders, _ = self._run_with(_message(), lambda: mail.list_folders(self.cred))
        self.assertEqual(folders, ["INBOX"])

    def test_list_messages_summary(self):
        messages, fake = self._run_with(
            _message(), lambda: mail.list_messages(self.cred, limit=2)
        )
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["uid"], "2")
        self.assertEqual(messages[0]["subject"], "Invoice 42")
        self.assertTrue(all(call[1] is True for call in fake.select_calls))

    def test_read_message_and_attachment(self):
        raw = _message(with_attachment=True)
        message, fake = self._run_with(raw, lambda: mail.read_message(self.cred, "2"))
        self.assertIn("Hello from the invoice mailbox", message["text"])
        self.assertEqual(len(message["attachments"]), 1)
        self.assertEqual(message["attachments"][0]["filename"], "invoice-42.pdf")
        part = message["attachments"][0]["part"]

        attachment, _ = self._run_with(
            raw, lambda: mail.download_attachment(self.cred, "2", part)
        )
        self.assertEqual(attachment["filename"], "invoice-42.pdf")
        self.assertIn(b"fake invoice", attachment["content"])
        self.assertTrue(all(call[1] is True for call in fake.select_calls))

    def test_folder_allowlist_rejects_other_folder(self):
        with self.assertRaises(mail.MailError) as ctx:
            self._run_with(_message(), lambda: mail.list_messages(self.cred, folder="Archive"))
        self.assertEqual(ctx.exception.code, "MAIL_FOLDER_DENIED")

    def test_invalid_uid(self):
        with self.assertRaises(mail.MailError) as ctx:
            mail.read_message(self.cred, "1; DROP")
        self.assertEqual(ctx.exception.code, "MAIL_BAD_UID")

    def test_search_criteria_rejects_bad_date(self):
        with self.assertRaises(mail.MailError) as ctx:
            mail.list_messages(self.cred, since="not-a-date")
        self.assertEqual(ctx.exception.code, "MAIL_BAD_QUERY")


if __name__ == "__main__":
    unittest.main()
