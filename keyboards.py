"""
keyboards.py - All InlineKeyboardMarkup builders for the bot.
Role-aware admin menus + new user features (referral, notifications, QR pay).
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from utils import chunks, role_can


# ══════════════════════════════════════════════════════════════════════════
# USER KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Browse Coupons", callback_data="browse")],
        [InlineKeyboardButton("💼 My Wallet", callback_data="wallet"),
         InlineKeyboardButton("📦 My Orders", callback_data="my_orders")],
        [InlineKeyboardButton("🎁 Refer & Earn", callback_data="referral")],
        [InlineKeyboardButton("🔔 Notifications", callback_data="notify_menu"),
         InlineKeyboardButton("ℹ️ Help", callback_data="help")],
    ])


def wallet_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Recharge Wallet", callback_data="recharge")],
        [InlineKeyboardButton("📜 Transaction History", callback_data="txn_history")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


def paid_kb() -> InlineKeyboardMarkup:
    """Shown under the QR: user taps once they've paid, or cancels."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I've Paid — Enter Txn ID", callback_data="ive_paid")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="cancel_recharge")],
    ])


def recharge_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Cancel", callback_data="cancel_recharge")],
    ])


def notify_kb(enabled: bool) -> InlineKeyboardMarkup:
    label = "🔔 Notifications: ON  (tap to turn OFF)" if enabled else \
            "🔕 Notifications: OFF  (tap to turn ON)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="notify_toggle")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


def referral_kb(share_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Share Invite Link", url=share_url)],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="ref_leaderboard")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


def categories_kb(categories: list[dict], stock_map: dict | None = None) -> InlineKeyboardMarkup:
    stock_map = stock_map or {}
    buttons = []
    for c in categories:
        st = stock_map.get(c["id"])
        tag = "" if st is None else (f" • {st} left" if st > 0 else " • ❌")
        buttons.append([InlineKeyboardButton(
            f"🏷️ {c['name']} — {c['price']:.0f}₹{tag}", callback_data=f"cat_{c['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def quantity_kb(cat_id: int) -> InlineKeyboardMarkup:
    quantities = [1, 2, 5, 10, 25, 50]
    rows = list(chunks(
        [InlineKeyboardButton(f"× {q}", callback_data=f"qty_{cat_id}_{q}") for q in quantities],
        3,
    ))
    rows.append([InlineKeyboardButton("✏️ Custom Quantity", callback_data=f"qtycustom_{cat_id}")])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="browse")])
    return InlineKeyboardMarkup(rows)


def confirm_purchase_kb(cat_id: int, qty: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Pay from Wallet", callback_data=f"buy_{cat_id}_{qty}")],
        [InlineKeyboardButton("➕ Recharge", callback_data="recharge"),
         InlineKeyboardButton("🔙 Back", callback_data=f"cat_{cat_id}")],
    ])


def back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
    ])


def my_orders_kb(orders: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for o in orders:
        label = f"{o['order_id']} • {o.get('category_name', 'N/A')} • {o['status']}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"vieworder_{o['order_id']}")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════════════════════════════════
# ADMIN KEYBOARDS (role-aware)
# ══════════════════════════════════════════════════════════════════════════
def admin_menu_kb(role: str) -> InlineKeyboardMarkup:
    """Build the admin menu according to the staff member's permissions."""
    rows = []
    if role_can(role, "coupons"):
        rows.append([InlineKeyboardButton("🏷️ Manage Coupons", callback_data="adm_coupons")])
    if role_can(role, "users_ban") or role_can(role, "users_view"):
        rows.append([InlineKeyboardButton("👥 Manage Users", callback_data="adm_users")])
    if role_can(role, "wallet_control") or role_can(role, "wallet_check"):
        rows.append([InlineKeyboardButton("💰 Wallet Control", callback_data="adm_wallet")])
    if role_can(role, "transactions"):
        rows.append([InlineKeyboardButton("📜 Transactions", callback_data="adm_txns")])
    if role_can(role, "analytics"):
        rows.append([InlineKeyboardButton("📊 Analytics", callback_data="adm_analytics")])
    if role_can(role, "referral"):
        rows.append([InlineKeyboardButton("🎁 Referral Control", callback_data="adm_referral")])
    if role_can(role, "announce"):
        rows.append([InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")])
    if role_can(role, "coupons"):
        rows.append([InlineKeyboardButton("🚨 Fraud / Flagged", callback_data="adm_fraud")])
    if role_can(role, "settings"):
        rows.append([InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")])
    if role == "super_admin":
        rows.append([InlineKeyboardButton("🧑‍✈️ Manage Staff", callback_data="adm_staff")])
        rows.append([InlineKeyboardButton("💾 Backup / Restore", callback_data="adm_backup")])
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton("🔙 Close", callback_data="adm_close")]])


def admin_coupons_kb(categories: list[dict]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("➕ Add Category", callback_data="adm_addcat")]]
    for c in categories:
        status = "" if c.get("is_active") else " (off)"
        buttons.append([InlineKeyboardButton(
            f"🏷️ {c['name']} ({c['price']:.0f}₹){status}", callback_data=f"adm_cat_{c['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")])
    return InlineKeyboardMarkup(buttons)


def admin_category_kb(cat_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    toggle = "🚫 Deactivate" if is_active else "✅ Activate"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Stock", callback_data=f"adm_addstock_{cat_id}")],
        [InlineKeyboardButton("✏️ Edit Name", callback_data=f"adm_editname_{cat_id}"),
         InlineKeyboardButton("💵 Edit Price", callback_data=f"adm_editprice_{cat_id}")],
        [InlineKeyboardButton(toggle, callback_data=f"adm_togglecat_{cat_id}")],
        [InlineKeyboardButton("🗑️ Delete Category", callback_data=f"adm_delcat_{cat_id}")],
        [InlineKeyboardButton("🔙 Coupons", callback_data="adm_coupons")],
    ])


def admin_confirm_delete_kb(cat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, delete", callback_data=f"adm_delcatyes_{cat_id}"),
         InlineKeyboardButton("❌ No", callback_data=f"adm_cat_{cat_id}")],
    ])


def admin_wallet_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Balance", callback_data="adm_walletadd")],
        [InlineKeyboardButton("➖ Deduct Balance", callback_data="adm_walletdeduct")],
        [InlineKeyboardButton("🔍 Check User Balance", callback_data="adm_walletcheck")],
        [InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")],
    ])


def admin_users_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"),
         InlineKeyboardButton("✅ Unban User", callback_data="adm_unban")],
        [InlineKeyboardButton("🔍 Look Up User", callback_data="adm_walletcheck")],
        [InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")],
    ])


def admin_broadcast_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 All Users", callback_data="adm_bc_all")],
        [InlineKeyboardButton("🔔 Opted-in Only", callback_data="adm_bc_notify")],
        [InlineKeyboardButton("🛒 Buyers", callback_data="adm_bc_buyers"),
         InlineKeyboardButton("💰 With Balance", callback_data="adm_bc_with_balance")],
        [InlineKeyboardButton("⬆️ Recharged", callback_data="adm_bc_recharged")],
        [InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")],
    ])


def admin_referral_kb(enabled: bool) -> InlineKeyboardMarkup:
    toggle = "🟢 Program: ON (tap to OFF)" if enabled else "🔴 Program: OFF (tap to ON)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle, callback_data="adm_ref_toggle")],
        [InlineKeyboardButton("💵 Set Signup Bonus", callback_data="adm_ref_signup")],
        [InlineKeyboardButton("📈 Set Commission %", callback_data="adm_ref_commission")],
        [InlineKeyboardButton("🎉 Set Welcome Bonus", callback_data="adm_ref_welcome")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="adm_ref_leaderboard")],
        [InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")],
    ])


def admin_settings_kb(maint: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Set UPI ID", callback_data="adm_setupi")],
        [InlineKeyboardButton("👤 Set Payee Name", callback_data="adm_setpayee")],
        [InlineKeyboardButton("🎉 Bulk Discounts", callback_data="adm_discounts")],
        [InlineKeyboardButton("📦 Low-Stock Threshold", callback_data="adm_setlowstock")],
        [InlineKeyboardButton(
            f"🛠️ Maintenance: {'ON' if maint else 'OFF'} (toggle)",
            callback_data="adm_togglemaint")],
        [InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")],
    ])


def admin_discounts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Tiers", callback_data="adm_editdiscounts")],
        [InlineKeyboardButton("🧹 Clear All", callback_data="adm_cleardiscounts")],
        [InlineKeyboardButton("🔙 Settings", callback_data="adm_settings")],
    ])


def admin_staff_kb(admins: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("➕ Add / Update Staff", callback_data="adm_staffadd")]]
    for a in admins:
        rows.append([InlineKeyboardButton(
            f"🗑️ {a['user_id']} • {a['role']}", callback_data=f"adm_staffdel_{a['user_id']}")])
    rows.append([InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")])
    return InlineKeyboardMarkup(rows)


def admin_staff_role_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Admin", callback_data=f"adm_setrole_{user_id}_admin")],
        [InlineKeyboardButton("🎧 Support Staff", callback_data=f"adm_setrole_{user_id}_support")],
        [InlineKeyboardButton("👑 Super Admin", callback_data=f"adm_setrole_{user_id}_super_admin")],
        [InlineKeyboardButton("🔙 Staff", callback_data="adm_staff")],
    ])


def admin_backup_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Download Backup (.json)", callback_data="adm_dobackup")],
        [InlineKeyboardButton("⬆️ Restore from File", callback_data="adm_dorestore")],
        [InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")],
    ])


def admin_restore_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, overwrite & restore", callback_data="adm_restoreyes")],
        [InlineKeyboardButton("❌ Cancel", callback_data="adm_backup")],
    ])


def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Admin Menu", callback_data="adm_menu")],
    ])
