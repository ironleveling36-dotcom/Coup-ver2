"""
notifications.py - Outbound notifications, broadcasts & background jobs.

  • notify_user()          - safe single-user push (respects notify pref)
  • broadcast()            - send to a segment with a live progress bar
  • new_coupon_alert()     - fan-out when admin adds/refills a category
  • low_stock_job()        - JobQueue callback: alert staff on low stock
  • notify_admins()        - push an internal alert to all staff / ADMIN_CHAT_ID
"""

import asyncio
import logging

from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

import config
import messages
from database import Database
from utils import animations, format_currency

logger = logging.getLogger(__name__)


async def notify_user(bot, user_id: int, text: str, *, respect_pref: bool = True,
                      db: Database | None = None) -> bool:
    """Send a message to one user. Never raises. Returns True on success."""
    try:
        if respect_pref:
            db = db or await Database.get_instance()
            u = await db.get_user(user_id)
            if u and u.get("notify") is False:
                return False
        await bot.send_message(user_id, text, parse_mode=ParseMode.MARKDOWN)
        return True
    except (Forbidden, TelegramError):
        return False
    except Exception:
        logger.exception("notify_user failed for %s", user_id)
        return False


async def notify_admins(bot, text: str):
    """Alert all staff (DB admins + env super admins + ADMIN_CHAT_ID)."""
    targets = set(config.SUPER_ADMIN_IDS) | set(config.SUPPORT_IDS)
    try:
        db = await Database.get_instance()
        for a in await db.list_admins():
            targets.add(a["user_id"])
    except Exception:
        pass
    if config.ADMIN_CHAT_ID:
        try:
            targets.add(int(config.ADMIN_CHAT_ID))
        except (ValueError, TypeError):
            pass
    for uid in targets:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


async def broadcast(bot, user_ids: list[int], text: str, progress_msg=None) -> dict:
    """
    Send `text` to every id. Updates `progress_msg` with a live progress bar.
    Returns dict(sent, failed, total).
    """
    sent = failed = 0
    total = len(user_ids)
    for i, uid in enumerate(user_ids, 1):
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1
        # Telegram-friendly pacing (~25 msgs/sec max recommended).
        if i % 25 == 0:
            await asyncio.sleep(1)
            if progress_msg is not None:
                bar = animations.progress_bar(i, total)
                try:
                    await progress_msg.edit_text(
                        f"📤 *Broadcasting…*\n\n{bar}\n\n✅ {sent}  •  ⚠️ {failed}",
                        parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
    return {"sent": sent, "failed": failed, "total": total}


async def fanout_new_coupon(bot, name: str, price: float):
    """Notify opted-in users that a new/refilled coupon is available."""
    db = await Database.get_instance()
    ids = await db.notify_user_ids()
    text = messages.new_coupon_alert(name, price)
    sent = 0
    for i, uid in enumerate(ids, 1):
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            pass
        if i % 25 == 0:
            await asyncio.sleep(1)
    logger.info("New-coupon alert sent to %s/%s users", sent, len(ids))
    return sent


async def low_stock_job(ctx):
    """JobQueue callback: alert staff when categories drop to/under threshold."""
    try:
        db = await Database.get_instance()
        threshold = int(await db.get_setting(
            "low_stock_threshold", config.LOW_STOCK_THRESHOLD_DEFAULT))
        low = await db.low_stock_categories(threshold)
        if not low:
            return
        lines = ["🚨 *Low Stock Alert*\n"]
        for c in low:
            lines.append(f"• *{c['name']}* — only *{c['_stock']}* left "
                         f"({format_currency(c['price'])})")
            await db.mark_low_stock_alerted(c["id"])
        lines.append("\nTop up stock from *Manage Coupons* → category → *Add Stock*.")
        await notify_admins(ctx.bot, "\n".join(lines))
    except Exception:
        logger.exception("low_stock_job failed")
