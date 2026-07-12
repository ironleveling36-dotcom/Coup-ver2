"""
gmail_checker.py - Reads payment-alert emails from Gmail via IMAP and checks
whether a given UPI transaction ID (UTR/RRN) appears in any recent message.
Also extracts the amount so the bot can credit the exact value to the wallet.

Runs blocking IMAP calls inside a thread executor (see handlers/payment.py) so
the async event loop is never blocked -> keeps the bot fast under load.

No third-party deps -- uses Python's built-in imaplib + email.
"""

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header

import config

logger = logging.getLogger(__name__)

# Matches "Rs. 100", "Rs 100.00", "INR 1,000", "₹100", "Rs.100/-", etc.
_AMOUNT_RE = re.compile(
    r"(?:Rs\.?|INR|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE
)


def _decode(value):
    if not value:
        return ""
    parts = decode_header(value)
    out = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="ignore")
        else:
            out += text
    return out


def _body_text(msg) -> str:
    """Flatten a possibly-multipart email into plain text."""
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    chunks.append(payload.decode(charset, errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            chunks.append(payload.decode(charset, errors="ignore"))
    text = "\n".join(chunks)
    # strip basic HTML tags so "₹100" inside markup is still matchable
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def _extract_amount(text: str):
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def find_transaction(txn_id: str) -> dict:
    """
    Returns dict(found=bool, amount=float|None, subject=str, date=str).
    `found` is True only if a recent email's text contains the exact txn_id.

    Raises on connection/login errors so the caller can show a friendly retry.
    """
    txn_id = (txn_id or "").strip()
    result = {"found": False, "amount": None, "subject": "", "date": ""}
    if not txn_id:
        return result
    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD):
        logger.warning("Gmail credentials not configured; cannot auto-verify.")
        return result

    imap = imaplib.IMAP4_SSL(config.IMAP_HOST)
    try:
        imap.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        imap.select("INBOX")

        since = (
            datetime.now(timezone.utc) - timedelta(hours=config.EMAIL_LOOKBACK_HOURS)
        ).strftime("%d-%b-%Y")

        criteria = ["SINCE", since]
        if config.SENDER_FILTER:
            criteria += ["FROM", config.SENDER_FILTER]

        status, data = imap.search(None, *criteria)
        if status != "OK" or not data or not data[0]:
            return result

        ids = data[0].split()
        # newest first, cap how many we inspect for speed
        for eid in reversed(ids[-100:]):
            status, msg_data = imap.fetch(eid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            text = _body_text(msg)
            if txn_id and txn_id in text:
                result["found"] = True
                result["amount"] = _extract_amount(text)
                result["subject"] = _decode(msg.get("Subject"))
                result["date"] = msg.get("Date", "")
                break
        return result
    finally:
        try:
            imap.logout()
        except Exception:
            pass