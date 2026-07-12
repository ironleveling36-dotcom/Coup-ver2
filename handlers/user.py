"""
handlers/user.py - User-facing handlers: start (+referral capture), browse,
wallet view, transaction history, my orders, referral home, notifications.

Every callback is wrapped so a failure can never crash the bot ("error-free").
"""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

import config
import keyboards
import messages
from database import Database
from utils import (
    is_admin, format_currency, fmt_dt, generate_ref_code, tiers_summary,
)

logger = logging.getLogger(__name__)


# ── safety wrapper ────────────────────────────────────────────────────────────
def safe(func):
    """Wrap a handler so any exception is caught and shown gently."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        try:
            return await func(update, ctx)
        except TelegramError as e:
            logger.warning("Telegram error in %s: %s", func.__name__, e)
        except Exception:
            logger.exception("Handler %s crashed", func.__name__)
            try:
                if update.callback_query:
                    await update.callback_query.answer(
                        "⚠️ Something went wrong. Please try again.", show_alert=True)
                elif update.message:
                    await update.message.reply_text(
                        "⚠️ Something went wrong. Please try /start again.")
            except Exception:
                pass
    return wrapper


async def _guard(update: Update, db: Database) -> bool:
    """Return True if the user is allowed to proceed."""
    user = update.effective_user
    if await db.is_staff(user.id):
        return True
    target = update.callback_query.message if update.callback_query else update.message
    if await db.is_banned(user.id):
        await target.reply_text(messages.banned(), parse_mode=ParseMode.MARKDOWN)
        return False
    if await db.get_setting("maintenance") == "true":
        await target.reply_text(messages.maintenance(), parse_mode=ParseMode.MARKDOWN)
        return False
    return True


def _bot_username(ctx) -> str:
    return config.BOT_USERNAME or (ctx.bot.username if ctx.bot and ctx.bot.username else "")


# ══════════════════════════════════════════════════════════════════════════
# START (+ referral capture)
# ══════════════════════════════════════════════════════════════════════════
@safe
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db = await Database.get_instance()
    rec = await db.upsert_user(user.id, user.username or "", user.full_name or "")

    # Referral deep-link: /start ref_<code>
    if ctx.args:
        await _try_capture_referral(update, ctx, db, ctx.args[0])

    if not await _guard(update, db):
        return

    await update.message.reply_text(
        messages.welcome(user.first_name, rec.get("wallet_balance", 0.0)),
        reply_markup=keyboards.main_menu_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _try_capture_referral(update, ctx, db: Database, payload: str):
    cfg = await db.get_referral_config()
    if not cfg["enabled"]:
        return
    if not payload.startswith("ref_"):
        return
    code = payload[4:]
    # Resolve code -> referrer user id. We use base36 of the user id as code.
    referrer_id = None
    try:
        referrer_id = int(code, 36)
    except ValueError:
        return
    me = update.effective_user.id
    if referrer_id == me:
        return
    referrer = await db.get_user(referrer_id)
    if not referrer:
        return  # unknown referrer
    linked = await db.set_referrer(me, referrer_id)
    if not linked:
        return
    # Optional welcome bonus for the new user.
    if cfg["welcome_bonus"] > 0:
        bal = await db.credit_wallet(me, cfg["welcome_bonus"], ttype="referral",
                                     ref=f"welcome:{referrer_id}",
                                     note="Referral welcome bonus")
        try:
            await update.message.reply_text(
                messages.referral_reward(cfg["welcome_bonus"], "welcome", bal),
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
# MAIN MENU / WALLET
# ══════════════════════════════════════════════════════════════════════════
@safe
async def cbq_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    balance = await db.get_balance(query.from_user.id)
    await query.edit_message_text(
        messages.welcome(query.from_user.first_name, balance),
        reply_markup=keyboards.main_menu_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


@safe
async def cbq_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    u = await db.get_user(query.from_user.id) or await db.upsert_user(
        query.from_user.id, query.from_user.username or "", query.from_user.full_name or "")
    await query.edit_message_text(
        messages.wallet_overview(
            u.get("wallet_balance", 0.0),
            u.get("total_recharged", 0.0),
            u.get("total_spent", 0.0),
            u.get("ref_earnings", 0.0),
        ),
        reply_markup=keyboards.wallet_kb(),
        parse_mode=ParseMode.MARKDOWN,
    )


@safe
async def cbq_txn_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    txns = await db.get_transactions(query.from_user.id, limit=15)

    if not txns:
        await query.edit_message_text(
            "📜 *Transaction History*\n\nNo transactions yet.",
            reply_markup=keyboards.wallet_kb(), parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["📜 *Transaction History*\n"]
    icons = {"recharge": "⬆️", "purchase": "🛒", "admin_adjust": "🛠️",
             "refund": "↩️", "referral": "🎁"}
    for t in txns:
        sign = "+" if t["amount"] >= 0 else "−"
        icon = icons.get(t["type"], "•")
        lines.append(
            f"{icon} {sign}{format_currency(abs(t['amount']))} • "
            f"{t['type']} • {fmt_dt(t['created_at'])}")
    await query.edit_message_text(
        "\n".join(lines), reply_markup=keyboards.wallet_kb(),
        parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════
# BROWSE
# ══════════════════════════════════════════════════════════════════════════
@safe
async def cbq_browse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    if not await _guard(update, db):
        return
    categories = await db.get_categories(active_only=True)
    if not categories:
        await query.edit_message_text(
            messages.no_categories(), reply_markup=keyboards.back_to_main_kb(),
            parse_mode=ParseMode.MARKDOWN)
        return
    stock_map = {c["id"]: await db.stock_count(c["id"]) for c in categories}
    await query.edit_message_text(
        "🛍️ *Available Coupons*\n\nSelect a category to continue:",
        reply_markup=keyboards.categories_kb(categories, stock_map),
        parse_mode=ParseMode.MARKDOWN)


@safe
async def cbq_select_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[1])
    db = await Database.get_instance()

    cat = await db.get_category(cat_id)
    if not cat or not cat.get("is_active", True):
        await query.answer("Category not available!", show_alert=True)
        return

    stock = await db.stock_count(cat_id)
    balance = await db.get_balance(query.from_user.id)
    if stock == 0:
        await query.edit_message_text(
            messages.out_of_stock_msg(cat["name"]),
            reply_markup=keyboards.back_to_main_kb(), parse_mode=ParseMode.MARKDOWN)
        return

    tiers = await db.get_discount_tiers()
    await query.edit_message_text(
        messages.category_detail(cat["name"], cat["price"], stock, balance,
                                 tiers_summary(tiers)),
        reply_markup=keyboards.quantity_kb(cat_id), parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════
# REFERRAL
# ══════════════════════════════════════════════════════════════════════════
@safe
async def cbq_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    cfg = await db.get_referral_config()
    uid = query.from_user.id
    stats = await db.referral_stats(uid)

    if not cfg["enabled"]:
        await query.edit_message_text(
            "🎁 *Referral Program*\n\nThe referral program is currently disabled. "
            "Check back soon!", reply_markup=keyboards.back_to_main_kb(),
            parse_mode=ParseMode.MARKDOWN)
        return

    username = _bot_username(ctx)
    code = generate_ref_code(uid)
    ref_link = f"https://t.me/{username}?start=ref_{code}" if username else \
               f"Your code: ref_{code}"
    from urllib.parse import quote
    share_text = quote(f"Join {config.BOT_NAME} and get instant coupons! {ref_link}")
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={share_text}"

    await query.edit_message_text(
        messages.referral_home(ref_link, stats["count"], stats["earnings"], cfg),
        reply_markup=keyboards.referral_kb(share_url),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


@safe
async def cbq_ref_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    top = await db.referral_leaderboard(limit=10)
    lines = ["🏆 *Top Referrers*\n"]
    medals = ["🥇", "🥈", "🥉"]
    if not top:
        lines.append("No referrals yet. Be the first! 🚀")
    for i, u in enumerate(top):
        badge = medals[i] if i < 3 else f"{i+1}."
        name = u.get("full_name") or (f"@{u['username']}" if u.get("username") else f"User {u['user_id']}")
        lines.append(f"{badge} {name} — {u.get('ref_count',0)} invites • "
                     f"{format_currency(u.get('ref_earnings',0))}")
    await query.edit_message_text(
        "\n".join(lines), reply_markup=keyboards.back_to_main_kb(),
        parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════
@safe
async def cbq_notify_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    u = await db.get_user(query.from_user.id) or {}
    enabled = u.get("notify", True)
    await query.edit_message_text(
        "🔔 *Notification Settings*\n\n"
        "Get alerts for new coupons, special offers, wallet credits and order "
        "updates.\n\nWallet & order alerts are always delivered.",
        reply_markup=keyboards.notify_kb(enabled), parse_mode=ParseMode.MARKDOWN)


@safe
async def cbq_notify_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    db = await Database.get_instance()
    u = await db.get_user(query.from_user.id) or {}
    new = not u.get("notify", True)
    await db.set_notify(query.from_user.id, new)
    await query.answer(f"Notifications {'ON' if new else 'OFF'}")
    await query.edit_message_text(
        "🔔 *Notification Settings*\n\n"
        "Get alerts for new coupons, special offers, wallet credits and order "
        "updates.\n\nWallet & order alerts are always delivered.",
        reply_markup=keyboards.notify_kb(new), parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════
# HELP / ORDERS
# ══════════════════════════════════════════════════════════════════════════
@safe
async def cbq_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        messages.help_msg(), reply_markup=keyboards.back_to_main_kb(),
        parse_mode=ParseMode.MARKDOWN)


@safe
async def cbq_my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    orders = await db.get_user_orders(query.from_user.id, limit=15)
    if not orders:
        await query.edit_message_text(
            "📦 *No orders found.*\n\nYou haven't purchased anything yet!",
            reply_markup=keyboards.back_to_main_kb(), parse_mode=ParseMode.MARKDOWN)
        return
    await query.edit_message_text(
        "📦 *Your Recent Orders:*\n\nSelect an order to view its coupon codes.",
        reply_markup=keyboards.my_orders_kb(orders), parse_mode=ParseMode.MARKDOWN)


@safe
async def cbq_view_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = query.data.split("vieworder_")[1]
    db = await Database.get_instance()
    order = await db.get_order(order_id)
    if not order or order["user_id"] != query.from_user.id:
        await query.answer("Order not found!", show_alert=True)
        return
    items = order.get("items", [])
    codes = "\n".join(f"{i}. `{c}`" for i, c in enumerate(items, 1)) or "_No codes stored_"
    text = (
        f"📋 *Order Details*\n\n"
        f"Order ID: `{order['order_id']}`\n"
        f"Category: {order.get('category_name', 'N/A')}\n"
        f"Quantity: {order['quantity']}\n"
        f"Amount: {format_currency(order['amount'])}\n"
        f"Status: {order['status'].upper()}\n"
        f"Date: {fmt_dt(order.get('created_at'))}\n\n"
        f"🎁 *Coupon Codes:*\n{codes}")
    await query.edit_message_text(
        text, reply_markup=keyboards.back_to_main_kb(), parse_mode=ParseMode.MARKDOWN)


def register_user_handlers(app):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cbq_main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cbq_wallet, pattern="^wallet$"))
    app.add_handler(CallbackQueryHandler(cbq_txn_history, pattern="^txn_history$"))
    app.add_handler(CallbackQueryHandler(cbq_browse, pattern="^browse$"))
    app.add_handler(CallbackQueryHandler(cbq_select_category, pattern=r"^cat_\d+$"))
    app.add_handler(CallbackQueryHandler(cbq_referral, pattern="^referral$"))
    app.add_handler(CallbackQueryHandler(cbq_ref_leaderboard, pattern="^ref_leaderboard$"))
    app.add_handler(CallbackQueryHandler(cbq_notify_menu, pattern="^notify_menu$"))
    app.add_handler(CallbackQueryHandler(cbq_notify_toggle, pattern="^notify_toggle$"))
    app.add_handler(CallbackQueryHandler(cbq_help, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(cbq_my_orders, pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(cbq_view_order, pattern=r"^vieworder_ORD-\w+-\w+$"))
