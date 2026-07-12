"""
handlers/admin.py - Full admin control dashboard.
Role-aware routing via @requires_role("permission").
"""

import asyncio
import json
import logging
from io import BytesIO
from functools import wraps

from telegram import Update, InputFile
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import keyboards
from database import Database
from notifications import broadcast, fanout_new_coupon
from utils import (
    env_role, role_can, role_label, safe_int, safe_float, format_currency, fmt_dt,
)

logger = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
(
    ADD_CAT_NAME, ADD_CAT_PRICE,
    EDIT_NAME, EDIT_PRICE,
    ADD_STOCK,
    WALLET_ADD_UID, WALLET_ADD_AMT,
    WALLET_DED_UID, WALLET_DED_AMT,
    WALLET_CHECK_UID,
    BAN_UID, UNBAN_UID,
    BC_MSG,
    SET_UPI, SET_PAYEE, SET_LOW_STOCK,
    REF_SIGNUP, REF_COMMISSION, REF_WELCOME,
    STAFF_ADD_UID, RESTORE_FILE,
) = range(20)


def requires_role(permission: str):
    """Decorator to enforce role permissions on admin commands."""
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id
            db = await Database.get_instance()
            role = await db.effective_role(user_id)
            if not role_can(role, permission):
                if update.callback_query:
                    await update.callback_query.answer("🚫 Permission denied.", show_alert=True)
                else:
                    await update.message.reply_text("🚫 You don't have permission for this.")
                return ConversationHandler.END
            ctx.user_data["_admin_role"] = role
            return await func(update, ctx)
        return wrapper
    return decorator


async def _close(update, ctx):
    if update.callback_query:
        try:
            await update.callback_query.message.delete()
        except TelegramError:
            pass
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════════════════════════════════════
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = await Database.get_instance()
    role = await db.effective_role(update.effective_user.id)
    if role == "user":
        return
    text = f"🛡️ *Admin Control Panel*\nRole: {role_label(role)}\n\nSelect an option below:"
    kb = keyboards.admin_menu_kb(role)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def cbq_admin_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_admin(update, ctx)


# ══════════════════════════════════════════════════════════════════════════
# COUPONS
# ══════════════════════════════════════════════════════════════════════════
@requires_role("coupons")
async def cbq_coupons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    cats = await db.get_categories(active_only=False)
    await query.edit_message_text(
        "🏷️ *Manage Coupons*\n\nSelect a category or add a new one:",
        reply_markup=keyboards.admin_coupons_kb(cats), parse_mode=ParseMode.MARKDOWN)


@requires_role("coupons")
async def cbq_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("adm_cat_")[1])
    db = await Database.get_instance()
    cat = await db.get_category(cat_id)
    if not cat:
        return
    stock = await db.stock_count(cat_id)
    text = (f"🏷️ *{cat['name']}*\n\n💵 Price: {format_currency(cat['price'])}\n"
            f"📦 Stock: {stock}\nStatus: {'Active ✅' if cat.get('is_active') else 'Inactive 🚫'}")
    await query.edit_message_text(text, reply_markup=keyboards.admin_category_kb(cat_id, cat.get("is_active")),
                                  parse_mode=ParseMode.MARKDOWN)


@requires_role("coupons")
async def cbq_add_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("➕ *Add Category*\n\nSend the category name:", parse_mode=ParseMode.MARKDOWN)
    return ADD_CAT_NAME


async def add_cat_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_cat_name"] = update.message.text.strip()
    await update.message.reply_text("💵 Now send the price (e.g. 50 or 99.99):")
    return ADD_CAT_PRICE


async def add_cat_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    price = safe_float(update.message.text)
    if price is None or price < 0:
        await update.message.reply_text("❌ Invalid price. Send a number:")
        return ADD_CAT_PRICE
    db = await Database.get_instance()
    name = ctx.user_data["new_cat_name"]
    try:
        cid = await db.add_category(name, price)
    except Exception:
        await update.message.reply_text("❌ A category with that name already exists.", reply_markup=keyboards.admin_back_kb())
        return ConversationHandler.END
    await update.message.reply_text(f"✅ Category *{name}* added.\nNow add stock from the category menu.",
                                    reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


@requires_role("coupons")
async def cbq_togglecat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cat_id = int(query.data.split("adm_togglecat_")[1])
    db = await Database.get_instance()
    cat = await db.get_category(cat_id)
    if cat:
        new = not cat.get("is_active", True)
        await db.update_category(cat_id, is_active=new)
        await query.answer(f"Category {'Activated' if new else 'Deactivated'}")
    await cbq_category(update, ctx)


@requires_role("stock")
async def cbq_add_stock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["stock_cat_id"] = int(query.data.split("adm_addstock_")[1])
    await query.edit_message_text("➕ *Add Stock*\n\nSend the coupon codes — *one per line*.", parse_mode=ParseMode.MARKDOWN)
    return ADD_STOCK


async def add_stock_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = await Database.get_instance()
    cid = ctx.user_data["stock_cat_id"]
    items = [ln for ln in update.message.text.splitlines() if ln.strip()]
    added = await db.add_stock(cid, items)
    await update.message.reply_text(f"✅ Added *{added}* coupon code(s).", reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)
    if added > 0:
        cat = await db.get_category(cid)
        if cat and cat.get("is_active"):
            await fanout_new_coupon(ctx.bot, cat["name"], cat["price"])
    return ConversationHandler.END


# ... (del_cat, edit_name, edit_price flow the same but wrapped in @requires_role("coupons"))
@requires_role("coupons")
async def cbq_del_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("adm_delcat_")[1])
    await query.edit_message_text("🗑️ *Delete this category and ALL its stock?*\nThis cannot be undone.",
                                  reply_markup=keyboards.admin_confirm_delete_kb(cat_id), parse_mode=ParseMode.MARKDOWN)

@requires_role("coupons")
async def cbq_del_cat_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("adm_delcatyes_")[1])
    db = await Database.get_instance()
    await db.delete_category(cat_id)
    await query.edit_message_text("✅ Category deleted.", reply_markup=keyboards.admin_back_kb())

@requires_role("coupons")
async def cbq_edit_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["edit_cat_id"] = int(query.data.split("adm_editname_")[1])
    await query.edit_message_text("✏️ Send the new category name:", parse_mode=ParseMode.MARKDOWN)
    return EDIT_NAME

async def edit_name_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = await Database.get_instance()
    cid = ctx.user_data.get("edit_cat_id")
    await db.update_category(cid, name=update.message.text.strip())
    await update.message.reply_text("✅ Name updated.", reply_markup=keyboards.admin_back_kb())
    return ConversationHandler.END

@requires_role("coupons")
async def cbq_edit_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["edit_cat_id"] = int(query.data.split("adm_editprice_")[1])
    await query.edit_message_text("💵 Send the new price:", parse_mode=ParseMode.MARKDOWN)
    return EDIT_PRICE

async def edit_price_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    price = safe_float(update.message.text)
    if price is None or price < 0:
        await update.message.reply_text("❌ Invalid price. Try again:")
        return EDIT_PRICE
    db = await Database.get_instance()
    await db.update_category(ctx.user_data.get("edit_cat_id"), price=round(price, 2))
    await update.message.reply_text("✅ Price updated.", reply_markup=keyboards.admin_back_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════
# USERS / BAN / FRAUD
# ══════════════════════════════════════════════════════════════════════════
@requires_role("users_view")
async def cbq_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    total = await db.count_users()
    flagged = len(await db.list_flagged(1))
    await query.edit_message_text(f"👥 *Manage Users*\n\nTotal users: *{total}*\nFlagged for review: {flagged}",
                                  reply_markup=keyboards.admin_users_kb(), parse_mode=ParseMode.MARKDOWN)


@requires_role("coupons")  # piggyback fraud onto coupons role for simplicity
async def cbq_fraud(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    flagged = await db.list_flagged(10)
    if not flagged:
        await query.edit_message_text("✅ No flagged users.", reply_markup=keyboards.admin_back_kb())
        return
    lines = ["🚨 *Flagged Users (Fraud / Spam)*\n"]
    for u in flagged:
        lines.append(f"• `{u['user_id']}` (@{u.get('username','')}) — {u.get('flag_reason','')}")
    lines.append("\n_Use Check User Balance to view details or Ban._")
    await query.edit_message_text("\n".join(lines), reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)


@requires_role("users_ban")
async def cbq_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("🚫 Send the *user ID* to ban:")
    return BAN_UID

async def ban_uid_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = safe_int(update.message.text)
    if not uid:
        return BAN_UID
    db = await Database.get_instance()
    await db.set_banned(uid, True)
    await update.message.reply_text(f"🚫 User `{uid}` banned.", reply_markup=keyboards.admin_back_kb())
    return ConversationHandler.END


@requires_role("users_ban")
async def cbq_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("✅ Send the *user ID* to unban:")
    return UNBAN_UID

async def unban_uid_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = safe_int(update.message.text)
    if not uid:
        return UNBAN_UID
    db = await Database.get_instance()
    await db.set_banned(uid, False)
    await db.unflag_user(uid)  # also clear fraud flag
    await update.message.reply_text(f"✅ User `{uid}` unbanned and unflagged.", reply_markup=keyboards.admin_back_kb())
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════
# WALLET
# ══════════════════════════════════════════════════════════════════════════
@requires_role("wallet_check")
async def cbq_wallet_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("💰 *Wallet Control*", reply_markup=keyboards.admin_wallet_kb(), parse_mode=ParseMode.MARKDOWN)


@requires_role("wallet_control")
async def cbq_wallet_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("➕ Send the *user ID* to credit:")
    return WALLET_ADD_UID

async def wallet_add_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["w_uid"] = safe_int(update.message.text)
    await update.message.reply_text("💵 Send the amount to ADD:")
    return WALLET_ADD_AMT

async def wallet_add_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    amt = safe_float(update.message.text)
    if not amt: return WALLET_ADD_AMT
    db = await Database.get_instance()
    uid = ctx.user_data["w_uid"]
    new_bal = await db.admin_adjust_wallet(uid, amt, note="Admin credit")
    await update.message.reply_text(f"✅ Added {format_currency(amt)} to `{uid}`.\nNew balance: {format_currency(new_bal)}",
                                    reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)
    try:
        await ctx.bot.send_message(uid, f"💰 Your wallet was credited {format_currency(amt)} by admin.")
    except TelegramError: pass
    return ConversationHandler.END


@requires_role("wallet_control")
async def cbq_wallet_deduct(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("➖ Send the *user ID* to deduct from:")
    return WALLET_DED_UID

async def wallet_ded_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["w_uid"] = safe_int(update.message.text)
    await update.message.reply_text("💵 Send the amount to DEDUCT:")
    return WALLET_DED_AMT

async def wallet_ded_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    amt = safe_float(update.message.text)
    if not amt: return WALLET_DED_AMT
    db = await Database.get_instance()
    uid = ctx.user_data["w_uid"]
    new_bal = await db.admin_adjust_wallet(uid, -amt, note="Admin deduction")
    await update.message.reply_text(f"✅ Deducted {format_currency(amt)} from `{uid}`.\nNew balance: {format_currency(new_bal)}",
                                    reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


@requires_role("wallet_check")
async def cbq_wallet_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("🔍 Send the *user ID* to check:")
    return WALLET_CHECK_UID

async def wallet_check_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = safe_int(update.message.text)
    if not uid: return WALLET_CHECK_UID
    db = await Database.get_instance()
    u = await db.get_user(uid)
    if not u:
        await update.message.reply_text("User not found.", reply_markup=keyboards.admin_back_kb())
        return ConversationHandler.END
    txns = await db.get_transactions(uid, limit=5)
    hist = "\n".join(f"  {'+' if t['amount']>=0 else '−'}{format_currency(abs(t['amount']))} • {t['type']} • {fmt_dt(t['created_at'])}"
                     for t in txns) or "  (no transactions)"
    await update.message.reply_text(
        f"👤 *User* `{uid}`\nName: {u.get('full_name','N/A')} (@{u.get('username','')})\n"
        f"💰 Balance: *{format_currency(u.get('wallet_balance',0))}*\n"
        f"Banned: {'Yes' if u.get('is_banned') else 'No'}\n"
        f"Flagged: {'Yes' if u.get('flagged') else 'No'}\n\n*Recent:* \n{hist}",
        reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════
# TRANSACTIONS / ANALYTICS
# ══════════════════════════════════════════════════════════════════════════
@requires_role("transactions")
async def cbq_txns(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    txns = await db.get_all_transactions(limit=20)
    if not txns:
        await query.edit_message_text("📜 No transactions yet.", reply_markup=keyboards.admin_back_kb())
        return
    icons = {"recharge": "⬆️", "purchase": "🛒", "admin_adjust": "🛠️", "refund": "↩️", "referral": "🎁"}
    lines = ["📜 *Recent Transactions*\n"]
    for t in txns:
        lines.append(f"{icons.get(t['type'],'•')} `{t['user_id']}` "
                     f"{'+' if t['amount']>=0 else '−'}{format_currency(abs(t['amount']))} • {fmt_dt(t['created_at'])}")
    await query.edit_message_text("\n".join(lines), reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)


@requires_role("analytics")
async def cbq_analytics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    a = await db.analytics()
    top = "\n".join(f"  • {c['_id'] or 'N/A'}: {c['count']} sold ({format_currency(c['revenue'])})"
                    for c in a["top_categories"]) or "  (no sales yet)"
    text = (f"📊 *Analytics*\n\n👥 Users: *{a['total_users']}*\n"
            f"🛒 Orders: *{a['total_orders']}*\n💵 Revenue: *{format_currency(a['revenue'])}*\n"
            f"⬆️ Recharged: *{format_currency(a['recharged'])}*\n"
            f"🎁 Ref Paid: *{format_currency(a.get('referral_paid',0))}*\n"
            f"💰 Liability: *{format_currency(a['wallet_liability'])}*\n"
            f"📦 Stock: *{a['available_stock']}*\n\n*Top Categories:*\n{top}")
    await query.edit_message_text(text, reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════
# BROADCAST
# ══════════════════════════════════════════════════════════════════════════
@requires_role("announce")
async def cbq_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("📢 *Broadcast*\n\nSelect audience segment:",
                                                  reply_markup=keyboards.admin_broadcast_kb(), parse_mode=ParseMode.MARKDOWN)

@requires_role("announce")
async def cbq_bc_segment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    seg = query.data.split("adm_bc_")[1]
    ctx.user_data["bc_segment"] = seg
    await query.edit_message_text(f"📢 *Broadcast ({seg})*\n\nSend the message (Markdown supported):", parse_mode=ParseMode.MARKDOWN)
    return BC_MSG

async def broadcast_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    seg = ctx.user_data.get("bc_segment", "all")
    db = await Database.get_instance()
    uids = await db.all_user_ids(seg)
    if not uids:
        await update.message.reply_text("No users found in that segment.", reply_markup=keyboards.admin_back_kb())
        return ConversationHandler.END
    msg = await update.message.reply_text(f"📤 Preparing to broadcast to {len(uids)} users…")
    # run background task that updates the message
    asyncio.create_task(broadcast(ctx.bot, uids, text, progress_msg=msg))
    # handler ends, admin is free to do other things
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════
@requires_role("settings")
async def cbq_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    maint = await db.get_setting("maintenance", "false") == "true"
    await query.edit_message_text("⚙️ *Settings*", reply_markup=keyboards.admin_settings_kb(maint), parse_mode=ParseMode.MARKDOWN)

@requires_role("settings")
async def cbq_toggle_maint(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    db = await Database.get_instance()
    cur = await db.get_setting("maintenance", "false")
    new = "false" if cur == "true" else "true"
    await db.set_setting("maintenance", new)
    await query.answer(f"Maintenance {'ON' if new=='true' else 'OFF'}")
    await cbq_settings(update, ctx)


def _setup_setting_conv(app, entry_pattern, prompt, state, db_key):
    async def entry(update, ctx):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(prompt, parse_mode=ParseMode.MARKDOWN)
        return state
    async def finish(update, ctx):
        db = await Database.get_instance()
        await db.set_setting(db_key, update.message.text.strip())
        await update.message.reply_text("✅ Updated.", reply_markup=keyboards.admin_back_kb())
        return ConversationHandler.END
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(requires_role("settings")(entry), pattern=entry_pattern)],
        states={state: [MessageHandler(filters.TEXT & ~filters.COMMAND, requires_role("settings")(finish))]},
        fallbacks=[CommandHandler("admin", cmd_admin), CallbackQueryHandler(cbq_admin_menu, pattern="^adm_menu$")],
    ))


# ══════════════════════════════════════════════════════════════════════════
# REFERRAL & DISCOUNTS
# ══════════════════════════════════════════════════════════════════════════
@requires_role("referral")
async def cbq_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    cfg = await db.get_referral_config()
    await query.edit_message_text(
        f"🎁 *Referral Config*\n\nSignup Bonus: {format_currency(cfg['signup_bonus'])}\n"
        f"Commission: {cfg['commission_pct']}%\nWelcome Bonus: {format_currency(cfg['welcome_bonus'])}",
        reply_markup=keyboards.admin_referral_kb(cfg["enabled"]), parse_mode=ParseMode.MARKDOWN)

@requires_role("referral")
async def cbq_ref_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = await Database.get_instance()
    cfg = await db.get_referral_config()
    await db.set_setting("ref_enabled", "false" if cfg["enabled"] else "true")
    await cbq_referral(update, ctx)

@requires_role("settings")
async def cbq_discounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    tiers = await db.get_discount_tiers()
    from utils import tiers_summary
    text = f"🎉 *Bulk Discounts*\n\n{tiers_summary(tiers)}\n\n_To update, you can edit them via DB directly or clear them here._"
    await query.edit_message_text(text, reply_markup=keyboards.admin_discounts_kb(), parse_mode=ParseMode.MARKDOWN)

@requires_role("settings")
async def cbq_cleardiscounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db = await Database.get_instance()
    await db.set_setting("discount_tiers", [])
    await update.callback_query.answer("Discounts cleared.")
    await cbq_discounts(update, ctx)


# ══════════════════════════════════════════════════════════════════════════
# SUPER ADMIN (Staff & Backup)
# ══════════════════════════════════════════════════════════════════════════
@requires_role("staff")
async def cbq_staff(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = await Database.get_instance()
    admins = await db.list_admins()
    await query.edit_message_text("🧑‍✈️ *Manage Staff*\n\nDatabase-assigned roles:",
                                  reply_markup=keyboards.admin_staff_kb(admins), parse_mode=ParseMode.MARKDOWN)

@requires_role("staff")
async def cbq_staffadd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("🧑‍✈️ Send the *user ID* to add/update:")
    return STAFF_ADD_UID

async def staff_add_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = safe_int(update.message.text)
    if not uid: return STAFF_ADD_UID
    ctx.user_data["s_uid"] = uid
    await update.message.reply_text("Select role:", reply_markup=keyboards.admin_staff_role_kb(uid))
    return ConversationHandler.END

@requires_role("staff")
async def cbq_setrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, uid_s, role = query.data.split("_")
    db = await Database.get_instance()
    await db.set_admin_role(int(uid_s), role, added_by=query.from_user.id)
    await query.edit_message_text(f"✅ User {uid_s} is now {role}.", reply_markup=keyboards.admin_back_kb())

@requires_role("staff")
async def cbq_staffdel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split("adm_staffdel_")[1])
    db = await Database.get_instance()
    await db.remove_admin(uid)
    await query.edit_message_text(f"🗑️ Removed {uid} from DB staff list.", reply_markup=keyboards.admin_back_kb())


@requires_role("backup")
async def cbq_backup_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("💾 *Backup & Restore*\n\nDownload a full JSON dump or restore from one.",
                                  reply_markup=keyboards.admin_backup_kb(), parse_mode=ParseMode.MARKDOWN)

@requires_role("backup")
async def cbq_dobackup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("⏳ Generating backup...")
    db = await Database.get_instance()
    data = await db.export_all()
    j = json.dumps(data, separators=(',', ':'))
    buf = BytesIO(j.encode("utf-8"))
    buf.name = f"backup_{config.BOT_NAME}_{fmt_dt(_now()).replace(' ', '_')}.json"
    await ctx.bot.send_document(query.message.chat_id, buf, caption="✅ Database Export")
    await query.message.delete()

@requires_role("backup")
async def cbq_dorestore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⬆️ *Restore*\n\nSend a previously exported `.json` file now.", parse_mode=ParseMode.MARKDOWN)
    return RESTORE_FILE

async def restore_file_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.document or not update.message.document.file_name.endswith(".json"):
        await update.message.reply_text("❌ Please send a valid .json backup file.")
        return RESTORE_FILE
    f = await update.message.document.get_file()
    jdata = await f.download_as_bytearray()
    try:
        data = json.loads(jdata)
        if "_meta" not in data: raise ValueError("Not a valid backup file")
        ctx.user_data["restore_data"] = data
        await update.message.reply_text("⚠️ *DANGER*\n\nThis will WIPE all current data and replace it with the backup. Continue?",
                                        reply_markup=keyboards.admin_restore_confirm_kb(), parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ Failed to parse backup file.", reply_markup=keyboards.admin_back_kb())
        return ConversationHandler.END

@requires_role("backup")
async def cbq_restoreyes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = ctx.user_data.pop("restore_data", None)
    if not data:
        await query.edit_message_text("Session expired.", reply_markup=keyboards.admin_back_kb())
        return
    await query.edit_message_text("⏳ Restoring...")
    db = await Database.get_instance()
    try:
        counts = await db.import_all(data)
        summary = "\n".join(f"{k}: {v}" for k,v in counts.items())
        await query.edit_message_text(f"✅ *Restore Complete*\n\n{summary}", reply_markup=keyboards.admin_back_kb(), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await query.edit_message_text(f"❌ Restore failed: {e}", reply_markup=keyboards.admin_back_kb())


def _conv(app, pattern, entry, state, func, permission=""):
    d = requires_role(permission)(entry) if permission else entry
    f = requires_role(permission)(func) if permission else func
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(d, pattern=pattern)],
        states={state: [MessageHandler(filters.TEXT & ~filters.COMMAND, f)]},
        fallbacks=[CommandHandler("admin", cmd_admin), CallbackQueryHandler(cbq_admin_menu, pattern="^adm_menu$")],
        per_chat=True, per_user=True,
    ))


def register_admin_handlers(app):
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(cbq_admin_menu, pattern="^adm_menu$"))
    app.add_handler(CallbackQueryHandler(_close, pattern="^adm_close$"))

    # Basic UI
    app.add_handler(CallbackQueryHandler(cbq_coupons, pattern="^adm_coupons$"))
    app.add_handler(CallbackQueryHandler(cbq_category, pattern=r"^adm_cat_\d+$"))
    app.add_handler(CallbackQueryHandler(cbq_del_cat, pattern=r"^adm_delcat_\d+$"))
    app.add_handler(CallbackQueryHandler(cbq_del_cat_yes, pattern=r"^adm_delcatyes_\d+$"))
    app.add_handler(CallbackQueryHandler(cbq_togglecat, pattern=r"^adm_togglecat_\d+$"))
    app.add_handler(CallbackQueryHandler(cbq_wallet_menu, pattern="^adm_wallet$"))
    app.add_handler(CallbackQueryHandler(cbq_users, pattern="^adm_users$"))
    app.add_handler(CallbackQueryHandler(cbq_fraud, pattern="^adm_fraud$"))
    app.add_handler(CallbackQueryHandler(cbq_txns, pattern="^adm_txns$"))
    app.add_handler(CallbackQueryHandler(cbq_analytics, pattern="^adm_analytics$"))
    app.add_handler(CallbackQueryHandler(cbq_settings, pattern="^adm_settings$"))
    app.add_handler(CallbackQueryHandler(cbq_toggle_maint, pattern="^adm_togglemaint$"))

    # Convs
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(requires_role("coupons")(cbq_add_cat), pattern="^adm_addcat$")],
        states={ADD_CAT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cat_name)],
                ADD_CAT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cat_price)]},
        fallbacks=[CommandHandler("admin", cmd_admin)], per_chat=True, per_user=True))
    _conv(app, r"^adm_editname_\d+$", cbq_edit_name, EDIT_NAME, edit_name_input, "coupons")
    _conv(app, r"^adm_editprice_\d+$", cbq_edit_price, EDIT_PRICE, edit_price_input, "coupons")
    _conv(app, r"^adm_addstock_\d+$", cbq_add_stock, ADD_STOCK, add_stock_input, "stock")

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(requires_role("wallet_control")(cbq_wallet_add), pattern="^adm_walletadd$")],
        states={WALLET_ADD_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_add_uid)],
                WALLET_ADD_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_add_amt)]},
        fallbacks=[CommandHandler("admin", cmd_admin)], per_chat=True, per_user=True))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(requires_role("wallet_control")(cbq_wallet_deduct), pattern="^adm_walletdeduct$")],
        states={WALLET_DED_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_ded_uid)],
                WALLET_DED_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_ded_amt)]},
        fallbacks=[CommandHandler("admin", cmd_admin)], per_chat=True, per_user=True))
    _conv(app, "^adm_walletcheck$", cbq_wallet_check, WALLET_CHECK_UID, wallet_check_uid, "wallet_check")
    _conv(app, "^adm_ban$", cbq_ban, BAN_UID, ban_uid_input, "users_ban")
    _conv(app, "^adm_unban$", cbq_unban, UNBAN_UID, unban_uid_input, "users_ban")
    _conv(app, r"^adm_bc_(all|notify|buyers|with_balance|recharged)$", cbq_bc_segment, BC_MSG, broadcast_input, "announce")
    app.add_handler(CallbackQueryHandler(requires_role("announce")(cbq_broadcast), pattern="^adm_broadcast$"))

    # Config Setters
    _setup_setting_conv(app, "^adm_setupi$", "💳 Send the new UPI ID:", SET_UPI, "upi_id")
    _setup_setting_conv(app, "^adm_setpayee$", "👤 Send the new Payee Name:", SET_PAYEE, "payee_name")
    _setup_setting_conv(app, "^adm_setlowstock$", "📦 Send the new low-stock threshold (e.g. 5):", SET_LOW_STOCK, "low_stock_threshold")

    # Referral & Discounts
    app.add_handler(CallbackQueryHandler(cbq_referral, pattern="^adm_referral$"))
    app.add_handler(CallbackQueryHandler(cbq_ref_toggle, pattern="^adm_ref_toggle$"))
    _setup_setting_conv(app, "^adm_ref_signup$", "💵 Send Signup Bonus amount:", REF_SIGNUP, "ref_signup_bonus")
    _setup_setting_conv(app, "^adm_ref_commission$", "📈 Send Commission %:", REF_COMMISSION, "ref_commission_pct")
    _setup_setting_conv(app, "^adm_ref_welcome$", "🎉 Send Welcome Bonus amount:", REF_WELCOME, "ref_welcome_bonus")
    app.add_handler(CallbackQueryHandler(cbq_discounts, pattern="^adm_discounts$"))
    app.add_handler(CallbackQueryHandler(cbq_cleardiscounts, pattern="^adm_cleardiscounts$"))

    # Super Admin Staff & Backup
    app.add_handler(CallbackQueryHandler(cbq_staff, pattern="^adm_staff$"))
    app.add_handler(CallbackQueryHandler(cbq_staffdel, pattern=r"^adm_staffdel_\d+$"))
    app.add_handler(CallbackQueryHandler(cbq_setrole, pattern=r"^adm_setrole_\d+_\w+$"))
    _conv(app, "^adm_staffadd$", cbq_staffadd, STAFF_ADD_UID, staff_add_uid, "staff")

    app.add_handler(CallbackQueryHandler(cbq_backup_menu, pattern="^adm_backup$"))
    app.add_handler(CallbackQueryHandler(cbq_dobackup, pattern="^adm_dobackup$"))
    app.add_handler(CallbackQueryHandler(cbq_restoreyes, pattern="^adm_restoreyes$"))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(requires_role("backup")(cbq_dorestore), pattern="^adm_dorestore$")],
        states={RESTORE_FILE: [MessageHandler(filters.Document.ALL, restore_file_input)]},
        fallbacks=[CommandHandler("admin", cmd_admin), CallbackQueryHandler(cbq_backup_menu, pattern="^adm_backup$")],
        per_chat=True, per_user=True))
