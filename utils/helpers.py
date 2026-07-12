"""
utils/helpers.py - Utility functions shared across the bot.

Adds (vs original):
  • Role helpers (super_admin / admin / support) + permission checks
  • Bulk-discount calculator
  • UPI deep-link + dynamic QR-code image generator
  • Referral-code helpers
  • Small formatting helpers
"""

import io
import random
import re
import string
from datetime import datetime, timezone
from urllib.parse import quote

import config


# ══════════════════════════════════════════════════════════════════════════
# ROLES & PERMISSIONS
# ══════════════════════════════════════════════════════════════════════════
ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_SUPPORT = "support"
ROLE_USER = "user"

# What each role is allowed to do. Super admin implicitly has everything.
PERMISSIONS = {
    ROLE_SUPER_ADMIN: {"*"},
    ROLE_ADMIN: {
        "coupons", "stock", "users_ban", "wallet_control", "transactions",
        "analytics", "announce", "referral", "low_stock", "settings", "offers",
    },
    ROLE_SUPPORT: {
        "transactions", "analytics", "wallet_check", "users_view",
    },
    ROLE_USER: set(),
}


def env_role(user_id: int) -> str | None:
    """Role assigned purely from environment bootstrap (highest wins)."""
    if user_id in config.SUPER_ADMIN_IDS:
        return ROLE_SUPER_ADMIN
    if user_id in config.SUPPORT_IDS:
        return ROLE_SUPPORT
    return None


def is_admin(user_id: int) -> bool:
    """Backward-compatible: True for any staff role (super/admin/support)."""
    return user_id in config.SUPER_ADMIN_IDS or user_id in config.SUPPORT_IDS


def is_super_admin(user_id: int) -> bool:
    return user_id in config.SUPER_ADMIN_IDS


def role_can(role: str, permission: str) -> bool:
    perms = PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms


def role_label(role: str) -> str:
    return {
        ROLE_SUPER_ADMIN: "👑 Super Admin",
        ROLE_ADMIN: "🛡️ Admin",
        ROLE_SUPPORT: "🎧 Support Staff",
        ROLE_USER: "👤 User",
    }.get(role, role)


# ══════════════════════════════════════════════════════════════════════════
# IDS / CODES
# ══════════════════════════════════════════════════════════════════════════
def generate_order_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%y%m%d")
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"ORD-{ts}-{rand}"


def generate_ref_code(user_id: int) -> str:
    """Deterministic short referral code from a user id (base36)."""
    n = int(user_id)
    digits = string.digits + string.ascii_uppercase
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


# ══════════════════════════════════════════════════════════════════════════
# FORMATTING
# ══════════════════════════════════════════════════════════════════════════
def format_currency(amount: float) -> str:
    return f"{config.CURRENCY_SYMBOL}{amount:,.2f}"


def chunks(seq, n):
    """Yield successive n-sized chunks from seq."""
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def safe_int(value, default=None):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def safe_float(value, default=None):
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


# A transaction ID / UTR is typically 10-30 chars, digits or alphanumeric.
TXN_RE = re.compile(r"^[A-Za-z0-9]{8,40}$")


def valid_txn_id(txn_id: str) -> bool:
    return bool(TXN_RE.match((txn_id or "").strip()))


def fmt_dt(dt) -> str:
    """Format a datetime (or ISO string) for display."""
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        return dt[:16]
    try:
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)[:16]


def format_delivery(category_name: str, items: list[str]) -> str:
    """Format delivered coupon codes for the buyer."""
    lines = [f"🎁 *Your {category_name} coupon(s):*", ""]
    for i, code in enumerate(items, 1):
        lines.append(f"{i}. `{code}`")
    lines.append("")
    lines.append("_Keep these safe. Thank you for your purchase!_")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# BULK DISCOUNT
# ══════════════════════════════════════════════════════════════════════════
def best_tier(qty: int, tiers: list[dict]) -> dict | None:
    """Return the highest-percentage tier whose min_qty <= qty."""
    eligible = [t for t in (tiers or []) if qty >= int(t.get("min_qty", 0))]
    if not eligible:
        return None
    return max(eligible, key=lambda t: float(t.get("pct", 0)))


def calc_pricing(unit_price: float, qty: int, tiers: list[dict]) -> dict:
    """
    Returns dict(subtotal, discount_pct, discount_amount, total, tier).
    Discount applies to the whole order once qty crosses a tier threshold.
    """
    subtotal = round(float(unit_price) * int(qty), 2)
    tier = best_tier(qty, tiers)
    pct = float(tier["pct"]) if tier else 0.0
    discount_amount = round(subtotal * pct / 100.0, 2)
    total = round(subtotal - discount_amount, 2)
    return {
        "subtotal": subtotal,
        "discount_pct": pct,
        "discount_amount": discount_amount,
        "total": total,
        "tier": tier,
    }


def tiers_summary(tiers: list[dict]) -> str:
    if not tiers:
        return "No bulk discounts configured."
    parts = []
    for t in sorted(tiers, key=lambda x: int(x.get("min_qty", 0))):
        parts.append(f"• Buy {int(t['min_qty'])}+ → *{float(t['pct']):g}%* off")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# UPI QR CODE
# ══════════════════════════════════════════════════════════════════════════
def upi_link(upi_id: str, payee: str, amount: float | None = None, note: str = "") -> str:
    """Build a standard UPI deep link (upi://pay?...)."""
    params = [f"pa={quote(upi_id)}", f"pn={quote(payee or 'Merchant')}", "cu=INR"]
    if amount and amount > 0:
        params.append(f"am={amount:.2f}")
    if note:
        params.append(f"tn={quote(note)}")
    return "upi://pay?" + "&".join(params)


def make_upi_qr(upi_id: str, payee: str, amount: float | None = None, note: str = ""):
    """
    Return a BytesIO PNG of a QR encoding the UPI deep link, or None if the
    qrcode library is unavailable. Any UPI app can scan it to pre-fill payment.
    """
    try:
        import qrcode  # local import so the bot still runs if lib missing
    except Exception:
        return None
    data = upi_link(upi_id, payee, amount, note)
    qr = qrcode.QRCode(version=None, box_size=10, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    buf.name = "upi_qr.png"
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
