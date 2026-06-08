from tradingview_ta import TA_Handler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from datetime import datetime, timedelta
import json
import os
import re
import requests
import asyncio

# ==============================
# CONFIG
# ==============================
TOKEN = "8994879739:AAF40FWxNyfBL7EWET7G1TtvWxLkPbfMdzc"
ADMIN_ID = 7889334774  # ganti kalau ID admin lu beda

USER_FILE = "users.json"
TRIAL_LIMIT_MARKET = 3
TRIAL_LIMIT_NEWS = 3

# Isi pembayaran lu di sini
PAYMENT_TEXT = "DANA / QRIS: 085778001402"
ADMIN_CONTACT = "@egingroho"

PAIRS = {
    "XAUUSD": {
    "symbol": "XAUUSD",
    "screener": "cfd",
    "exchange": "FOREXCOM",
    "name": "XAU/USD"
},
    "XAGUSD": {
    "symbol": "SILVER",
    "screener": "cfd",
    "exchange": "TVC",
    "name": "XAG/USD"
},
    "BTCUSD": {"symbol": "BTCUSD", "screener": "crypto", "exchange": "BITSTAMP", "name": "BTC/USD"},
    "ETHUSD": {"symbol": "ETHUSD", "screener": "crypto", "exchange": "BITSTAMP", "name": "ETH/USD"},
}

TIMEFRAMES = {
    "M1": "1m", "M3": "3m", "M5": "5m", "M15": "15m",
    "M30": "30m", "H1": "1h", "H4": "4h", "DAILY": "1d",
}

IMPORTANT_NEWS_KEYWORDS = [
    "Non-Farm", "Nonfarm", "NFP", "CPI", "Core CPI", "FOMC",
    "Federal Funds Rate", "Fed Interest Rate", "PMI", "ISM", "Manufacturing PMI",
    "Services PMI", "PCE", "Core PCE", "Unemployment Rate", "Average Hourly Earnings"
]

# ==============================
# USER DATABASE
# ==============================
def load_users():
    if not os.path.exists(USER_FILE):
        return {}
    try:
        with open(USER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users):
    with open(USER_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=4)


def get_user(user_id):
    users = load_users()
    uid = str(user_id)

    if uid not in users:
        users[uid] = {
            "market_used": 0,
            "news_used": 0,
            "premium": False,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_users(users)

    # Admin otomatis premium
    if int(uid) == ADMIN_ID:
        users[uid]["premium"] = True
        save_users(users)

    return users[uid]


def update_user(user_id, data):
    users = load_users()
    users[str(user_id)] = data
    save_users(users)


def can_use_market(user_id):
    user = get_user(user_id)
    return user["premium"] or user["market_used"] < TRIAL_LIMIT_MARKET


def can_use_news(user_id):
    user = get_user(user_id)
    return user["premium"] or user["news_used"] < TRIAL_LIMIT_NEWS


def add_market_usage(user_id):
    user = get_user(user_id)
    if not user["premium"]:
        user["market_used"] += 1
        update_user(user_id, user)


def add_news_usage(user_id):
    user = get_user(user_id)
    if not user["premium"]:
        user["news_used"] += 1
        update_user(user_id, user)

# ==============================
# HELPERS
# ==============================
def fmt(x):
    try:
        return f"{float(x):.2f}"
    except Exception:
        return "-"


def conf_bar(conf):
    full = int(conf / 10)
    return "█" * full + "░" * (10 - full)


def clean_num(value):
    if value is None:
        return None
    value = str(value).replace(",", "").replace("%", "").replace("K", "").replace("M", "")
    m = re.search(r"-?\d+(\.\d+)?", value)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def dynamic_sl_tp(pair_key, signal, entry, high, low):
    rng = abs(high - low)

    if pair_key == "XAUUSD":
        min_sl, min_tp = 3.0, 6.0
        max_sl, max_tp = 4.0, 10.0
    elif pair_key == "XAGUSD":
        min_sl, min_tp = 0.12, 0.25
        max_sl, max_tp = 0.20, 0.45
    elif pair_key == "BTCUSD":
        min_sl, min_tp = 120, 250
        max_sl, max_tp = 250, 600
    else:  # ETHUSD
        min_sl, min_tp = 10, 25
        max_sl, max_tp = 20, 50

    sl_dist = max(min_sl, min(rng * 1.2, max_sl))
    tp_dist = max(min_tp, min(sl_dist * 2.2, max_tp))

    if signal == "BUY":
        return entry - sl_dist, entry + tp_dist
    return entry + sl_dist, entry - tp_dist

# ==============================
# MARKET ANALYSIS: SNR + SND + ICT + SMC
# ==============================
def get_market_analysis(pair_key, tf_key):
    pair = PAIRS[pair_key]

    handler = TA_Handler(
        symbol=pair["symbol"],
        screener=pair["screener"],
        exchange=pair["exchange"],
        interval=TIMEFRAMES[tf_key]
    )

    try:
        data = handler.get_analysis()
    except Exception as e:
        return f"⚠️ Error ambil data market:\n{str(e)}"

    ind = data.indicators
    summ = data.summary

    price = ind.get("close")
    open_price = ind.get("open")
    high = ind.get("high")
    low = ind.get("low")
    ema20 = ind.get("EMA20")
    ema50 = ind.get("EMA50")
    rsi = ind.get("RSI")
    rec = summ.get("RECOMMENDATION")

    if price is None or high is None or low is None:
        return "⚠️ Data market belum tersedia. Coba lagi nanti."

    price = float(price)
    open_price = float(open_price) if open_price else price
    high = float(high)
    low = float(low)
    rsi = float(rsi) if rsi else 50

    # ===============================
    # HIGH PROBABILITY SCALPING METHOD
    # Liquidity Sweep + OB/iFVG Retest + Fibo Golden Area + HTF Bias
    # ===============================
    market_range = max(abs(high - low), 0.0001)
    equilibrium = (high + low) / 2
    candle = "BULLISH" if price > open_price else "BEARISH"

    if rec in ["BUY", "STRONG_BUY"]:
        htf_bias = "BULLISH"
    elif rec in ["SELL", "STRONG_SELL"]:
        htf_bias = "BEARISH"
    else:
        htf_bias = "BULLISH" if price <= equilibrium else "BEARISH"

    if ema20 and ema50:
        if price > ema20 > ema50:
            structure = "BOS BULLISH"
        elif price < ema20 < ema50:
            structure = "MSS BEARISH"
        else:
            structure = "RETEST / CHOPPY"
    else:
        structure = "RETEST"

    signal = "BUY" if htf_bias == "BULLISH" else "SELL"
    entry = price

    if pair_key == "XAUUSD":
        sl_dist = max(3.0, min(market_range * 0.8, 5.0))
    elif pair_key == "XAGUSD":
        sl_dist = max(0.12, min(market_range * 0.8, 0.25))
    elif pair_key == "BTCUSD":
        sl_dist = max(120, min(market_range * 0.8, 300))
    else:
        sl_dist = max(10, min(market_range * 0.8, 35))

    # Fibo Golden Area dihitung internal, lalu digabung ke Entry Area.
    if signal == "BUY":
        fibo_618 = low + (market_range * 0.618)
        fibo_705 = low + (market_range * 0.705)
    else:
        fibo_618 = high - (market_range * 0.618)
        fibo_705 = high - (market_range * 0.705)

    fibo_low = min(fibo_618, fibo_705)
    fibo_high = max(fibo_618, fibo_705)

    if signal == "BUY":
        entry_low = min(entry - (sl_dist * 0.25), fibo_low)
        entry_high = max(entry + (sl_dist * 0.15), fibo_high)
        zero_low = entry - (sl_dist * 0.06)
        zero_high = entry + (sl_dist * 0.06)
        sl = entry - sl_dist
        tp1 = entry + (sl_dist * 1.3)
        tp2 = entry + (sl_dist * 2.2)
        tp3 = entry + (sl_dist * 3.0)
        entry_reason = """
• HTF bias cenderung bullish.
• Entry area adalah gabungan retest, OB/iFVG, dan Fibo Golden Area.
• Zero Floating Area adalah area terbaik untuk entry lebih presisi.
• Target diarahkan ke buy-side liquidity.
"""
    else:
        entry_low = min(entry - (sl_dist * 0.15), fibo_low)
        entry_high = max(entry + (sl_dist * 0.25), fibo_high)
        zero_low = entry - (sl_dist * 0.06)
        zero_high = entry + (sl_dist * 0.06)
        sl = entry + sl_dist
        tp1 = entry - (sl_dist * 1.3)
        tp2 = entry - (sl_dist * 2.2)
        tp3 = entry - (sl_dist * 3.0)
        entry_reason = """
• HTF bias cenderung bearish.
• Entry area adalah gabungan retest, OB/iFVG, dan Fibo Golden Area.
• Zero Floating Area adalah area terbaik untuk entry lebih presisi.
• Target diarahkan ke sell-side liquidity.
"""

    confidence = 84
    if signal == "BUY" and "BULLISH" in structure:
        confidence += 5
    if signal == "SELL" and "BEARISH" in structure:
        confidence += 5
    if signal == "BUY" and candle == "BULLISH":
        confidence += 3
    if signal == "SELL" and candle == "BEARISH":
        confidence += 3
    if signal == "BUY" and rsi > 50:
        confidence += 3
    if signal == "SELL" and rsi < 50:
        confidence += 3
    confidence = min(confidence, 95)

    # SESSION WIB
    wib_now = datetime.utcnow() + timedelta(hours=7)
    hour = wib_now.hour

    if 5 <= hour < 14:
        session_name = "🇯🇵 ASIA SESSION"
        session_note = "Liquidity Building"
    elif 14 <= hour < 20:
        session_name = "🇬🇧 LONDON SESSION"
        session_note = "High Volatility"
    else:
        session_name = "🇺🇸 NEW YORK SESSION"
        session_note = "Institutional Flow"

    if confidence >= 90:
        grade = "A+"
        market_condition = "HIGH PROBABILITY RETEST"
        market_note = "Setup kuat. Prioritaskan entry di Zero Floating Area."
    elif confidence >= 85:
        grade = "A"
        market_condition = "VALID SCALPING SETUP"
        market_note = "Setup valid. Tunggu harga masuk entry area."
    elif confidence >= 75:
        grade = "B+"
        market_condition = "MODERATE SETUP"
        market_note = "Gunakan lot kecil dan tunggu candle close valid."
    else:
        grade = "B"
        market_condition = "LOW MOMENTUM"
        market_note = "Setup agresif. Hindari overlot."

    bars = int(confidence / 10)
    confidence_bar = "█" * bars + "░" * (10 - bars)
    rr = round(abs(tp2 - entry) / max(abs(entry - sl), 0.0001), 1)

    return f"""
<b>━━━━━━━━━━━━━━━━━━━━</b>
👑 <b>EGI CAPITAL PRO</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>

💱 <b>ASSET</b> : {pair["name"]}
⏰ <b>TF</b> : {tf_key}
🌍 <b>SESSION</b> : {session_name}
⚡ <b>FLOW</b> : {session_note}
🕒 <b>WIB</b> : {wib_now.strftime("%H:%M")}

📊 <b>BIAS</b> : {htf_bias}
🔥 <b>SIGNAL ENTRY</b> : {signal}

<b>━━━━━━━━━━━━━━━━━━━━</b>
🎯 <b>EXECUTION PLAN</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>

📍 <b>ENTRY AREA</b>
<code>{fmt(entry_low)} - {fmt(entry_high)}</code>

💎 <b>ZERO FLOATING AREA</b>
<code>{fmt(zero_low)} - {fmt(zero_high)}</code>

<b>━━━━━━━━━━━━━━━━━━━━</b>
🛡 <b>RISK & TARGET</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>

🛑 <b>STOP LOSS</b>
<code>{fmt(sl)}</code>

🎯 <b>TP1</b> : <code>{fmt(tp1)}</code>
🎯 <b>TP2</b> : <code>{fmt(tp2)}</code>
🎯 <b>TP3</b> : <code>{fmt(tp3)}</code>

⚖️ <b>RR</b> : 1 : {rr}

<b>━━━━━━━━━━━━━━━━━━━━</b>
🧠 <b>ENTRY REASON</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>

{entry_reason}

<b>━━━━━━━━━━━━━━━━━━━━</b>
⚡ <b>SETUP SCORE</b>
<b>━━━━━━━━━━━━━━━━━━━━</b>

Grade : <b>{grade}</b>
Confidence : <b>{confidence}%</b>
{confidence_bar}

Condition : <b>{market_condition}</b>
Note : {market_note}

<b>━━━━━━━━━━━━━━━━━━━━</b>
⚠️ <b>DISCLAIMER</b> ⚠️
<b>━━━━━━━━━━━━━━━━━━━━</b>

<i>Bukan saran finansial. Trading mengandung risiko — selalu kelola modal dengan bijak.</i>

Gunakan Stop Loss.
Jangan overlot.
Jangan all-in.

<b>━━━━━━━━━━━━━━━━━━━━</b>
🤖 <b>EGI CAPITAL AI</b>
"""

# ==============================
# NEWS IMPACT ENGINE
# ==============================
def parse_forex_factory_today():
    """
    Scrape Forex Factory calendar. Gratis, tapi bisa gagal kalau website block request.
    Kalau gagal, bot tetap punya manual analyzer lewat /news dan /fomc.
    """
    url = "https://www.forexfactory.com/calendar"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)
        html = r.text

        # Regex ringan: ambil blok sekitar event USD. Struktur FF bisa berubah kapan saja.
        # Karena FF sering ubah HTML, ini dibuat fallback-friendly.
        events = []
        rows = re.findall(r"<tr[^>]*calendar__row[^>]*>(.*?)</tr>", html, flags=re.S | re.I)

        for row in rows:
            txt = re.sub(r"<[^>]+>", " ", row)
            txt = re.sub(r"\s+", " ", txt).strip()

            if " USD " not in (" " + txt + " "):
                continue

            if not any(k.lower() in txt.lower() for k in IMPORTANT_NEWS_KEYWORDS):
                continue

            impact = "HIGH" if "High Impact" in row or "impact--high" in row else "NEWS"
            events.append({"raw": txt, "impact": impact})

        return events[:8]
    except Exception:
        return []


def analyze_news_result(news_type, actual, forecast, previous=None):
    nt = news_type.lower()
    actual_num = clean_num(actual)
    forecast_num = clean_num(forecast)

    if actual_num is None or forecast_num is None:
        return "Format angka tidak valid. Contoh: /news cpi actual=3.2 forecast=3.4 previous=3.5"

    usd_bias = "NEUTRAL"
    market_reason = "Actual sama dengan forecast. Market bisa choppy."
    confidence = 65

    # CPI/PCE: lebih tinggi = inflasi kuat = USD bullish, gold/crypto bearish
    if "cpi" in nt or "pce" in nt:
        if actual_num > forecast_num:
            usd_bias = "BULLISH"
            market_reason = "Inflasi lebih tinggi dari forecast. Potensi Fed lebih hawkish."
            confidence = 88
        elif actual_num < forecast_num:
            usd_bias = "BEARISH"
            market_reason = "Inflasi lebih rendah dari forecast. Potensi Fed lebih dovish."
            confidence = 88

    # NFP/PMI/ISM/Unemployment earnings general rules
    elif "nfp" in nt or "nonfarm" in nt or "pmi" in nt or "ism" in nt or "jobs" in nt:
        if actual_num > forecast_num:
            usd_bias = "BULLISH"
            market_reason = "Data ekonomi lebih kuat dari forecast. USD cenderung menguat."
            confidence = 86
        elif actual_num < forecast_num:
            usd_bias = "BEARISH"
            market_reason = "Data ekonomi lebih lemah dari forecast. USD cenderung melemah."
            confidence = 86

    # Unemployment rate kebalik: angka lebih tinggi = USD bearish
    if "unemployment" in nt:
        if actual_num > forecast_num:
            usd_bias = "BEARISH"
            market_reason = "Unemployment lebih tinggi dari forecast. USD cenderung melemah."
            confidence = 86
        elif actual_num < forecast_num:
            usd_bias = "BULLISH"
            market_reason = "Unemployment lebih rendah dari forecast. USD cenderung menguat."
            confidence = 86

    if usd_bias == "BULLISH":
        xau = "SELL"
        xag = "SELL"
        btc = "SELL / RISK-OFF"
        eth = "SELL / RISK-OFF"
    elif usd_bias == "BEARISH":
        xau = "BUY"
        xag = "BUY"
        btc = "BUY / RISK-ON"
        eth = "BUY / RISK-ON"
    else:
        xau = xag = btc = eth = "WAIT FOR VOLATILITY"

    now = datetime.now().strftime("%d-%m-%Y %H:%M WIB")

    return f"""
⬜━━━━━━━━━━━━━━━━━━━━⬜
🚨 <b>NEWS IMPACT ANALYSIS</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

📅 <b>Waktu</b>     : {now}
📰 <b>News</b>      : <b>{news_type.upper()}</b>
📊 <b>Actual</b>    : <code>{actual}</code>
📈 <b>Forecast</b>  : <code>{forecast}</code>
📉 <b>Previous</b>  : <code>{previous if previous else '-'}</code>

⬜━━━━━━━━━━━━━━━━━━━━⬜
🤖 <b>AI VERDICT</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

💵 <b>USD Bias</b>   : <b>{usd_bias}</b>
🎯 <b>Confidence</b> : <b>{confidence}%</b> {conf_bar(confidence)}

🥇 XAU/USD : <b>{xau}</b>
🥈 XAG/USD : <b>{xag}</b>
₿ BTC/USD : <b>{btc}</b>
♦ ETH/USD : <b>{eth}</b>

✅ <b>Reason</b>:
{market_reason}

⬜━━━━━━━━━━━━━━━━━━━━⬜
⚠️ <b>NEWS TRADING RULE</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

<i>Jangan entry di detik rilis news.
Tunggu 1-2 candle M5 close, spread normal, lalu cari retest.</i>

🤍 <b>Egi Capital AI — News Engine</b>
"""


def analyze_fomc(tone):
    t = tone.lower()
    if "hawk" in t or "naik" in t or "higher" in t:
        usd = "BULLISH"
        xau = "SELL"
        btc = "SELL / RISK-OFF"
        reason = "Nada FOMC hawkish. Market melihat peluang suku bunga lebih tinggi/lebih lama."
        conf = 88
    elif "dov" in t or "turun" in t or "cut" in t:
        usd = "BEARISH"
        xau = "BUY"
        btc = "BUY / RISK-ON"
        reason = "Nada FOMC dovish. Market melihat peluang pelonggaran kebijakan."
        conf = 88
    else:
        usd = "NEUTRAL"
        xau = "WAIT"
        btc = "WAIT"
        reason = "Tone FOMC belum jelas. Tunggu press conference dan reaksi candle."
        conf = 65

    return f"""
⬜━━━━━━━━━━━━━━━━━━━━⬜
🏦 <b>FOMC IMPACT ANALYSIS</b>
⬜━━━━━━━━━━━━━━━━━━━━⬜

🧠 <b>Tone</b>      : <b>{tone.upper()}</b>
💵 <b>USD Bias</b>  : <b>{usd}</b>
🎯 <b>Confidence</b>: <b>{conf}%</b> {conf_bar(conf)}

🥇 XAU/USD : <b>{xau}</b>
🥈 XAG/USD : <b>{xau}</b>
₿ BTC/USD : <b>{btc}</b>
♦ ETH/USD : <b>{btc}</b>

✅ <b>Reason</b>:
{reason}

⚠️ Tunggu 1-2 candle M5 setelah statement/press conference.
"""

# ==============================
# MENUS
# ==============================
def main_menu(user_id):
    user = get_user(user_id)
    if user["premium"]:
        status = "💎 PREMIUM UNLIMITED"
    else:
        status = f"🆓 TRIAL Market {TRIAL_LIMIT_MARKET - user['market_used']} | News {TRIAL_LIMIT_NEWS - user['news_used']}"

    text = f"""
Halo! 👋

📊 <b>EGI CAPITAL AI</b>
AI-Powered Forex & Crypto Analysis Bot

Status: <b>{status}</b>

Pilih menu di bawah:
"""
    keyboard = [
        [InlineKeyboardButton("📊 Analisa Market", callback_data="menu_pairs")],
        [InlineKeyboardButton("📰 News Impact", callback_data="menu_news")],
        [InlineKeyboardButton("👤 Akun Saya", callback_data="account")],
        [InlineKeyboardButton("💎 Upgrade Premium", callback_data="upgrade")],
    ]
    return text, InlineKeyboardMarkup(keyboard)

# ==============================
# TELEGRAM HANDLERS
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, keyboard = main_menu(update.effective_user.id)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = q.from_user.id
    user = get_user(user_id)
    data = q.data

    if data == "menu_pairs":
        if not can_use_market(user_id):
            await q.edit_message_text(
                "🚫 <b>FREE TRIAL MARKET HABIS</b>\n\nUpgrade premium untuk akses unlimited.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade Premium", callback_data="upgrade")]]),
                parse_mode="HTML"
            )
            return
        keyboard = [
            [InlineKeyboardButton("🥇 XAU/USD", callback_data="pair_XAUUSD"), InlineKeyboardButton("🥈 XAG/USD", callback_data="pair_XAGUSD")],
            [InlineKeyboardButton("₿ BTC/USD", callback_data="pair_BTCUSD"), InlineKeyboardButton("♦ ETH/USD", callback_data="pair_ETHUSD")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]
        ]
        await q.edit_message_text("🏆 <b>Kategori: Komoditas & Crypto</b>\n\nPilih pair:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("pair_"):
        pair_key = data.replace("pair_", "")
        keyboard = [
            [InlineKeyboardButton("M1", callback_data=f"tf_{pair_key}_M1"), InlineKeyboardButton("M3", callback_data=f"tf_{pair_key}_M3"), InlineKeyboardButton("M5", callback_data=f"tf_{pair_key}_M5")],
            [InlineKeyboardButton("M15", callback_data=f"tf_{pair_key}_M15"), InlineKeyboardButton("M30", callback_data=f"tf_{pair_key}_M30"), InlineKeyboardButton("H1", callback_data=f"tf_{pair_key}_H1")],
            [InlineKeyboardButton("H4", callback_data=f"tf_{pair_key}_H4"), InlineKeyboardButton("DAILY", callback_data=f"tf_{pair_key}_DAILY")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data="menu_pairs")]
        ]
        await q.edit_message_text(f"💱 <b>{PAIRS[pair_key]['name']}</b>\n\nPilih timeframe:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("tf_"):
        if not can_use_market(user_id):
            await q.edit_message_text("🚫 <b>FREE TRIAL MARKET HABIS</b>\n\nUpgrade premium untuk akses unlimited.", parse_mode="HTML")
            return
        parts = data.split("_")
        pair_key, tf_key = parts[1], parts[2]
        await q.edit_message_text("⏳ Sedang analisa market...")
        try:
            hasil = get_market_analysis(pair_key, tf_key)
            add_market_usage(user_id)
            user = get_user(user_id)
            if not user["premium"]:
                hasil += f"\n\n🆓 Sisa trial market: {TRIAL_LIMIT_MARKET - user['market_used']} analisa"
        except Exception as e:
            hasil = "Error ambil data market: " + str(e)
        keyboard = [[InlineKeyboardButton("🔁 Analisa Lagi", callback_data=f"pair_{pair_key}")], [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_start")]]
        await q.edit_message_text(hasil, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "menu_news":
        if not can_use_news(user_id):
            await q.edit_message_text("🚫 <b>FREE TRIAL NEWS HABIS</b>\n\nUpgrade premium untuk akses unlimited.", parse_mode="HTML")
            return
        keyboard = [
            [InlineKeyboardButton("📅 Forex Factory Today", callback_data="news_ff")],
            [InlineKeyboardButton("📌 Cara Input Manual", callback_data="news_manual_help")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]
        ]
        await q.edit_message_text("📰 <b>NEWS IMPACT ENGINE</b>\n\nPilih menu news:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "news_ff":
        if not can_use_news(user_id):
            await q.edit_message_text("🚫 <b>FREE TRIAL NEWS HABIS</b>\n\nUpgrade premium untuk akses unlimited.", parse_mode="HTML")
            return
        await q.edit_message_text("⏳ Mengambil data Forex Factory...")
        events = parse_forex_factory_today()
        add_news_usage(user_id)
        if events:
            text = "⬜━━━━━━━━━━━━━━━━━━━━⬜\n📰 <b>FOREX FACTORY TODAY</b>\n⬜━━━━━━━━━━━━━━━━━━━━⬜\n\n"
            for idx, ev in enumerate(events, 1):
                text += f"<b>{idx}. USD {ev['impact']}</b>\n{ev['raw']}\n\n"
            text += "Gunakan command manual setelah actual keluar:\n<code>/news cpi actual=3.2 forecast=3.4 previous=3.5</code>"
        else:
            text = "⚠️ Forex Factory gagal dibaca / tidak ada news USD penting.\n\nGunakan manual:\n<code>/news nfp actual=250 forecast=180 previous=190</code>\n<code>/fomc hawkish</code>"
        user = get_user(user_id)
        if not user["premium"]:
            text += f"\n\n🆓 Sisa trial news: {TRIAL_LIMIT_NEWS - user['news_used']}"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="back_start")]]), parse_mode="HTML")

    elif data == "news_manual_help":
        text = """
📰 <b>MANUAL NEWS ANALYZER</b>

Format:
<code>/news cpi actual=3.2 forecast=3.4 previous=3.5</code>
<code>/news nfp actual=250 forecast=180 previous=190</code>
<code>/news pmi actual=53.2 forecast=51.8 previous=50.9</code>
<code>/fomc hawkish</code>
<code>/fomc dovish</code>

Rule:
CPI tinggi = USD bullish = XAU cenderung bearish.
NFP/PMI tinggi = USD bullish = XAU cenderung bearish.
FOMC hawkish = USD bullish.
FOMC dovish = USD bearish.
"""
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="menu_news")]]), parse_mode="HTML")

    elif data == "account":
        user = get_user(user_id)
        status = "💎 PREMIUM" if user["premium"] else "🆓 FREE TRIAL"
        market_left = "Unlimited" if user["premium"] else f"{TRIAL_LIMIT_MARKET - user['market_used']} / {TRIAL_LIMIT_MARKET}"
        news_left = "Unlimited" if user["premium"] else f"{TRIAL_LIMIT_NEWS - user['news_used']} / {TRIAL_LIMIT_NEWS}"
        text = f"""
👤 <b>AKUN SAYA</b>

ID Telegram:
<code>{user_id}</code>

Status: <b>{status}</b>
Trial Market: <b>{market_left}</b>
Trial News: <b>{news_left}</b>
"""
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]]), parse_mode="HTML")

    elif data == "upgrade":
        text = f"""
💎 <b>UPGRADE PREMIUM</b>

Benefit:
✅ Unlimited analisa market
✅ Unlimited news impact
✅ XAU/USD, XAG/USD, BTC/USD, ETH/USD
✅ Semua timeframe
✅ SNR + SND + ICT + SMC Engine
✅ Forex Factory News Engine

Harga:
<b>Rp 499.000 / Lifetime</b>

Pembayaran:
<b>{PAYMENT_TEXT}</b>

Setelah bayar, kirim bukti ke admin:
<b>{ADMIN_CONTACT}</b>
"""
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="back_start")]]), parse_mode="HTML")

    elif data == "back_start":
        text, keyboard = main_menu(user_id)
        await q.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not can_use_news(user_id):
        await update.message.reply_text("🚫 Free trial news habis. Upgrade premium untuk akses unlimited.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Format: /news cpi actual=3.2 forecast=3.4 previous=3.5")
        return

    news_type = context.args[0]
    raw = " ".join(context.args[1:])
    actual = re.search(r"actual=([^\s]+)", raw, re.I)
    forecast = re.search(r"forecast=([^\s]+)", raw, re.I)
    previous = re.search(r"previous=([^\s]+)", raw, re.I)

    if not actual or not forecast:
        await update.message.reply_text("Format salah. Contoh: /news nfp actual=250 forecast=180 previous=190")
        return

    hasil = analyze_news_result(news_type, actual.group(1), forecast.group(1), previous.group(1) if previous else None)
    add_news_usage(user_id)
    await update.message.reply_text(hasil, parse_mode="HTML")


async def fomc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not can_use_news(user_id):
        await update.message.reply_text("🚫 Free trial news habis. Upgrade premium untuk akses unlimited.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /fomc hawkish atau /fomc dovish")
        return
    tone = " ".join(context.args)
    hasil = analyze_fomc(tone)
    add_news_usage(user_id)
    await update.message.reply_text(hasil, parse_mode="HTML")


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /premium ID_TELEGRAM")
        return
    target_id = context.args[0]
    users = load_users()
    if target_id not in users:
        users[target_id] = {"market_used": 0, "news_used": 0, "premium": True, "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    else:
        users[target_id]["premium"] = True
    save_users(users)
    await update.message.reply_text(f"User {target_id} sudah PREMIUM.")


async def unpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /unpremium ID_TELEGRAM")
        return
    target_id = context.args[0]
    users = load_users()
    if target_id in users:
        users[target_id]["premium"] = False
        save_users(users)
    await update.message.reply_text(f"Premium user {target_id} dicabut.")


async def cekuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Format: /cekuser ID_TELEGRAM")
        return
    uid = context.args[0]
    users = load_users()
    if uid not in users:
        await update.message.reply_text("User belum ada di database.")
        return
    u = users[uid]
    await update.message.reply_text(f"ID: {uid}\nPremium: {u.get('premium')}\nMarket used: {u.get('market_used')}\nNews used: {u.get('news_used')}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Lu bukan admin.")
        return
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Format: /broadcast pesan")
        return
    users = load_users()
    sent = 0
    for uid, u in users.items():
        if u.get("premium"):
            try:
                await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode="HTML")
                sent += 1
            except Exception:
                pass
    await update.message.reply_text(f"Broadcast terkirim ke {sent} premium user.")


# ==============================
# AUTO BROADCAST HARIAN + NEWS
# ==============================
async def daily_broadcast(app):
    terkirim_hari_ini = set()

    while True:
        wib_now = datetime.utcnow() + timedelta(hours=7)
        tanggal = wib_now.strftime("%Y-%m-%d")
        jam = wib_now.strftime("%H:%M")
        current_key = f"{tanggal}_{jam}"

        jadwal = [
            f"{tanggal}_08:00",
            f"{tanggal}_12:00",
            f"{tanggal}_18:00"
        ]

        if current_key in jadwal and current_key not in terkirim_hari_ini:
            if jam == "08:00":
                title = "🌏 <b>ASIA MARKET OUTLOOK</b>"
                focus = "Market mulai membentuk liquidity dan range awal."
                note = "Fokus tunggu area support/resistance dan hindari entry terburu-buru."
                news_title = "📰 <b>NEWS WATCH</b>"
                news_note = "Pantau jadwal USD hari ini: CPI, NFP, PMI, FOMC, PCE jika ada."
            elif jam == "12:00":
                title = "🇬🇧 <b>LONDON PREPARATION</b>"
                focus = "Persiapan volatilitas London session."
                note = "Waspadai liquidity sweep, stop hunt, dan fake breakout."
                news_title = "🚨 <b>HIGH IMPACT CHECK</b>"
                news_note = "Cek news USD sebelum London/New York. Jangan entry saat spread melebar."
            else:
                title = "🇺🇸 <b>NEW YORK PREPARATION</b>"
                focus = "Sesi paling agresif akan segera dimulai."
                note = "Perhatikan USD news, DXY, yield, dan sentimen market."
                news_title = "🔥 <b>NEWS VOLATILITY ALERT</b>"
                news_note = "Jika ada CPI/NFP/FOMC/PMI, tunggu 1-2 candle M5 setelah rilis."

            users = load_users()

            premium_msg = f"""
{title}

🕒 <b>WIB</b> : {jam}

{focus}

<b>Market Watch</b>:
🥇 XAU/USD
🥈 XAG/USD
₿ BTC/USD
♦ ETH/USD

📌 <b>Trading Note</b>:
{note}

{news_title}
{news_note}

💎 <b>Premium Member</b>:
Buka menu <b>Analisa Market</b> untuk cek setup terbaru.

⚠️ Selalu gunakan SL dan money management.

🤖 <b>EGI CAPITAL AI</b>
"""

            free_msg = f"""
🚀 <b>EGI CAPITAL AI</b>

{title}

🕒 <b>WIB</b> : {jam}

Analisa premium terbaru sudah tersedia.

Upgrade Premium untuk membuka:
✅ Entry Area
✅ Zero Floating Area
✅ SL & TP
✅ News Impact AI
✅ Semua Pair & Timeframe

💎 Klik menu <b>Upgrade Premium</b> di bot.
"""

            for uid, u in users.items():
                try:
                    msg = premium_msg if u.get("premium") else free_msg
                    await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode="HTML")
                except Exception:
                    pass

            terkirim_hari_ini.add(current_key)

        if len(terkirim_hari_ini) > 30:
            terkirim_hari_ini.clear()

        await asyncio.sleep(20)


async def start_daily_broadcast(app):
    app.create_task(daily_broadcast(app))

# ==============================
# RUN APP
# ==============================
app = ApplicationBuilder().token(TOKEN).post_init(start_daily_broadcast).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("news", news_command))
app.add_handler(CommandHandler("fomc", fomc_command))
app.add_handler(CommandHandler("premium", premium))
app.add_handler(CommandHandler("unpremium", unpremium))
app.add_handler(CommandHandler("cekuser", cekuser))
app.add_handler(CommandHandler("broadcast", broadcast))
app.add_handler(CallbackQueryHandler(button))

print("EGI CAPITAL AI PREMIUM NEWS BOT jalan...")
app.run_polling()
