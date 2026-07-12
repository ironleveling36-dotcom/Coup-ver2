"""
handlers/security.py - Anti-spam, rate limiting & fraud detection.

Implemented as a high-priority TypeHandler (group -10) that inspects EVERY
update before the feature handlers run. If a user exceeds the allowed number of
actions inside the sliding window, the update is dropped with a gentle warning
and ApplicationHandlerStop is raised so no downstream handler fires.

In-memory sliding-window counter -> zero DB latency on the hot path. Repeated
abuse escalates: soft warn -> auto-flag in DB for admin review.
"""

import logging
import time
from collections import defaultdict, deque

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, TypeHandler, ApplicationHandlerStop

import config
import messages
from database import Database
from utils import is_admin

logger = logging.getLogger(__name__)

# user_id -> deque[timestamps]
_hits: dict[int, deque] = defaultdict(deque)
# user_id -> last time we sent them a "slow down" warning (avoid warning spam)
_warned_at: dict[int, float] = {}
# user_id -> how many times they've tripped the limit
_trips: dict[int, int] = defaultdict(int)


def _too_fast(user_id: int) -> bool:
    now = time.monotonic()
    window = config.RATE_WINDOW_SECONDS
    dq = _hits[user_id]
    dq.append(now)
    while dq and now - dq[0] > window:
        dq.popleft()
    return len(dq) > config.RATE_LIMIT_ACTIONS


async def rate_limit_gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    uid = user.id

    # Admins are never rate-limited.
    if is_admin(uid):
        return

    if not _too_fast(uid):
        return

    # Over the limit -> escalate.
    _trips[uid] += 1
    now = time.monotonic()

    # Warn at most once every few seconds.
    if now - _warned_at.get(uid, 0) > 4:
        _warned_at[uid] = now
        try:
            if update.callback_query:
                await update.callback_query.answer(
                    "🐢 Too fast — please wait a moment.", show_alert=False)
            elif update.message:
                await update.message.reply_text(
                    messages.rate_limited(), parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    # Persistent auto-flag for sustained abuse.
    if _trips[uid] == config.FRAUD_FAILED_TXN_LIMIT:
        try:
            db = await Database.get_instance()
            await db.flag_user(uid, reason="Rate-limit abuse (spam)")
            logger.warning("Auto-flagged user %s for spam", uid)
        except Exception:
            logger.exception("Failed to flag spamming user %s", uid)

    raise ApplicationHandlerStop


def register_security_handlers(app):
    # group -10 runs before every feature handler.
    app.add_handler(TypeHandler(Update, rate_limit_gate), group=-10)
