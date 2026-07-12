# 🎟️ Coupon Selling Bot (Upgraded)

A fast, secure, scalable Telegram bot for selling coupons with a **MongoDB-backed
wallet system** and **automatic UPI wallet recharge** via Gmail verification.

Everything (users, wallet balances, coupons, transactions, purchase history) is
stored in MongoDB, so **no data is ever lost** on bot updates, restarts, or
Railway redeploys.

---

## ✨ Features

### 💼 Wallet System
- Secure wallet for every user, permanently linked to their account in MongoDB.
- Balance survives bot updates, restarts, and redeploys.
- Atomic balance updates — concurrent purchases can never double-spend.

### ➕ Auto Wallet Recharge
- User pays via UPI, then sends the **Transaction ID / UTR**.
- Bot searches your Gmail bank-alert emails, verifies the payment & amount, and
  **credits the wallet automatically** — usually within 1–2 minutes.
- The exact amount from the email is credited.
- Each transaction ID can only be used **once** (anti-replay protection).

### 🛒 Wallet Usage
- Buy coupons anytime using wallet balance.
- Balance updates instantly after every recharge and purchase.
- Coupon codes are delivered instantly on purchase; auto-refund if stock vanishes.

### 🛠️ Admin Control Panel (`/admin`)
- **Manage Coupons** — add / edit / delete categories, add stock (one code per line).
- **Manage Users** — ban / unban.
- **Wallet Control** — add / deduct / check any user's balance.
- **Transactions** — view the recent wallet ledger.
- **Analytics** — users, revenue, recharges, wallet liability, stock, top categories.
- **Announcement** — broadcast a message to all users.
- **Settings** — UPI ID, payee name, maintenance mode toggle.

### ⚡ Performance
- Async MongoDB (Motor) with connection pooling.
- `concurrent_updates(True)` — many users handled in parallel without lag.
- Blocking Gmail IMAP runs in a thread so the bot never freezes.
- Runs in long-polling mode — perfect for the Railway free plan (no domain needed).

---

## 🚀 Deploy to Railway (GitHub → Railway)

### 1. Create a free MongoDB database
1. Go to <https://www.mongodb.com/cloud/atlas> and create a **free M0 cluster**.
2. Create a database user (username + password).
3. Network Access → **Allow access from anywhere** (`0.0.0.0/0`).
4. Click **Connect → Drivers** and copy the connection string. It looks like:
   `mongodb+srv://USER:PASS@cluster.xxxxx.mongodb.net/?retryWrites=true&w=majority`

### 2. Create a Gmail App Password (for auto recharge)
1. Enable **2-Step Verification** on your Google account.
2. Go to <https://myaccount.google.com/apppasswords> and create a 16-char app password.
3. Make sure your bank/UPI app sends transaction alert emails to this Gmail.

### 3. Push this code to GitHub
```bash
git init
git add .
git commit -m "Upgraded coupon bot with wallet + MongoDB"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### 4. Deploy on Railway
1. <https://railway.app> → **New Project → Deploy from GitHub repo**.
2. Pick your repo. Railway auto-detects Python (NIXPACKS).
3. Go to **Variables** and add everything from `.env.example` (see below).
4. Railway builds and runs `python main.py` automatically.

> **Free plan note:** Railway's free credits reset periodically. Just redeploy
> from GitHub when needed — because all data lives in MongoDB Atlas, **nothing is
> lost** between redeploys.

---

## 🔑 Required Environment Variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | From [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | ✅ | Comma-separated Telegram numeric IDs (admins) |
| `ADMIN_CHAT_ID` | recommended | Where sale/recharge alerts are sent |
| `MONGO_URI` | ✅ | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | ✅ | e.g. `coupon_bot` |
| `GMAIL_ADDRESS` | for auto-recharge | Gmail that receives bank alerts |
| `GMAIL_APP_PASSWORD` | for auto-recharge | 16-char Gmail App Password |
| `SENDER_FILTER` | optional | Only scan emails from this address |
| `UPI_ID` | ✅ | Your UPI ID shown to buyers |
| `PAYEE_NAME` | optional | Name shown to buyers |
| `BOT_NAME` / `CURRENCY_SYMBOL` | optional | Branding |
| `WEBHOOK_URL` | optional | Leave blank for polling (recommended) |

Find your Telegram ID by messaging [@userinfobot](https://t.me/userinfobot).

---

## 🧪 Run locally
```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your values
python main.py
```

---

## 📁 Project Structure
```
coupon-bot-upgraded/
├── main.py              # entry point (polling/webhook)
├── config.py           # env-based configuration
├── database.py         # MongoDB async data layer (wallet, atomic ops)
├── gmail_checker.py    # IMAP UPI transaction verification
├── messages.py         # message templates
├── keyboards.py        # inline keyboards
├── handlers/
│   ├── user.py         # start, browse, wallet, orders
│   ├── payment.py      # recharge (Gmail) + purchase from wallet
│   └── admin.py        # full admin dashboard
├── utils/helpers.py    # shared helpers
├── requirements.txt
├── Procfile            # worker: python main.py
├── railway.json
├── runtime.txt
└── .env.example
```

---

## 🛡️ How wallet integrity is guaranteed
- **Credits/debits** use MongoDB `$inc` inside `find_one_and_update` — atomic.
- **Debits** include a `wallet_balance >= amount` filter, so an overspend simply
  fails (returns `None`) instead of going negative.
- **Stock reservation** is atomic per code; partial claims roll back automatically.
- **Every** balance change writes a row to the `transactions` ledger.
- **UPI transaction IDs** are stored with a unique index → can't be reused.

Enjoy! 🎉
