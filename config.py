"""
config.py - Central configuration for the upgraded Coupon Selling Bot.

All sensitive values load from environment variables (Railway / .env support).
New in this version:
  • Role-based admin bootstrap (SUPER_ADMIN_IDS, ADMIN_IDS, SUPPORT_IDS)
  • Referral / affiliate defaults
  • Bulk-discount defaults
  • Security / rate-limit / fraud-detection knobs
  • Low-stock alert threshold
  • Dynamic UPI QR generation toggle
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _clean(v):
    return v.strip() if isinstance(v, str) else v


def _parse_ids(raw: str) -> list[int]:
    """Parse comma-separated Telegram user IDs into a list of ints."""
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]


def _as_bool(raw, default=False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _as_int(raw, default=0) -> int:
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return default


def _as_float(raw, default=0.0) -> float:
    try:
        return float(str(raw).strip())
    except (ValueError, TypeError):
        return default


# ── Telegram ────────────────────────────────────────────────────────────────
BOT_TOKEN: str = _clean(os.getenv("BOT_TOKEN", ""))

# Role bootstrap from env. SUPER_ADMIN_IDS have every permission.
# ADMIN_IDS kept for backward compatibility -> treated as super admins too.
SUPER_ADMIN_IDS: list[int] = _parse_ids(_clean(os.getenv("SUPER_ADMIN_IDS", "")))
ADMIN_IDS: list[int] = _parse_ids(_clean(os.getenv("ADMIN_IDS", "")))
SUPPORT_IDS: list[int] = _parse_ids(_clean(os.getenv("SUPPORT_IDS", "")))
# Legacy ADMIN_IDS are promoted to super admins so nothing breaks on upgrade.
SUPER_ADMIN_IDS = list(dict.fromkeys(SUPER_ADMIN_IDS + ADMIN_IDS))

ADMIN_CHAT_ID: str = _clean(os.getenv("ADMIN_CHAT_ID", ""))

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI: str = _clean(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
MONGO_DB_NAME: str = _clean(os.getenv("MONGO_DB_NAME", "coupon_bot"))

# ── Gmail (IMAP for auto payment verification) ────────────────────────────────
GMAIL_ADDRESS: str = _clean(os.getenv("GMAIL_ADDRESS", ""))
GMAIL_APP_PASSWORD: str = _clean(os.getenv("GMAIL_APP_PASSWORD", ""))
IMAP_HOST: str = _clean(os.getenv("IMAP_HOST", "imap.gmail.com"))
SENDER_FILTER: str = _clean(os.getenv("SENDER_FILTER", ""))
EMAIL_LOOKBACK_HOURS: int = _as_int(os.getenv("EMAIL_LOOKBACK_HOURS"), 48)

# ── Payment ───────────────────────────────────────────────────────────────────
UPI_ID: str = _clean(os.getenv("UPI_ID", ""))
PAYEE_NAME: str = _clean(os.getenv("PAYEE_NAME", "CouponBot"))
QR_IMAGE_PATH: str = _clean(os.getenv("QR_IMAGE_PATH", ""))  # optional static QR
GENERATE_DYNAMIC_QR: bool = _as_bool(os.getenv("GENERATE_DYNAMIC_QR", "true"), True)

# ── Bot branding ──────────────────────────────────────────────────────────────
BOT_NAME: str = _clean(os.getenv("BOT_NAME", "CouponBot"))
BOT_USERNAME: str = _clean(os.getenv("BOT_USERNAME", ""))  # without @, for ref links
CURRENCY_SYMBOL: str = _clean(os.getenv("CURRENCY_SYMBOL", "₹"))
SUPPORT_USERNAME: str = _clean(os.getenv("SUPPORT_USERNAME", ""))  # e.g. mystore_help

# ── Bulk discount defaults (admin can override via panel) ─────────────────────
# Each tier: buy >= min_qty  ->  pct % off the whole order.
DISCOUNT_TIERS_DEFAULT = [
    {"min_qty": 5, "pct": 5},
    {"min_qty": 10, "pct": 10},
    {"min_qty": 25, "pct": 15},
]

# ── Referral / affiliate defaults (admin can override via panel) ──────────────
REFERRAL_ENABLED_DEFAULT: bool = _as_bool(os.getenv("REFERRAL_ENABLED", "true"), True)
# Flat wallet bonus credited to the referrer when a referred user makes their
# first successful recharge.
REFERRAL_SIGNUP_BONUS_DEFAULT: float = _as_float(os.getenv("REFERRAL_SIGNUP_BONUS"), 10.0)
# Percentage commission credited to the referrer on every recharge the referred
# user makes (lifetime).
REFERRAL_COMMISSION_PCT_DEFAULT: float = _as_float(os.getenv("REFERRAL_COMMISSION_PCT"), 5.0)
# Optional welcome bonus for the NEW user who joined via a referral link.
REFERRAL_WELCOME_BONUS_DEFAULT: float = _as_float(os.getenv("REFERRAL_WELCOME_BONUS"), 0.0)

# ── Security / anti-spam / rate limiting ──────────────────────────────────────
# Max actions (messages + button taps) allowed inside RATE_WINDOW seconds.
RATE_LIMIT_ACTIONS: int = _as_int(os.getenv("RATE_LIMIT_ACTIONS"), 20)
RATE_WINDOW_SECONDS: int = _as_int(os.getenv("RATE_WINDOW_SECONDS"), 10)
# If a user trips the limit this many times they are auto-flagged for review.
FRAUD_FAILED_TXN_LIMIT: int = _as_int(os.getenv("FRAUD_FAILED_TXN_LIMIT"), 5)
# Cooldown (seconds) between two txn-id verification attempts.
TXN_ATTEMPT_COOLDOWN: int = _as_int(os.getenv("TXN_ATTEMPT_COOLDOWN"), 15)

# ── Low-stock alerts ──────────────────────────────────────────────────────────
LOW_STOCK_THRESHOLD_DEFAULT: int = _as_int(os.getenv("LOW_STOCK_THRESHOLD"), 5)
LOW_STOCK_CHECK_INTERVAL: int = _as_int(os.getenv("LOW_STOCK_CHECK_INTERVAL"), 300)

# ── Performance / behavior ────────────────────────────────────────────────────
PAYMENT_TIMEOUT_MINUTES: int = _as_int(os.getenv("PAYMENT_TIMEOUT_MINUTES"), 30)
LOG_LEVEL: str = _clean(os.getenv("LOG_LEVEL", "INFO"))
MAINTENANCE_MODE: bool = _as_bool(os.getenv("MAINTENANCE_MODE", "false"))
MAX_BULK_QTY: int = _as_int(os.getenv("MAX_BULK_QTY"), 100)
ANIMATIONS_ENABLED: bool = _as_bool(os.getenv("ANIMATIONS_ENABLED", "true"), True)

# ── Webhook (optional — blank = polling) ──────────────────────────────────────
WEBHOOK_URL: str = _clean(os.getenv("WEBHOOK_URL", ""))
PORT: int = _as_int(os.getenv("PORT"), 8443)


def validate():
    """Raise SystemExit if required env vars are missing."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not MONGO_URI:
        missing.append("MONGO_URI")
    if not SUPER_ADMIN_IDS:
        missing.append("SUPER_ADMIN_IDS (or ADMIN_IDS)")
    if missing:
        raise SystemExit(
            "Missing required environment variables: " + ", ".join(missing)
        )
