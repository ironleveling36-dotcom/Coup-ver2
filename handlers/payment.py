"""
handlers/payment.py - Wallet recharge (QR + UPI, auto Gmail verification) and
coupon purchase paid from the wallet (with bulk discounts).

Recharge flow (fixed + upgraded):
  user taps "Recharge"
   -> bot shows a UPI *QR code* AND the UPI ID together
   -> user pays, taps "✅ I've Paid", then sends the UPI Transaction ID / UTR
   -> bot verifies it against Gmail bank-alert emails (anti-replay + cooldown)
   -> on success the QR + payment message DISAPPEAR and the wallet is credited
      with the exact amount from the email
   -> referral commission / signup bonus is paid to the referrer automatically.

Purchase flow:
  pick category + quantity -> bulk discount auto-applied -> confirm -> atomic
  wallet debit -> stock reserved -> order recorded -> codes delivered instantly
  (auto-refund if stock vanishes in a race).
"""

import asyncio
import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import keyboards
import messages
from database import Database
from gmail_checker import find_transaction
from notifications import notify_user, notify_admins
from utils import (
    is_admin, generate_order_id, format_currency, format_delivery,
    valid_txn_id, safe_int, calc_pricing, animations,
)

logger = logging.getLogger(__name__)

# Conversation states
WAIT_PAID = 0
RECHARGE_TXN = 1
CUSTOM_QTY = 2

# Per-user cooldown between txn verification attempts (anti-abuse).
_last_attempt: dict[int, float] = {}


# ══════════════════════════════════════════════════════════════════════════
# RECHARGE — show QR + UPI
# ══════════════════════════════════════════════════════════════════════════
async def cbq_recharge_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    upi = await db.get_setting("upi_id", config.UPI_ID) or config.UPI_ID
    payee = await db.get_setting("payee_name", config.PAYEE_NAME) or config.PAYEE_NAME

    if not upi:
        await query.edit_message_text(
            "⚠️ Recharge is not configured yet. Please contact the admin.",
            reply_markup=keyboards.wallet_kb(), parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    caption = messages.recharge_instructions(upi, payee)

    # Try to show a scannable UPI QR alongside the UPI id.
    qr_buf = None
    if config.GENERATE_DYNAMIC_QR:
        try:
            from utils import make_upi_qr
            qr_buf = make_upi_qr(upi, payee, note=f"{config.BOT_NAME} wallet")
        except Exception:
            qr_buf = None

    # Remove the previous (text) wallet menu so the chat stays clean.
    try:
        await query.message.delete()
    except TelegramError:
        pass

    ctx.user_data.pop("recharge_msg_id", None)
    if qr_buf is not None:
        sent = await ctx.bot.send_photo(
            chat_id=query.message.chat_id, photo=qr_buf, caption=caption,
            reply_markup=keyboards.paid_kb(), parse_mode=ParseMode.MARKDOWN)
        ctx.user_data["recharge_is_photo"] = True
    else:
        sent = await ctx.bot.send_message(
            chat_id=query.message.chat_id, text=caption,
            reply_markup=keyboards.paid_kb(), parse_mode=ParseMode.MARKDOWN)
        ctx.user_data["recharge_is_photo"] = False

    ctx.user_data["recharge_msg_id"] = sent.message_id
    ctx.user_data["recharge_chat_id"] = sent.chat_id
    return WAIT_PAID


async def cbq_ive_paid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Great! Now send your Transaction ID.")
    prompt = messages.enter_txn_prompt()
    try:
        if ctx.user_data.get("recharge_is_photo"):
            await query.edit_message_caption(
                caption=prompt, reply_markup=keyboards.recharge_cancel_kb(),
                parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(
                prompt, reply_markup=keyboards.recharge_cancel_kb(),
                parse_mode=ParseMode.MARKDOWN)
    except TelegramError:
        pass
    return RECHARGE_TXN


async def _cleanup_recharge_msgs(ctx: ContextTypes.DEFAULT_TYPE):
    """Delete the QR / payment prompt message(s) so they disappear on success."""
    chat_id = ctx.user_data.get("recharge_chat_id")
    mid = ctx.user_data.get("recharge_msg_id")
    if chat_id and mid:
        try:
            await ctx.bot.delete_message(chat_id, mid)
        except TelegramError:
            pass
    ctx.user_data.pop("recharge_msg_id", None)
    ctx.user_data.pop("recharge_chat_id", None)
    ctx.user_data.pop("recharge_is_photo", None)


async def receive_txn_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    txn_id = (update.message.text or "").strip().replace(" ", "")
    db = await Database.get_instance()

    # ── cooldown (anti-spam on the expensive IMAP path) ──
    now = time.monotonic()
    last = _last_attempt.get(user.id, 0)
    if now - last < config.TXN_ATTEMPT_COOLDOWN:
        wait = int(config.TXN_ATTEMPT_COOLDOWN - (now - last)) + 1
        await update.message.reply_text(
            f"⏳ Please wait {wait}s before trying another Transaction ID.")
        return RECHARGE_TXN
    _last_attempt[user.id] = now

    if not valid_txn_id(txn_id):
        await update.message.reply_text(
            "❌ That doesn't look like a valid Transaction ID.\n"
            "Send only the UTR / reference number (8–40 letters or digits).",
            reply_markup=keyboards.recharge_cancel_kb())
        return RECHARGE_TXN

    # ── anti-replay ──
    if await db.is_txn_used(txn_id):
        await update.message.reply_text(
            "⚠️ This Transaction ID has already been used. "
            "Each payment can only be credited once.",
            reply_markup=keyboards.wallet_kb())
        await notify_admins(ctx.bot,
            f"🔁 Reused UTR attempt `{txn_id}` by @{user.username or user.id}")
        return ConversationHandler.END

    status_msg = await update.message.reply_text("🔎 Verifying your payment…")

    # Run blocking IMAP in a thread with an animated spinner on the status msg.
    try:
        result = await animations.run_with_spinner(
            status_msg, "Verifying your payment…",
            asyncio.to_thread(find_transaction, txn_id))
    except Exception as e:
        logger.exception("Gmail check failed")
        await status_msg.edit_text(
            "⚠️ Couldn't reach the verification system right now. "
            "Please try again in a minute.", reply_markup=keyboards.wallet_kb())
        await notify_admins(ctx.bot, f"❗ Gmail check error: {e}")
        return ConversationHandler.END

    if not result["found"]:
        fails = await db.record_failed_txn(user.id)
        if fails >= config.FRAUD_FAILED_TXN_LIMIT:
            await db.flag_user(user.id, reason=f"{fails} failed txn attempts")
            await notify_admins(ctx.bot,
                f"🚨 User @{user.username or user.id} flagged: {fails} failed "
                "verification attempts.")
        await status_msg.edit_text(
            "❌ I couldn't find a payment with that Transaction ID yet.\n\n"
            "• Bank emails can take 1–2 minutes — wait and resend.\n"
            "• Double-check the UTR is correct.\n"
            "• Make sure you paid the correct UPI ID.",
            reply_markup=keyboards.recharge_cancel_kb())
        return RECHARGE_TXN

    amount = result.get("amount")
    if not amount or amount <= 0:
        await status_msg.edit_text(
            "⚠️ Found the payment email, but I couldn't read the amount. "
            "Please contact the admin to credit it manually.",
            reply_markup=keyboards.wallet_kb())
        await notify_admins(ctx.bot,
            f"⚠️ Amount unreadable for UTR `{txn_id}` from @{user.username or user.id}. "
            "Manual credit needed.")
        return ConversationHandler.END

    # Mark used FIRST (atomic unique index) to prevent double-credit on races.
    if not await db.mark_txn_used(txn_id, user.id, amount):
        await status_msg.edit_text(
            "⚠️ This Transaction ID has already been credited.",
            reply_markup=keyboards.wallet_kb())
        return ConversationHandler.END

    new_balance = await db.credit_wallet(
        user.id, amount, ttype="recharge", ref=txn_id, note="Auto Gmail verification")
    await db.reset_failed_txn(user.id)

    # The QR / payment message disappears on success.
    await _cleanup_recharge_msgs(ctx)

    await animations.celebrate(
        status_msg, messages.recharge_success(amount, new_balance),
        final_markup=keyboards.wallet_kb())

    await notify_admins(ctx.bot,
        f"✅ Auto-recharge {format_currency(amount)} | UTR `{txn_id}` | "
        f"@{user.username or user.id} | New balance {format_currency(new_balance)}")

    # ── referral rewards on recharge ──
    try:
        await _reward_referrer(ctx, db, user, amount)
    except Exception:
        logger.exception("Referral reward failed")

    return ConversationHandler.END


async def _reward_referrer(ctx, db: Database, user, recharge_amount: float):
    cfg = await db.get_referral_config()
    if not cfg["enabled"]:
        return
    referrer_id = await db.get_referrer(user.id)
    if not referrer_id:
        return

    urec = await db.get_user(user.id) or {}
    first_time = not urec.get("first_recharge_done")
    if first_time:
        await db.db.users.update_one(
            {"user_id": user.id}, {"$set": {"first_recharge_done": True}})

    total_reward = 0.0
    notes = []
    # one-time signup bonus
    if first_time and cfg["signup_bonus"] > 0 and await db.mark_signup_rewarded(user.id):
        total_reward += cfg["signup_bonus"]
        notes.append("signup")
    # lifetime commission on every recharge
    if cfg["commission_pct"] > 0:
        commission = round(recharge_amount * cfg["commission_pct"] / 100.0, 2)
        if commission > 0:
            total_reward += commission
            notes.append("commission")

    if total_reward <= 0:
        return
    bal = await db.credit_wallet(
        referrer_id, total_reward, ttype="referral",
        ref=f"ref:{user.id}", note="Referral reward: " + "+".join(notes))
    kind = "signup" if "signup" in notes and "commission" not in notes else "commission"
    await notify_user(ctx.bot, referrer_id,
                      messages.referral_reward(total_reward, kind, bal),
                      respect_pref=False, db=db)


async def cancel_recharge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer("Cancelled.")
    await _cleanup_recharge_msgs(ctx)
    try:
        if query:
            await ctx.bot.send_message(
                query.message.chat_id, "💼 *My Wallet*",
                reply_markup=keyboards.wallet_kb(), parse_mode=ParseMode.MARKDOWN)
    except TelegramError:
        pass
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════
# PURCHASE FROM WALLET (with bulk discount)
# ══════════════════════════════════════════════════════════════════════════
async def cbq_quantity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Preset quantity buttons: qty_{cat_id}_{qty}"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    cat_id, qty = int(parts[1]), int(parts[2])
    await _show_confirm(ctx, query, cat_id, qty)


async def cbq_custom_qty_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("qtycustom_")[1])
    ctx.user_data["pending_cat_id"] = cat_id
    await query.edit_message_text(
        f"✏️ *Enter Custom Quantity*\n\nType how many you want "
        f"(1–{config.MAX_BULK_QTY}).", parse_mode=ParseMode.MARKDOWN)
    return CUSTOM_QTY


async def receive_custom_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qty = safe_int(update.message.text)
    cat_id = ctx.user_data.get("pending_cat_id")
    if cat_id is None:
        await update.message.reply_text("Session expired. Please start again.",
                                        reply_markup=keyboards.back_to_main_kb())
        return ConversationHandler.END
    if qty is None or qty < 1 or qty > config.MAX_BULK_QTY:
        await update.message.reply_text(
            f"❌ Enter a valid number between 1 and {config.MAX_BULK_QTY}.")
        return CUSTOM_QTY
    await _show_confirm(ctx, update.message, cat_id, qty, is_message=True)
    return ConversationHandler.END


async def _show_confirm(ctx, target, cat_id: int, qty: int, is_message: bool = False):
    db = await Database.get_instance()
    cat = await db.get_category(cat_id)
    if not cat:
        text = "Category not found."
        if is_message:
            await target.reply_text(text, reply_markup=keyboards.back_to_main_kb())
        else:
            await target.edit_message_text(text, reply_markup=keyboards.back_to_main_kb())
        return

    user_id = target.chat.id if is_message else target.from_user.id
    stock = await db.stock_count(cat_id)
    balance = await db.get_balance(user_id)
    tiers = await db.get_discount_tiers()
    pricing = calc_pricing(cat["price"], qty, tiers)
    total = pricing["total"]

    if stock < qty:
        text = (f"😔 Only *{stock}* in stock for *{cat['name']}*.\n"
                "Please choose a smaller quantity.")
        kb = keyboards.quantity_kb(cat_id)
    elif balance < total:
        text = messages.insufficient_balance(total, balance)
        kb = keyboards.confirm_purchase_kb(cat_id, qty)
    else:
        disc_line = ""
        if pricing["discount_amount"] > 0:
            disc_line = (f"Subtotal: {format_currency(pricing['subtotal'])}\n"
                         f"Bulk discount ({pricing['discount_pct']:g}%): "
                         f"*−{format_currency(pricing['discount_amount'])}* 🎉\n")
        text = (
            f"🧾 *Confirm Purchase*\n\n"
            f"Item: {cat['name']}\n"
            f"Quantity: {qty}\n"
            f"{disc_line}"
            f"Total: *{format_currency(total)}*\n"
            f"Wallet Balance: *{format_currency(balance)}*\n"
            f"After purchase: *{format_currency(balance - total)}*\n\n"
            "Confirm to pay from your wallet 👇")
        kb = keyboards.confirm_purchase_kb(cat_id, qty)

    if is_message:
        await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await target.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def cbq_confirm_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """buy_{cat_id}_{qty} — finalize purchase paid from wallet."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    cat_id, qty = int(parts[1]), int(parts[2])
    user = query.from_user
    db = await Database.get_instance()

    cat = await db.get_category(cat_id)
    if not cat:
        await query.answer("Category not found!", show_alert=True)
        return

    tiers = await db.get_discount_tiers()
    pricing = calc_pricing(cat["price"], qty, tiers)
    total = pricing["total"]

    stock = await db.stock_count(cat_id)
    if stock < qty:
        await query.edit_message_text(
            f"😔 Not enough stock. Only {stock} left.",
            reply_markup=keyboards.back_to_main_kb(), parse_mode=ParseMode.MARKDOWN)
        return

    # animated "processing"
    try:
        await query.edit_message_text("◐ Processing your order…")
    except TelegramError:
        pass

    # 1) Atomically debit wallet (fails if insufficient)
    new_balance = await db.debit_wallet(
        user.id, total, ttype="purchase", note=f"{qty}x {cat['name']}")
    if new_balance is None:
        balance = await db.get_balance(user.id)
        await query.edit_message_text(
            messages.insufficient_balance(total, balance),
            reply_markup=keyboards.wallet_kb(), parse_mode=ParseMode.MARKDOWN)
        return

    # 2) Reserve stock
    order_id = generate_order_id()
    codes = await db.reserve_stock(cat_id, qty, order_id)
    if not codes:
        await db.credit_wallet(user.id, total, ttype="refund", ref=order_id,
                               note="Auto refund: stock unavailable")
        await query.edit_message_text(
            "😔 Sorry, the stock just sold out. Your wallet was *not* charged.",
            reply_markup=keyboards.back_to_main_kb(), parse_mode=ParseMode.MARKDOWN)
        return

    # 3) Record order
    await db.create_order({
        "order_id": order_id, "user_id": user.id, "username": user.username or "",
        "category_id": cat_id, "category_name": cat["name"], "quantity": qty,
        "amount": total, "discount": pricing["discount_amount"],
        "status": "completed", "items": codes, "ref": order_id,
    })
    await db.db.transactions.update_one(
        {"user_id": user.id, "type": "purchase", "ref": ""},
        {"$set": {"ref": order_id}}, upsert=False)

    # 4) Deliver
    await animations.celebrate(
        query.message,
        messages.purchase_success(cat["name"], qty, total, new_balance,
                                  pricing["discount_amount"]),
        final_markup=keyboards.back_to_main_kb())
    await ctx.bot.send_message(
        chat_id=user.id, text=format_delivery(cat["name"], codes),
        parse_mode=ParseMode.MARKDOWN)
    await notify_admins(ctx.bot,
        f"🛒 Sale: {qty}x {cat['name']} = {format_currency(total)} | "
        f"@{user.username or user.id} | Order {order_id}")

    # low stock check right after a sale
    remaining = await db.stock_count(cat_id)
    threshold = int(await db.get_setting("low_stock_threshold",
                                         config.LOW_STOCK_THRESHOLD_DEFAULT))
    if remaining <= threshold and not cat.get("low_stock_alerted"):
        await db.mark_low_stock_alerted(cat_id)
        await notify_admins(ctx.bot,
            f"🚨 *Low Stock:* {cat['name']} has only {remaining} left.")


def register_payment_handlers(app):
    recharge_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cbq_recharge_start, pattern="^recharge$")],
        states={
            WAIT_PAID: [
                CallbackQueryHandler(cbq_ive_paid, pattern="^ive_paid$"),
                CallbackQueryHandler(cancel_recharge, pattern="^cancel_recharge$"),
            ],
            RECHARGE_TXN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_txn_id),
                CallbackQueryHandler(cancel_recharge, pattern="^cancel_recharge$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_recharge,
                                        pattern="^(cancel_recharge|wallet|main_menu)$")],
        per_chat=True, per_user=True,
    )

    custom_qty_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cbq_custom_qty_start, pattern=r"^qtycustom_\d+$")],
        states={CUSTOM_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_qty)]},
        fallbacks=[CallbackQueryHandler(cbq_quantity, pattern=r"^qty_\d+_\d+$")],
        per_chat=True, per_user=True,
    )

    app.add_handler(recharge_conv)
    app.add_handler(custom_qty_conv)
    app.add_handler(CallbackQueryHandler(cbq_quantity, pattern=r"^qty_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(cbq_confirm_buy, pattern=r"^buy_\d+_\d+$"))
