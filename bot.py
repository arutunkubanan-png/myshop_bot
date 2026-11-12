import threading
import time
import telebot
import sqlite3
import os
import re
import glob
import random
from datetime import datetime
from telebot import types

# ============ НАСТРОЙКИ — ЗАМЕНИ НА СВОЁ ============
TOKEN = "9199418160:AAHbUIc5brvirsQTxBkqSv4QYRKkFNEbFIc"   # ← новый токен (старый отзови в @BotFather!)
ADMIN_ID = 8770504764                         # ← твой числовой ID (узнать: @userinfobot)
PAY_CARD = "2204 2402 9628 4682"    # ← номер твоей карты
MIN_DEPOSIT = 10000                 # ← минимальное пополнение
MAX_DEPOSIT = 100000                # ← максимальное пополнение
MIN_WITHDRAW = 15000                # ← минимальный вывод
MIN_PRICE = 15000                   # ← самая дешёвая картина
MAX_PRICE = 150000                  # ← самая дорогая картина
MAX_SELL_PRICE = 1000000            # ← максимум цены при перепродаже
SUPPORT_USERNAME = "@NFThub_support_bot"
PAGE_SIZE = 1
BG_FILE = "profile_bg.png"
# ===================================================

bot = telebot.TeleBot(TOKEN)

# ---------- База данных ----------
db = sqlite3.connect("shop.db", check_same_thread=False)
db_lock = threading.Lock()

class _SafeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        with db_lock:
            c = self._conn.cursor()
            c.execute(sql, params)
            self._rows = c.fetchall()
            c.close()
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

cursor = _SafeCursor(db)

cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0)")
cursor.execute("""CREATE TABLE IF NOT EXISTS purchases (
    user_id INTEGER, nft_id INTEGER, nft_name TEXT, price INTEGER, seller_id INTEGER)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
    user_id INTEGER, card TEXT, amount INTEGER, status TEXT DEFAULT 'pending')""")
cursor.execute("""CREATE TABLE IF NOT EXISTS marketplace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nft_id INTEGER, seller_id INTEGER, price INTEGER)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER, nft_id INTEGER)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, status TEXT DEFAULT 'pending')""")
cursor.execute("""CREATE TABLE IF NOT EXISTS bans (
    user_id INTEGER PRIMARY KEY,
    until REAL,
    reason TEXT)""")

cols_users = [r[1] for r in cursor.execute("PRAGMA table_info(users)").fetchall()]
if "username" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
if "full_name" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
if "verified" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
if "lang" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN lang TEXT DEFAULT 'ru'")
if "currency" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN currency TEXT DEFAULT 'RUB'")
cols_purch = [r[1] for r in cursor.execute("PRAGMA table_info(purchases)").fetchall()]
if "seller_id" not in cols_purch:
    cursor.execute("ALTER TABLE purchases ADD COLUMN seller_id INTEGER")
cols_wd = [r[1] for r in cursor.execute("PRAGMA table_info(withdrawals)").fetchall()]
if "status" not in cols_wd:
    cursor.execute("ALTER TABLE withdrawals ADD COLUMN status TEXT DEFAULT 'pending'")
if "id" not in cols_wd:
    cursor.execute("ALTER TABLE withdrawals RENAME TO withdrawals_old")
    cursor.execute("""CREATE TABLE withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, card TEXT, amount INTEGER, status TEXT DEFAULT 'pending')""")
    cursor.execute("""INSERT INTO withdrawals (user_id, card, amount, status)
        SELECT user_id, card, amount, status FROM withdrawals_old""")
    cursor.execute("DROP TABLE withdrawals_old")
    print("Миграция: таблица выводов пересоздана с колонкой id")

db.commit()

# ---------- Языки и валюты ----------
LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

# Валюты: rate = сколько единиц валюты в 1 рубле (фиксированные курсы; живые можно прикрутить API позже)
CURR = {
    "RUB":  {"rate": 1.0,       "dec": 0, "suffix": " ₽"},
    "USD":  {"rate": 1 / 80,    "dec": 2, "suffix": " $"},
    "USDT": {"rate": 1 / 80,    "dec": 2, "suffix": " USDT"},
    "BTC":  {"rate": 1.25e-7,   "dec": 8, "suffix": " BTC"},
    "ETH":  {"rate": 4.17e-6,   "dec": 6, "suffix": " ETH"},
    "TON":  {"rate": 0.0025,    "dec": 4, "suffix": " TON"},
}
CURR_LIST = ["RUB", "USD", "USDT", "BTC", "ETH", "TON"]

def get_lang(uid):
    row = cursor.execute("SELECT lang FROM users WHERE id = ?", (uid,)).fetchone()
    return row[0] if row and row[0] in LANGS else "ru"

def get_currency(uid):
    row = cursor.execute("SELECT currency FROM users WHERE id = ?", (uid,)).fetchone()
    return row[0] if row and row[0] in CURR else "RUB"

def fmt_money(rub, uid):
    c = CURR[get_currency(uid)]
    v = rub * c["rate"]
    if c["dec"] == 0:
        return f"{int(round(v)):,}".replace(",", " ") + c["suffix"]
    s = f"{v:.{c['dec']}f}".rstrip("0").rstrip(".")
    return s + c["suffix"]

def fmt_rub(n):
    return f"{n:,}".replace(",", " ")

# ---------- Переводы ----------
TXT = {
    "ru": {
        "btn_cabinet": "👤 Личный кабинет",
        "btn_market": "🏪 Площадка",
        "btn_deposit": "💳 Пополнить",
        "btn_withdraw": "💸 Вывести",
        "btn_verify": "📝 Верификация",
        "btn_fav": "❤️ Избранное",
        "btn_mynft": "🖼 Мои NFT",
        "btn_create": "➕ Создать NFT",
        "btn_settings": "⚙️ Настройки",
        "btn_transactions": "📜 Транзакции",
        "btn_support": "🆘 Поддержка",
        "greet_title": "👋 <b>Привет, {name}!</b>\n\n",
        "greet_body": "Добро пожаловать в <b>NFT Маркет</b> — место, где живут коллекционные картины 💎\n\n"
                      "🏪 <b>Площадка</b> — листай картины от дешёвых к дорогим\n"
                      "👤 <b>Личный кабинет</b> — баланс и твоя статистика\n"
                      "📝 <b>Верификация</b> — ФИО и проверка, нужна для вывода средств\n"
                      "💳 <b>Пополнить</b> — закинь баланс от {min_dep} и вперёд\n\n"
                      "👇 Жми на кнопки ниже — начнём!",
        "prof_title": "👤 <b>Личный кабинет</b>\n\n",
        "prof_balance": "💰 Баланс: <b>{v}</b>\n",
        "prof_pending": "⏳ На выводе: <b>{v}</b>\n",
        "prof_turnover": "📈 Оборот средств: <b>{v}</b>\n\n",
        "prof_collected": "🖼 Картин в коллекции: <b>{v}</b>\n",
        "prof_bought": "🛒 Куплено картин: <b>{v}</b>\n",
        "prof_sold": "📤 Продано картин: <b>{v}</b>\n\n",
        "prof_verified": "Верификация: ✅ <b>Верифицирован</b>\n",
        "prof_not_verified": "Верификация: ⚠️ <b>Не верифицирован</b>\n",
        "prof_fio": "ФИО: <b>{v}</b>\n",
        "prof_nick": "Ник: <b>@{v}</b>\n",
        "prof_id": "Ваш ID: <code>{v}</code>\n\n",
        "prof_time": "Дата и время: {v}",
        "mkt_empty": "📭 На площадке пока пусто. Загляни позже!",
        "cap_year": "📅 Год выпуска: {v}",
        "cap_price": "💰 Цена: {v}",
        "cap_price_market": "💰 Цена в каталоге: {v}",
        "cap_unique": "✨ Уникальный экземпляр — 1 из 1",
        "cap_page": "📄 Картина {p} из {n}",
        "btn_buy": "🛒 Купить за {v}",
        "btn_buy_market": "🛒 Купить на площадке за {v}",
        "btn_unlist": "❌ Снять с продажи",
        "btn_fav_add": "❤️ В избранное",
        "btn_sell": "🏪 Выставить на площадку",
        "mynft_empty": "🖼 У тебя пока нет NFT.\nКупи первый на 🏪 площадке!",
        "mynft_title": "🖼 Твои NFT",
        "fav_empty": "❤️ В избранном пусто.\nНайди картину на 🏪 площадке и нажми «❤️ В избранное» на её лоте.",
        "fav_title": "❤️ Твоё избранное:",
        "fav_no_lot": "Сейчас лота нет 😔",
        "tr_empty": "📜 Транзакций пока нет.",
        "tr_title": "📜 Последние транзакции:",
        "dep_text": "➕ Пополнение баланса:\n\n"
                    "1. Открой свой банк (Сбер, Т-Банк и т.д.)\n"
                    "2. Выбери «Перевод по номеру карты»\n"
                    "3. Переведи сумму от {min} до {max} на карту:\n"
                    "<code>{card}</code>\n"
                    "4. Сделай скриншот перевода (где видно сумму) и отправь его сюда\n\n"
                    "⚠️ Сумма меньше {min} или больше {max} не принимается!\n\n"
                    "⏳ После скриншота админ проверит перевод — баланс обновится ✅",
        "dep_photo_hint": "📸 Пришли скриншот перевода картинкой — так заявка обработается быстрее.",
        "dep_sent": "📸 Скриншот получен! Админ проверит перевод и одобрит пополнение ⏳",
        "dep_rejected": "❌ Твоя заявка на пополнение отклонена. Если считаешь, что это ошибка — напиши в 🆘 Поддержку.",
        "wd_need_verify": "🚫 Вывод средств доступен только верифицированным пользователям.\n\nПройди верификацию: кнопка «📝 Верификация».",
        "wd_text": "💸 Вывод средств\n\nМинимальная сумма вывода: <b>{min}</b>\n\nНапиши номер своей карты одним сообщением:",
        "wd_card_bad": "Похоже, это не номер карты. Напиши номер одним сообщением:",
        "wd_amount_prompt": "Карта: {card}\nТеперь сумма вывода (только число):\n⚠️ Минимум: {min}",
        "wd_amount_bad": "Нужно число, например: 20000:",
        "wd_min": "Минимальная сумма вывода — {min}. Напиши сумму снова:",
        "wd_no_money": "На балансе только {v}. Напиши сумму снова:",
        "wd_sent": "✅ Заявка на вывод {amount} на карту {card} отправлена.\n⏳ Админ рассмотрит заявку — после одобрения деньги придут на карту.\n💰 Баланс: {bal}\n⏳ На выводе: {amount}",
        "wd_approved": "✅ Твоя заявка на вывод {v} одобрена! Деньги придут на карту в ближайшее время.",
        "wd_rejected": "❌ Твоя заявка на вывод {v} отклонена. Деньги возвращены на баланс.\n💰 Баланс: {bal}",
        "ver_status": "📝 Верификация\n\nСтатус: ✅ <b>Верифицирован</b>\nФИО: <b>{fio}</b>\n\nВывод средств тебе доступен.",
        "ver_step1": "📝 Верификация\n\nШаг 1 из 2.\n\nНапиши свои фамилию, имя и отчество одним сообщением:\nНапример: <code>Иванов Иван Иванович</code>\n\n⚠️ Пиши настоящие данные — они понадобятся при выводе средств.",
        "ver_fio_bad": "Нужно ровно три слова — Фамилия Имя Отчество. Например: Иванов Иван Иванович. Напиши ещё раз:",
        "ver_math": "Отлично, ФИО записал: <b>{fio}</b>.\n\nШаг 2 из 2. Реши пример:\n\n<b>{example} = ?</b>\n\nНапиши ответ одним числом:",
        "ver_math_bad": "❌ Неверно. Новый пример:\n\n<b>{example} = ?</b>",
        "ver_not_number": "Нужно число — ответ на пример. Напиши ещё раз:",
        "ver_done": "✅ Верификация пройдена!\n\nФИО: <b>{fio}</b>\nСтатус: ✅ <b>Верифицирован</b>\n\nТеперь тебе доступен вывод средств 💸",
        "settings_title": "⚙️ Настройки профиля:",
        "settings_lang": "🌐 Язык: {v}",
        "settings_cur": "💱 Валюта: {v}",
        "settings_changed": "✅ Готово! Настройки обновлены.",
        "sup_prompt": "🆘 Поддержка.\n\nНапиши свой вопрос — админ ответит.\nИли пиши напрямую: {sup}",
        "sup_sent": "Вопрос отправлен! Ожидай ответа ⏳",
        "support_reply": "🆘 Ответ поддержки: {v}",
        "fallback": "Выбери действие кнопкой ниже 👇",
        "banned_access": "⛔ Доступ к площадке ограничен. Напиши в 🆘 Поддержку.",
        "photo_unneeded": "Фото получил, но сейчас оно не нужно 🙂",
        "create_not_admin": "➕ Создание NFT доступно только команде проекта.",
    },
    "en": {
        "btn_cabinet": "👤 My account",
        "btn_market": "🏪 Marketplace",
        "btn_deposit": "💳 Deposit",
        "btn_withdraw": "💸 Withdraw",
        "btn_verify": "📝 Verification",
        "btn_fav": "❤️ Favorites",
        "btn_mynft": "🖼 My NFTs",
        "btn_create": "➕ Create NFT",
        "btn_settings": "⚙️ Settings",
        "btn_transactions": "📜 Transactions",
        "btn_support": "🆘 Support",
        "greet_title": "👋 <b>Hi, {name}!</b>\n\n",
        "greet_body": "Welcome to <b>NFT Market</b> — the home of collectible paintings 💎\n\n"
                      "🏪 <b>Marketplace</b> — browse paintings from cheapest to priciest\n"
                      "👤 <b>My account</b> — balance and your stats\n"
                      "📝 <b>Verification</b> — full name + a quick check, required for withdrawals\n"
                      "💳 <b>Deposit</b> — top up from {min_dep} to get going\n\n"
                      "👇 Tap the buttons below — let's start!",
        "prof_title": "👤 <b>My account</b>\n\n",
        "prof_balance": "💰 Balance: <b>{v}</b>\n",
        "prof_pending": "⏳ Pending withdrawal: <b>{v}</b>\n",
        "prof_turnover": "📈 Total turnover: <b>{v}</b>\n\n",
        "prof_collected": "🖼 Paintings owned: <b>{v}</b>\n",
        "prof_bought": "🛒 Bought: <b>{v}</b>\n",
        "prof_sold": "📤 Sold: <b>{v}</b>\n\n",
        "prof_verified": "Verification: ✅ <b>Verified</b>\n",
        "prof_not_verified": "Verification: ⚠️ <b>Not verified</b>\n",
        "prof_fio": "Full name: <b>{v}</b>\n",
        "prof_nick": "Nickname: <b>@{v}</b>\n",
        "prof_id": "Your ID: <code>{v}</code>\n\n",
        "prof_time": "Date and time: {v}",
        "mkt_empty": "📭 The marketplace is empty for now. Check back later!",
        "cap_year": "📅 Year: {v}",
        "cap_price": "💰 Price: {v}",
        "cap_price_market": "💰 Catalog price: {v}",
        "cap_unique": "✨ Unique edition — 1 of 1",
        "cap_page": "📄 Painting {p} of {n}",
        "btn_buy": "🛒 Buy for {v}",
        "btn_buy_market": "🛒 Buy on marketplace for {v}",
        "btn_unlist": "❌ Remove from sale",
        "btn_fav_add": "❤️ Add to favorites",
        "btn_sell": "🏪 List on marketplace",
        "mynft_empty": "🖼 You have no NFTs yet.\nBuy your first one on the 🏪 Marketplace!",
        "mynft_title": "🖼 Your NFTs",
        "fav_empty": "❤️ Favorites are empty.\nFind a painting on the 🏪 Marketplace and tap “❤️ Add to favorites”.",
        "fav_title": "❤️ Your favorites:",
        "fav_no_lot": "No listing right now 😔",
        "tr_empty": "📜 No transactions yet.",
        "tr_title": "📜 Recent transactions:",
        "dep_text": "➕ Balance top-up:\n\n"
                    "1. Open your banking app\n"
                    "2. Choose “Transfer by card number”\n"
                    "3. Transfer from {min} to {max} to the card:\n"
                    "<code>{card}</code>\n"
                    "4. Take a screenshot of the transfer (amount visible) and send it here\n\n"
                    "⚠️ Amounts below {min} or above {max} are not accepted!\n\n"
                    "⏳ After the screenshot the admin will check the transfer — balance will update ✅",
        "dep_photo_hint": "📸 Send the transfer screenshot as a photo — it gets processed faster.",
        "dep_sent": "📸 Screenshot received! The admin will check the transfer and approve your top-up ⏳",
        "dep_rejected": "❌ Your top-up request was rejected. If you think this is a mistake — contact 🆘 Support.",
        "wd_need_verify": "🚫 Withdrawals are available to verified users only.\n\nPass verification: the “📝 Verification” button.",
        "wd_text": "💸 Withdraw funds\n\nMinimum withdrawal: <b>{min}</b>\n\nSend your card number in one message:",
        "wd_card_bad": "That doesn't look like a card number. Send the number in one message:",
        "wd_amount_prompt": "Card: {card}\nNow the withdrawal amount (a number only):\n⚠️ Minimum: {min}",
        "wd_amount_bad": "A number is required, e.g.: 20000:",
        "wd_min": "Minimum withdrawal is {min}. Send the amount again:",
        "wd_no_money": "Your balance is only {v}. Send the amount again:",
        "wd_sent": "✅ Withdrawal request for {amount} to card {card} has been submitted.\n⏳ The admin will review it — after approval the money goes to your card.\n💰 Balance: {bal}\n⏳ Pending: {amount}",
        "wd_approved": "✅ Your withdrawal of {v} has been approved! The money will arrive on your card soon.",
        "wd_rejected": "❌ Your withdrawal of {v} was rejected. The money is back on your balance.\n💰 Balance: {bal}",
        "ver_status": "📝 Verification\n\nStatus: ✅ <b>Verified</b>\nFull name: <b>{fio}</b>\n\nWithdrawals are available to you.",
        "ver_step1": "📝 Verification\n\nStep 1 of 2.\n\nSend your last name, first name and middle name in one message:\nExample: <code>John Michael Smith</code>\n\n⚠️ Use your real name — it will be needed for withdrawals.",
        "ver_fio_bad": "Exactly three words are required — last name, first name, middle name. Example: John Michael Smith. Try again:",
        "ver_math": "Great, name saved: <b>{fio}</b>.\n\nStep 2 of 2. Solve this:\n\n<b>{example} = ?</b>\n\nSend the answer as a single number:",
        "ver_math_bad": "❌ Wrong. New problem:\n\n<b>{example} = ?</b>",
        "ver_not_number": "A number is required — the answer to the problem. Try again:",
        "ver_done": "✅ Verification complete!\n\nFull name: <b>{fio}</b>\nStatus: ✅ <b>Verified</b>\n\nWithdrawals are now available 💸",
        "settings_title": "⚙️ Profile settings:",
        "settings_lang": "🌐 Language: {v}",
        "settings_cur": "💱 Currency: {v}",
        "settings_changed": "✅ Done! Settings updated.",
        "sup_prompt": "🆘 Support.\n\nSend your question — the admin will reply.\nOr write directly: {sup}",
        "sup_sent": "Question sent! Wait for a reply ⏳",
        "support_reply": "🆘 Support reply: {v}",
        "fallback": "Pick an action with the button below 👇",
        "banned_access": "⛔ Marketplace access is restricted. Contact 🆘 Support.",
        "photo_unneeded": "Photo received, but it's not needed right now 🙂",
        "create_not_admin": "➕ Creating NFTs is available to the project team only.",
    },
}

def t(uid, key, **kw):
    lang = get_lang(uid)
    s = TXT.get(lang, {}).get(key) or TXT["ru"][key]
    return s.format(**kw) if kw else s

def is_btn(message, key):
    return message.text == t(message.chat.id, key)

# ---------- Баны ----------
def is_banned(user_id):
    row = cursor.execute("SELECT until FROM bans WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return False
    until = row[0]
    if until > 0 and until < time.time():
        cursor.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
        db.commit()
        return False
    return True

def ban_message(user_id):
    row = cursor.execute("SELECT until, reason FROM bans WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return ""
    until, reason = row
    if until > 0:
        left = until - time.time()
        hours = int(left // 3600)
        minutes = int((left % 3600) // 60)
        return (f"⛔ Вы забанены на торговой площадке.\nПричина: {reason}\n"
                f"Осталось: {hours} ч {minutes} мин\n\n"
                f"🆘 Написать в поддержку вы можете — кнопка «{t(user_id, 'btn_support')}».")
    return (f"⛔ Вы забанены на торговой площадке навсегда.\nПричина: {reason}\n\n"
            f"🆘 Написать в поддержку вы можете — кнопка «{t(user_id, 'btn_support')}».")

# ---------- Пользователи ----------
def register(user_id, username=None):
    cursor.execute("INSERT OR IGNORE INTO users (id, balance) VALUES (?, 0)", (user_id,))
    if username:
        cursor.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
    db.commit()

def get_balance(user_id):
    row = cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row else 0

def change_balance(user_id, delta):
    register(user_id)
    cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (delta, user_id))
    db.commit()

def get_username(user_id):
    row = cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row and row[0] else None

def is_verified(user_id):
    row = cursor.execute("SELECT verified FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row[0])

def get_full_name(user_id):
    row = cursor.execute("SELECT full_name FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row and row[0] else None

def find_user_by_input(text):
    text = text.strip().lstrip("@")
    if text.isdigit():
        uid = int(text)
        return uid if cursor.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone() else None
    row = cursor.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (text,)).fetchone()
    return row[0] if row else None

def get_turnover(user_id):
    row = cursor.execute("SELECT SUM(price) FROM purchases WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row and row[0] else 0

def get_pending_withdraw(user_id):
    row = cursor.execute(
        "SELECT SUM(amount) FROM withdrawals WHERE user_id = ? AND status = 'pending'",
        (user_id,)
    ).fetchone()
    return row[0] if row and row[0] else 0

def get_owned_ids(user_id):
    """Картины, владельцем которых пользователь является СЕЙЧАС.
    Последняя запись покупки определяет владельца: если картину перепродали — она уходит новому владельцу."""
    rows = cursor.execute("SELECT nft_id, user_id FROM purchases ORDER BY rowid ASC").fetchall()
    owner = {}
    for nid, uid in rows:
        owner[nid] = uid
    return [nid for nid, uid in owner.items() if uid == user_id]

def get_bought_count(user_id):
    return cursor.execute("SELECT COUNT(*) FROM purchases WHERE user_id = ?", (user_id,)).fetchone()[0]

def get_sold_count(user_id):
    return cursor.execute("SELECT COUNT(*) FROM purchases WHERE seller_id = ?", (user_id,)).fetchone()[0]

# ---------- Названия картин ----------
_ADJ = ["Neon", "Golden", "Silent", "Cosmic", "Velvet", "Liquid", "Crystal",
        "Eternal", "Mystic", "Solar", "Lunar", "Frozen", "Burning", "Electric",
        "Dreamy", "Royal", "Shadow", "Radiant", "Wild", "Sacred"]
_NOUN = ["Dream", "Wave", "Vision", "Mirror", "Garden", "Pulse", "Horizon",
         "Echo", "Prism", "Phoenix", "Orbit", "Bloom", "Storm", "Crown",
         "River", "Flame", "Song", "Portal", "Lotus", "Comet",
         "Temple", "Falcon", "Diamond", "Spark", "Sunset"]
_EMOJI = ["💠", "🌌", "🔥", "✨", "🌊", "🌠", "🌈", "💫", "🦅", "🕯",
          "🔮", "🪐", "⚡", "🍃", "🌊", "🌌", "💫", "🌠", "💠", "🔥"]

_name_random = random.Random(777)
_name_pool = [f"{_EMOJI[i % len(_EMOJI)]} {a} {n}"
              for i, (a, n) in enumerate((a, n) for a in _ADJ for n in _NOUN)]
_name_random.shuffle(_name_pool)

def painting_name(idx):
    return _name_pool[(idx - 1) % len(_name_pool)]

# ---------- Загрузка картин: ВСЕ файлы из pictures, номера по порядку ----------
def _natural_key(path):
    m = re.search(r"(\d+)", os.path.basename(path))
    return (int(m.group(1)) if m else 0, path)

_picture_paths = sorted(
    glob.glob(os.path.join("pictures", "*.jpg"))
    + glob.glob(os.path.join("pictures", "*.jpeg"))
    + glob.glob(os.path.join("pictures", "*.png")),
    key=_natural_key,
)

_picture_numbers = list(range(1, len(_picture_paths) + 1))

# ---------- Лестница цен: 15 000 → 150 000, шаги в разноброс ----------
def build_price_ladder(count):
    rnd = random.Random(4242)  # сид: цены одинаковы при каждом запуске
    if count <= 1:
        return [MIN_PRICE]
    if count == 2:
        return [MIN_PRICE, MAX_PRICE]
    out, p = [], MIN_PRICE
    for i in range(count - 1):
        out.append(p)
        remaining_steps = count - 1 - i
        remaining = MAX_PRICE - p
        step = int(remaining / remaining_steps)
        jitter = rnd.choice([-0.3, -0.15, 0, 0.15, 0.3, 0.5])
        step = int(step * (1 + jitter))
        step = max(1000, min(step, remaining - (remaining_steps - 1) * 1000))
        p += step
    out.append(MAX_PRICE)
    return out

_ladder = build_price_ladder(len(_picture_paths))

def nft_price(i):
    if i <= len(_ladder):
        return _ladder[i - 1]
    return MAX_PRICE

_year_random = random.Random(2024)

NFT = {}
for _path, _i in zip(_picture_paths, _picture_numbers):
    NFT[_i] = {
        "name": painting_name(_i),
        "price": nft_price(_i),
        "year": _year_random.randint(2023, 2026),
        "file": _path,
    }

print(f"Загружено картин: {len(NFT)} из {len(_picture_paths)} файлов в папке pictures")
print(f"Цены: от {fmt_rub(MIN_PRICE)} до {fmt_rub(MAX_PRICE)} ₽")

user_states = {}
page_msgs = {}

# ---------- Фон профиля ----------
def make_background():
    if os.path.exists(BG_FILE):
        return
    try:
        from PIL import Image, ImageDraw
        w, h = 1080, 1000
        img = Image.new("RGB", (w, h), (10, 8, 16))
        d = ImageDraw.Draw(img)
        top, bottom = (88, 40, 200), (28, 16, 60)
        cx0, cy0, cx1, cy1 = 60, 60, w - 60, h - 60
        for y in range(cy0, cy1):
            tt = (y - cy0) / (cy1 - cy0)
            r = int(top[0] + (bottom[0] - top[0]) * tt)
            g = int(top[1] + (bottom[1] - top[1]) * tt)
            b = int(top[2] + (bottom[2] - top[2]) * tt)
            d.line([(cx0, y), (cx1, y)], fill=(r, g, b))
        for i in range(3):
            off = 40 + i * 30
            d.polygon([(w//2 - 260 - off, 120), (w//2, 20), (w//2 + 260 + off, 120), (w//2, 340)], outline=(150, 100, 255), width=3)
        d.rounded_rectangle([w//2 - 190, 90, w//2 + 190, 320], radius=28, fill=(60, 25, 130), outline=(160, 110, 255), width=4)
        d.ellipse([w//2 - 70, 130, w//2 + 30, 230], outline=(190, 150, 255), width=5)
        d.rounded_rectangle([w//2 + 60, 140, w//2 + 160, 165], radius=8, outline=(190, 150, 255), width=4)
        d.rounded_rectangle([w//2 + 60, 185, w//2 + 140, 205], radius=8, outline=(190, 150, 255), width=4)
        d.rounded_rectangle([w//2 - 150, 260, w//2 + 160, 290], radius=8, outline=(190, 150, 255), width=4)
        img.save(BG_FILE)
    except Exception:
        pass

make_background()

def safe_send_photo(chat_id, file_path, caption, reply_markup=None):
    if os.path.exists(file_path):
        with open(file_path, "rb") as photo:
            return bot.send_photo(chat_id, photo, caption=caption, reply_markup=reply_markup)
    return bot.send_message(chat_id, f"(картинка не найдена: {file_path})\n\n{caption}", reply_markup=reply_markup)

def show_photo_page(chat_id, kind, file_path, caption, markup):
    old = page_msgs.get((chat_id, kind))
    if old and os.path.exists(file_path):
        try:
            with open(file_path, "rb") as photo:
                bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=old,
                    media=types.InputMediaPhoto(photo, caption=caption),
                    reply_markup=markup,
                )
            return
        except Exception:
            pass
    msg = safe_send_photo(chat_id, file_path, caption, markup)
    if msg and msg.message_id:
        page_msgs[(chat_id, kind)] = msg.message_id

def nav_markup(page, total_pages, prefix):
    nav = types.InlineKeyboardMarkup()
    row = []
    if page > 0:
        row.append(types.InlineKeyboardButton("◀", callback_data=f"{prefix}{page - 1}"))
    row.append(types.InlineKeyboardButton("✦", callback_data="noop"))
    if page < total_pages - 1:
        row.append(types.InlineKeyboardButton("▶", callback_data=f"{prefix}{page + 1}"))
    nav.row(*row)
    return nav

# ---------- Площадка: все картины официальными лотами ----------
def seed_marketplace():
    owner = {}
    for nid, uid in cursor.execute("SELECT nft_id, user_id FROM purchases ORDER BY rowid ASC").fetchall():
        owner[nid] = uid
    sold_ids = {nid for nid, uid in owner.items() if uid != ADMIN_ID}
    added, updated = 0, 0
    for nid, item in NFT.items():
        row = cursor.execute("SELECT id, seller_id, price FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
        if row:
            listing_id, seller_id, price = row
            if seller_id == ADMIN_ID:
                if price != item["price"]:
                    cursor.execute("UPDATE marketplace SET price = ? WHERE id = ?", (item["price"], listing_id))
                    updated += 1
            else:
                if price < MIN_PRICE:
                    cursor.execute("UPDATE marketplace SET price = ? WHERE id = ?", (MIN_PRICE, listing_id))
                    updated += 1
                elif price > MAX_SELL_PRICE:
                    cursor.execute("UPDATE marketplace SET price = ? WHERE id = ?", (MAX_SELL_PRICE, listing_id))
                    updated += 1
            continue
        if nid in sold_ids:
            continue
        cursor.execute(
            "INSERT INTO marketplace (nft_id, seller_id, price) VALUES (?, ?, ?)",
            (nid, ADMIN_ID, item["price"])
        )
        added += 1
    db.commit()
    print(f"Площадка: выставлено {added}, синхронизировано {updated} картин")

seed_marketplace()

# ---------- Клавиатуры ----------
def profile_keyboard(chat_id, admin_view=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if admin_view:
        markup.row(types.KeyboardButton("➕ Пополнить пользователю"), types.KeyboardButton(t(chat_id, "btn_deposit")), types.KeyboardButton(t(chat_id, "btn_withdraw")))
    else:
        markup.row(types.KeyboardButton(t(chat_id, "btn_deposit")), types.KeyboardButton(t(chat_id, "btn_withdraw")))
    markup.row(types.KeyboardButton(t(chat_id, "btn_cabinet")), types.KeyboardButton(t(chat_id, "btn_verify")))
    markup.row(types.KeyboardButton(t(chat_id, "btn_fav")), types.KeyboardButton(t(chat_id, "btn_mynft")))
    markup.row(types.KeyboardButton(t(chat_id, "btn_create")), types.KeyboardButton(t(chat_id, "btn_settings")))
    markup.row(types.KeyboardButton(t(chat_id, "btn_transactions")), types.KeyboardButton(t(chat_id, "btn_market")))
    markup.add(types.KeyboardButton(t(chat_id, "btn_support")))
    return markup

# ---------- Личный кабинет ----------
def send_profile(chat_id, greet=False, name=None):
    register(chat_id)
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    if greet and name:
        text = (
            t(chat_id, "greet_title", name=name)
            + t(chat_id, "greet_body", min_dep=fmt_money(MIN_DEPOSIT, chat_id))
        )
    else:
        nick = get_username(chat_id)
        nick_line = t(chat_id, "prof_nick", v=nick) if nick else ""
        owned = len(get_owned_ids(chat_id))
        bought = get_bought_count(chat_id)
        sold = get_sold_count(chat_id)
        if is_verified(chat_id):
            verify_line = t(chat_id, "prof_verified") + t(chat_id, "prof_fio", v=get_full_name(chat_id))
        else:
            verify_line = t(chat_id, "prof_not_verified")
        text = (
            t(chat_id, "prof_title")
            + t(chat_id, "prof_balance", v=fmt_money(get_balance(chat_id), chat_id))
            + t(chat_id, "prof_pending", v=fmt_money(get_pending_withdraw(chat_id), chat_id))
            + t(chat_id, "prof_turnover", v=fmt_money(get_turnover(chat_id), chat_id))
            + t(chat_id, "prof_collected", v=owned)
            + t(chat_id, "prof_bought", v=bought)
            + t(chat_id, "prof_sold", v=sold)
            + verify_line
            + nick_line
            + t(chat_id, "prof_id", v=chat_id)
            + t(chat_id, "prof_time", v=now)
        )
    kb = profile_keyboard(chat_id, admin_view=(chat_id == ADMIN_ID))
    if os.path.exists(BG_FILE):
        with open(BG_FILE, "rb") as photo:
            bot.send_photo(chat_id, photo, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

@bot.message_handler(func=lambda m: is_btn(m, "btn_cabinet"))
def menu_cabinet(message):
    send_profile(message.chat.id)

# ---------- Приветствие ----------
@bot.message_handler(commands=['start'])
def start(message):
    register(message.chat.id, message.from_user.username)
    user_states[message.chat.id] = None
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    name = message.from_user.first_name or ("друг" if get_lang(message.chat.id) == "ru" else "friend")
    send_profile(message.chat.id, greet=True, name=name)

# ---------- Кнопки профиля ----------
@bot.message_handler(func=lambda m: m.text == "➕ Пополнить пользователю" and m.chat.id == ADMIN_ID)
def menu_give(message):
    user_states[message.chat.id] = "give_id"
    bot.send_message(
        message.chat.id,
        "➕ Пополнение баланса пользователю.\n\n"
        "Напиши ник пользователя через @ (например: @ivan_petrov)\n"
        "или его числовой ID — оба варианта работают.\n\n"
        "⚠️ Ник сработает, только если человек хотя бы раз нажал /start в боте."
    )

@bot.message_handler(func=lambda m: is_btn(m, "btn_deposit"))
def menu_deposit(message):
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    user_states[message.chat.id] = "deposit_screenshot"
    bot.send_message(
        message.chat.id,
        t(message.chat.id, "dep_text", min=fmt_money(MIN_DEPOSIT, message.chat.id),
          max=fmt_money(MAX_DEPOSIT, message.chat.id), card=PAY_CARD),
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: is_btn(m, "btn_withdraw"))
def menu_withdraw(message):
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    if not is_verified(message.chat.id):
        bot.send_message(message.chat.id, t(message.chat.id, "wd_need_verify"))
        return
    user_states[message.chat.id] = "withdraw_card"
    bot.send_message(message.chat.id, t(message.chat.id, "wd_text", min=fmt_money(MIN_WITHDRAW, message.chat.id)), parse_mode="HTML")

@bot.message_handler(func=lambda m: is_btn(m, "btn_verify"))
def menu_verify(message):
    if is_verified(message.chat.id):
        bot.send_message(message.chat.id, t(message.chat.id, "ver_status", fio=get_full_name(message.chat.id)), parse_mode="HTML")
        return
    user_states[message.chat.id] = "verify_fio"
    bot.send_message(message.chat.id, t(message.chat.id, "ver_step1"), parse_mode="HTML")

@bot.message_handler(func=lambda m: is_btn(m, "btn_fav"))
def menu_favorites(message):
    rows = cursor.execute("SELECT nft_id FROM favorites WHERE user_id = ?", (message.chat.id,)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, t(message.chat.id, "fav_empty"))
        return
    markup = types.InlineKeyboardMarkup()
    for (nid,) in rows:
        if nid in NFT:
            markup.add(types.InlineKeyboardButton(NFT[nid]["name"], callback_data=f"favshow_{nid}"))
    bot.send_message(message.chat.id, t(message.chat.id, "fav_title"), reply_markup=markup)

@bot.message_handler(func=lambda m: is_btn(m, "btn_mynft"))
def menu_my_nft(message):
    send_my_nft_page(message.chat.id, 0)

@bot.message_handler(func=lambda m: is_btn(m, "btn_create"))
def menu_create_nft(message):
    if message.chat.id == ADMIN_ID:
        user_states[message.chat.id] = "create_nft_photo"
        bot.send_message(message.chat.id, f"➕ Создание NFT.\n\nОтправь фото картины:\n(цена от {fmt_money(MIN_PRICE, message.chat.id)} до {fmt_money(MAX_PRICE, message.chat.id)})")
    else:
        bot.send_message(message.chat.id, t(message.chat.id, "create_not_admin"))

@bot.message_handler(func=lambda m: is_btn(m, "btn_settings"))
def menu_settings(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(message.chat.id, "settings_lang", v=LANGS[get_lang(message.chat.id)]), callback_data="noop"))
    markup.add(types.InlineKeyboardButton("🇷🇺 Русский", callback_data="setlang_ru"),
               types.InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"))
    markup.add(types.InlineKeyboardButton(t(message.chat.id, "settings_cur", v=get_currency(message.chat.id)), callback_data="noop"))
    markup.row(
        types.InlineKeyboardButton("₽ RUB", callback_data="setcur_RUB"),
        types.InlineKeyboardButton("$ USD", callback_data="setcur_USD"),
    )
    markup.row(
        types.InlineKeyboardButton("USDT", callback_data="setcur_USDT"),
        types.InlineKeyboardButton("TON", callback_data="setcur_TON"),
    )
    markup.row(
        types.InlineKeyboardButton("BTC", callback_data="setcur_BTC"),
        types.InlineKeyboardButton("ETH", callback_data="setcur_ETH"),
    )
    bot.send_message(message.chat.id, t(message.chat.id, "settings_title"), reply_markup=markup)

@bot.message_handler(func=lambda m: is_btn(m, "btn_transactions"))
def menu_transactions(message):
    rows = cursor.execute("SELECT nft_name, price FROM purchases WHERE user_id = ? ORDER BY rowid DESC LIMIT 10", (message.chat.id,)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, t(message.chat.id, "tr_empty"))
        return
    lines = [f"• {name} — {fmt_money(price, message.chat.id)}" for name, price in rows]
    bot.send_message(message.chat.id, t(message.chat.id, "tr_title") + "\n\n" + "\n".join(lines))

@bot.message_handler(func=lambda m: is_btn(m, "btn_market"))
def menu_market(message):
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    send_market_page(message.chat.id, 0)

@bot.message_handler(func=lambda m: is_btn(m, "btn_support"))
def menu_support(message):
    user_states[message.chat.id] = "support"
    bot.send_message(message.chat.id, t(message.chat.id, "sup_prompt", sup=SUPPORT_USERNAME))

# ---------- Мои NFT: витрина купленных картин со стрелочками ----------
def send_my_nft_page(chat_id, page):
    owned = get_owned_ids(chat_id)
    if not owned:
        bot.send_message(chat_id, t(chat_id, "mynft_empty"))
        return
    total_pages = len(owned)
    page = max(0, min(page, total_pages - 1))
    nid = owned[page]
    item = NFT[nid]
    markup = types.InlineKeyboardMarkup()
    if not is_banned(chat_id):
        markup.add(types.InlineKeyboardButton(t(chat_id, "btn_sell"), callback_data=f"sellpick_{nid}"))
    markup.add(types.InlineKeyboardButton(t(chat_id, "btn_fav_add"), callback_data=f"fav_{nid}"))
    for row in nav_markup(page, total_pages, "mynft_").keyboard:
        markup.row(*row)
    caption = (
        f"🖼 {item['name']}\n\n"
        f"{t(chat_id, 'cap_year', v=item['year'])}\n"
        f"{t(chat_id, 'cap_price_market', v=fmt_money(item['price'], chat_id))}\n"
        f"{t(chat_id, 'cap_unique')}\n"
        f"{t(chat_id, 'cap_page', p=page + 1, n=total_pages)}"
    )
    show_photo_page(chat_id, "mynft", item["file"], caption, markup)

# ---------- Фото ----------
@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    register(message.chat.id, message.from_user.username)
    state = user_states.get(message.chat.id)
    if state == "create_nft_photo" and message.chat.id == ADMIN_ID:
        user_states[message.chat.id] = ("create_nft_info", message.photo[-1].file_id)
        bot.send_message(message.chat.id, f"Фото получено. Теперь напиши название и цену через пробел, например: 🌟 Star 25000\n(цена от {fmt_rub(MIN_PRICE)} до {fmt_rub(MAX_PRICE)} ₽; картина сразу встанет на площадку твоим лотом)")
    elif state == "deposit_screenshot":
        if is_banned(message.chat.id):
            user_states[message.chat.id] = None
            bot.send_message(message.chat.id, ban_message(message.chat.id))
            return
        cursor.execute("INSERT INTO deposits (user_id, status) VALUES (?, 'pending')", (message.chat.id,))
        dep_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        user_states[message.chat.id] = None
        bot.send_message(message.chat.id, t(message.chat.id, "dep_sent"))
        bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"ok_{dep_id}_{message.chat.id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{dep_id}_{message.chat.id}"),
        )
        nick = message.from_user.username
        nick_line = f" (@{nick})" if nick else ""
        bot.send_message(ADMIN_ID, f"💳 Заявка на пополнение №{dep_id} от пользователя {message.chat.id}{nick_line}\nПроверь перевод на карте, затем выбери:", reply_markup=kb)
    else:
        bot.send_message(message.chat.id, t(message.chat.id, "photo_unneeded"))

# ---------- Команды админа ----------
@bot.message_handler(commands=['add'])
def admin_add(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        _, target, amount = message.text.split()
        uid = find_user_by_input(target)
        if uid is None:
            bot.send_message(ADMIN_ID, f"Не нашёл пользователя {target}. Ник сработает, если человек нажимал /start в боте.")
            return
        change_balance(uid, int(amount))
        bot.send_message(ADMIN_ID, f"Баланс {uid} пополнен на {amount} ₽")
        bot.send_message(uid, f"💳 На вашем балансе пополнение: +{fmt_money(int(amount), uid)}\n💰 Баланс: {fmt_money(get_balance(uid), uid)}")
    except Exception:
        bot.send_message(ADMIN_ID, "Формат: /add ник_или_id сумма")

@bot.message_handler(commands=['minus'])
def admin_minus(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        _, target, amount = message.text.split()
        uid = find_user_by_input(target)
        if uid is None:
            bot.send_message(ADMIN_ID, f"Не нашёл пользователя {target}.")
            return
        change_balance(uid, -int(amount))
        bot.send_message(ADMIN_ID, f"Списано {amount} ₽ у {uid}")
        bot.send_message(uid, f"➖ Списание {fmt_money(int(amount), uid)}.\n💰 Баланс: {fmt_money(get_balance(uid), uid)}")
    except Exception:
        bot.send_message(ADMIN_ID, "Формат: /minus ник_или_id сумма")

@bot.message_handler(commands=['reply'])
def admin_reply(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        _, target, *parts = message.text.split()
        uid = find_user_by_input(target)
        if uid is None:
            bot.send_message(ADMIN_ID, f"Не нашёл пользователя {target}.")
            return
        bot.send_message(uid, t(uid, "support_reply", v=' '.join(parts)))
        bot.send_message(ADMIN_ID, "Ответ отправлен")
    except Exception:
        bot.send_message(ADMIN_ID, "Формат: /reply ник_или_id текст")

@bot.message_handler(commands=['clearlots'])
def admin_clearlots(message):
    if message.chat.id != ADMIN_ID:
        return
    cursor.execute("DELETE FROM marketplace")
    db.commit()
    bot.send_message(ADMIN_ID, "🗑 Все лоты на площадке удалены")

@bot.message_handler(commands=['lot'])
def admin_lot(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        _, nid, price = message.text.split()
        nid, price = int(nid), int(price)
    except Exception:
        bot.send_message(ADMIN_ID, "Формат: /lot номер_файла цена, например: /lot 5 25000")
        return
    if nid not in NFT:
        bot.send_message(ADMIN_ID, "Картины с этим номером нет. Проверь папку pictures.")
        return
    if price < MIN_PRICE or price > MAX_PRICE:
        bot.send_message(ADMIN_ID, f"⚠️ Цена официального лота должна быть от {fmt_rub(MIN_PRICE)} до {fmt_rub(MAX_PRICE)} ₽.")
        return
    already = cursor.execute("SELECT id FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
    if already:
        cursor.execute("UPDATE marketplace SET price = ?, seller_id = ? WHERE nft_id = ?", (price, ADMIN_ID, nid))
        bot.send_message(ADMIN_ID, f"♻️ Лот обновлён: {NFT[nid]['name']} — {fmt_rub(price)} ₽")
    else:
        cursor.execute("INSERT INTO marketplace (nft_id, seller_id, price) VALUES (?, ?, ?)", (nid, ADMIN_ID, price))
        bot.send_message(ADMIN_ID, f"✅ Лот создан: {NFT[nid]['name']} — {fmt_rub(price)} ₽")
    db.commit()

@bot.message_handler(commands=['ban'])
def admin_ban(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        _, target, hours, *reason_parts = message.text.split()
        hours = float(hours)
        reason = ' '.join(reason_parts) if reason_parts else "Нарушение правил"
    except Exception:
        bot.send_message(ADMIN_ID, "Формат: /ban ник_или_id часы причина (часы 0 = навсегда)")
        return
    uid = find_user_by_input(target)
    if uid is None:
        bot.send_message(ADMIN_ID, f"Не нашёл пользователя {target}. Ник сработает, если человек нажимал /start в боте.")
        return
    if uid == ADMIN_ID:
        bot.send_message(ADMIN_ID, "Себя забанить нельзя 🙂")
        return
    until = time.time() + hours * 3600 if hours > 0 else 0
    cursor.execute("INSERT OR REPLACE INTO bans (user_id, until, reason) VALUES (?, ?, ?)", (uid, until, reason))
    db.commit()
    try:
        if until > 0:
            bot.send_message(uid, f"⛔ Вы забанены на торговой площадке на {hours:g} ч.\nПричина: {reason}\n\n🆘 В поддержку написать можно.")
        else:
            bot.send_message(uid, f"⛔ Вы забанены на торговой площадке навсегда.\nПричина: {reason}\n\n🆘 В поддержку написать можно.")
    except Exception:
        pass
    term = "навсегда" if until == 0 else f"{hours:g} ч"
    bot.send_message(ADMIN_ID, f"✅ Пользователь {uid} забанен ({term}). Причина: {reason}")

@bot.message_handler(commands=['unban'])
def admin_unban(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        target = message.text.split()[1]
    except Exception:
        bot.send_message(ADMIN_ID, "Формат: /unban ник_или_id")
        return
    uid = find_user_by_input(target)
    if uid is None:
        bot.send_message(ADMIN_ID, f"Не нашёл пользователя {target}.")
        return
    cursor.execute("DELETE FROM bans WHERE user_id = ?", (uid,))
    db.commit()
    bot.send_message(ADMIN_ID, f"✅ Бан снят с {uid}")
    try:
        bot.send_message(uid, "✅ Бан снят! Торговая площадка снова доступна.")
    except Exception:
        pass

@bot.message_handler(commands=['banlist'])
def admin_banlist(message):
    if message.chat.id != ADMIN_ID:
        return
    rows = cursor.execute("SELECT user_id, until, reason FROM bans").fetchall()
    if not rows:
        bot.send_message(ADMIN_ID, "Банов нет")
        return
    lines, now = [], time.time()
    for uid, until, reason in rows:
        if until > 0 and until <= now:
            continue
        if until == 0:
            lines.append(f"{uid} — навсегда — {reason}")
        else:
            left = until - now
            lines.append(f"{uid} — {int(left // 3600)} ч {int((left % 3600) // 60)} мин — {reason}")
    bot.send_message(ADMIN_ID, "📋 Активные баны:\n" + ("\n".join(lines) if lines else "Банов нет"))

# ---------- Текстовые сообщения ----------
@bot.message_handler(content_types=['text'])
def text_handler(message):
    chat_id = message.chat.id
    register(chat_id, message.from_user.username)
    state = user_states.get(chat_id)

    if state == "deposit_screenshot":
        bot.send_message(chat_id, t(chat_id, "dep_photo_hint"))
        return

    if state == "verify_fio":
        parts = message.text.strip().split()
        if len(parts) != 3 or not all(p.isalpha() for p in parts):
            bot.send_message(chat_id, t(chat_id, "ver_fio_bad"))
            return
        fio = " ".join(p.capitalize() for p in parts)
        a, b = random.randint(11, 89), random.randint(11, 89)
        user_states[chat_id] = ("verify_math", fio, a + b, f"{a} + {b}")
        bot.send_message(chat_id, t(chat_id, "ver_math", fio=fio, example=f"{a} + {b}"), parse_mode="HTML")
        return

    if state and state[0] == "verify_math":
        fio, correct, example = state[1], state[2], state[3]
        try:
            answer = int(message.text.strip())
        except ValueError:
            bot.send_message(chat_id, t(chat_id, "ver_not_number"))
            return
        if answer != correct:
            a, b = random.randint(11, 89), random.randint(11, 89)
            user_states[chat_id] = ("verify_math", fio, a + b, f"{a} + {b}")
            bot.send_message(chat_id, t(chat_id, "ver_math_bad", example=f"{a} + {b}"), parse_mode="HTML")
            return
        cursor.execute("UPDATE users SET verified = 1, full_name = ? WHERE id = ?", (fio, chat_id))
        db.commit()
        user_states[chat_id] = None
        bot.send_message(chat_id, t(chat_id, "ver_done", fio=fio), parse_mode="HTML")
        return

    if state == "give_id":
        uid = find_user_by_input(message.text)
        if uid is None:
            bot.send_message(chat_id, "Не нашёл такого пользователя 🤔\nПроверь ник (нужен @ или ник без пробелов) или напиши его числовой ID.")
            return
        register(uid)
        nick = get_username(uid)
        nick_line = f" (@{nick})" if nick else ""
        user_states[chat_id] = ("give_amount", uid)
        bot.send_message(chat_id, f"Пользователь {uid}{nick_line} найден.\n💰 Текущий баланс: {fmt_rub(get_balance(uid))} ₽\n\nТеперь напиши сумму пополнения (только число):")
        return

    if state and state[0] == "give_amount":
        uid = state[1]
        try:
            amount = int(message.text.strip())
        except ValueError:
            bot.send_message(chat_id, "Нужно число, например: 5000")
            return
        if amount <= 0:
            bot.send_message(chat_id, "Сумма должна быть больше нуля. Напиши снова:")
            return
        change_balance(uid, amount)
        user_states[chat_id] = None
        nick = get_username(uid)
        nick_line = f" (@{nick})" if nick else ""
        bot.send_message(chat_id, f"✅ Готово! Пользователю {uid}{nick_line} начислено {fmt_rub(amount)} ₽.\n💰 Его баланс: {fmt_rub(get_balance(uid))} ₽")
        try:
            bot.send_message(uid, f"💳 На вашем балансе пополнение: +{fmt_money(amount, uid)}\n💰 Баланс: {fmt_money(get_balance(uid), uid)}")
        except Exception:
            pass
        return

    if state and state[0] == "approve_amount":
        uid, dep_id = state[1], state[2]
        try:
            amount = int(message.text.strip())
        except ValueError:
            bot.send_message(chat_id, "Нужно число, например: 10000")
            return
        if amount < MIN_DEPOSIT or amount > MAX_DEPOSIT:
            bot.send_message(chat_id, f"Сумма должна быть от {fmt_rub(MIN_DEPOSIT)} до {fmt_rub(MAX_DEPOSIT)} ₽. Напиши снова:")
            return
        change_balance(uid, amount)
        cursor.execute("UPDATE deposits SET status = 'approved' WHERE id = ?", (dep_id,))
        db.commit()
        user_states[chat_id] = None
        bot.send_message(chat_id, f"✅ Заявка №{dep_id} одобрена: +{fmt_rub(amount)} ₽ пользователю {uid}")
        bot.send_message(uid, f"💳 На вашем балансе пополнение: +{fmt_money(amount, uid)}\n💰 Баланс: {fmt_money(get_balance(uid), uid)}")
        return

    if state and state[0] == "create_nft_info":
        try:
            parts = message.text.strip().rsplit(" ", 1)
            name, price = parts[0], int(parts[1])
        except Exception:
            bot.send_message(chat_id, "Формат: Название Цена, например: 🌟 Star 25000")
            return
        if price < MIN_PRICE:
            bot.send_message(chat_id, f"⚠️ Минимальная цена картины — {fmt_rub(MIN_PRICE)} ₽. Укажи цену не ниже:")
            return
        if price > MAX_PRICE:
            bot.send_message(chat_id, f"⚠️ Максимальная цена картины — {fmt_rub(MAX_PRICE)} ₽. Укажи цену не выше:")
            return
        try:
            new_id = max(NFT.keys()) + 1 if NFT else 1
            file_path = f"pictures/art{new_id}.jpg"
            file_info = bot.get_file(state[1])
            downloaded = bot.download_file(file_info.file_path)
            with open(file_path, "wb") as f:
                f.write(downloaded)
            NFT[new_id] = {"name": name, "price": price, "year": _year_random.randint(2023, 2026), "file": file_path}
            cursor.execute("INSERT INTO marketplace (nft_id, seller_id, price) VALUES (?, ?, ?)", (new_id, ADMIN_ID, price))
            db.commit()
            user_states[chat_id] = None
            bot.send_message(chat_id, f"✅ NFT создан и выставлен на площадку: {name} за {fmt_rub(price)} ₽!")
        except Exception:
            bot.send_message(chat_id, "Формат: Название Цена, например: 🌟 Star 25000")
        return

    if state and state[0] == "sell_price":
        if is_banned(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, ban_message(chat_id))
            return
        nid = state[1]
        try:
            price = int(message.text.strip())
        except ValueError:
            bot.send_message(chat_id, "Нужно число, например: 25000:")
            return
        if price < MIN_PRICE:
            bot.send_message(chat_id, f"⚠️ Минимальная цена продажи — {fmt_money(MIN_PRICE, chat_id)}. Укажи цену не ниже:")
            return
        if price > MAX_SELL_PRICE:
            bot.send_message(chat_id, f"⚠️ Максимальная цена продажи — {fmt_money(MAX_SELL_PRICE, chat_id)}. Укажи цену не выше:")
            return
        if nid not in get_owned_ids(chat_id):
            bot.send_message(chat_id, "Эта картина тебе больше не принадлежит.")
            user_states[chat_id] = None
            return
        already = cursor.execute("SELECT id, seller_id FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
        if already:
            if already[1] == chat_id:
                cursor.execute("UPDATE marketplace SET price = ? WHERE nft_id = ?", (price, nid))
                db.commit()
                bot.send_message(chat_id, f"✅ Цена обновлена: {fmt_money(price, chat_id)}")
            else:
                bot.send_message(chat_id, "Эта картина уже выставлена другим человеком.")
            user_states[chat_id] = None
            return
        cursor.execute("INSERT INTO marketplace (nft_id, seller_id, price) VALUES (?, ?, ?)", (nid, chat_id, price))
        db.commit()
        user_states[chat_id] = None
        listing_id = cursor.execute("SELECT id FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()[0]
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(t(chat_id, "btn_unlist"), callback_data=f"unlist_{listing_id}"))
        bot.send_message(chat_id, f"✅ {NFT[nid]['name']} выставлена за {fmt_money(price, chat_id)}. Когда купят — деньги сразу на твой баланс.", reply_markup=kb)
        return

    if state == "withdraw_card":
        if is_banned(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, ban_message(chat_id))
            return
        if not is_verified(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, t(chat_id, "wd_need_verify"))
            return
        card = message.text.strip()
        if len(card) < 12 or not any(ch.isdigit() for ch in card):
            bot.send_message(chat_id, t(chat_id, "wd_card_bad"))
            return
        user_states[chat_id] = ("withdraw_amount", card)
        bot.send_message(chat_id, t(chat_id, "wd_amount_prompt", card=card, min=fmt_money(MIN_WITHDRAW, chat_id)))
        return

    if state and state[0] == "withdraw_amount":
        if is_banned(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, ban_message(chat_id))
            return
        if not is_verified(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, t(chat_id, "wd_need_verify"))
            return
        card = state[1]
        try:
            amount = int(message.text.strip())
        except ValueError:
            bot.send_message(chat_id, t(chat_id, "wd_amount_bad"))
            return
        balance = get_balance(chat_id)
        if amount < MIN_WITHDRAW:
            bot.send_message(chat_id, t(chat_id, "wd_min", min=fmt_money(MIN_WITHDRAW, chat_id)))
            return
        if amount > balance:
            bot.send_message(chat_id, t(chat_id, "wd_no_money", v=fmt_money(balance, chat_id)))
            return
        change_balance(chat_id, -amount)
        cursor.execute(
            "INSERT INTO withdrawals (user_id, card, amount, status) VALUES (?, ?, ?, 'pending')",
            (chat_id, card, amount)
        )
        wd_id = cursor.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()
        user_states[chat_id] = None
        bot.send_message(
            chat_id,
            t(chat_id, "wd_sent", amount=fmt_money(amount, chat_id), card=card,
              bal=fmt_money(get_balance(chat_id), chat_id))
        )
        nick = get_username(chat_id)
        nick_line = f" (@{nick})" if nick else ""
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Одобрить вывод", callback_data=f"wok_{wd_id}_{chat_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"wno_{wd_id}_{chat_id}"),
        )
        bot.send_message(
            ADMIN_ID,
            f"➖ Заявка на вывод №{wd_id}\n"
            f"Пользователь: {chat_id}{nick_line}\n"
            f"ФИО: {get_full_name(chat_id) or '—'}\n"
            f"Карта: {card}\n"
            f"Сумма: {fmt_rub(amount)} ₽\n"
            f"Сумма уже списана с баланса и заморожена до решения.",
            reply_markup=kb
        )
        return

    if state == "support":
        user_states[chat_id] = None
        bot.send_message(chat_id, t(chat_id, "sup_sent"))
        nick = get_username(chat_id)
        nick_line = f" (@{nick})" if nick else ""
        bot.send_message(ADMIN_ID, f"🆘 Вопрос от {chat_id}{nick_line}:\n{message.text}\n\nОтветить: /reply {chat_id} текст")
        return

    bot.send_message(chat_id, t(chat_id, "fallback"), reply_markup=profile_keyboard(chat_id, admin_view=(chat_id == ADMIN_ID)))

# ---------- Площадка: листаешь, цены растут от дешёвых к дорогим ----------
def send_market_page(chat_id, page):
    lots = cursor.execute(
        "SELECT id, nft_id, seller_id, price FROM marketplace ORDER BY price ASC"
    ).fetchall()

    if not lots:
        bot.send_message(chat_id, t(chat_id, "mkt_empty"))
        return

    total_pages = (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    lot_id, nft_id, seller_id, price = lots[page]
    if nft_id not in NFT:
        return
    item = NFT[nft_id]
    markup = types.InlineKeyboardMarkup()
    if seller_id != chat_id:
        markup.add(types.InlineKeyboardButton(t(chat_id, "btn_buy", v=fmt_money(price, chat_id)), callback_data=f"mbuy_{lot_id}"))
    else:
        markup.add(types.InlineKeyboardButton(t(chat_id, "btn_unlist"), callback_data=f"unlist_{lot_id}"))
    markup.add(types.InlineKeyboardButton(t(chat_id, "btn_fav_add"), callback_data=f"fav_{nft_id}"))
    for row in nav_markup(page, total_pages, "mpage_").keyboard:
        markup.row(*row)
    caption = (
        f"🖼 {item['name']}\n\n"
        f"{t(chat_id, 'cap_year', v=item['year'])}\n"
        f"{t(chat_id, 'cap_price', v=fmt_money(price, chat_id))}\n"
        f"{t(chat_id, 'cap_unique')}"
    )
    show_photo_page(chat_id, "market", item["file"], caption, markup)

# ---------- Кнопки ----------
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    chat_id = call.message.chat.id
    data = call.data

    if data.startswith("setlang_"):
        lang = data.split("_")[1]
        if lang in LANGS:
            cursor.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, chat_id))
            db.commit()
            bot.send_message(chat_id, t(chat_id, "settings_changed"), reply_markup=profile_keyboard(chat_id, admin_view=(chat_id == ADMIN_ID)))
        bot.answer_callback_query(call.id)

    elif data.startswith("setcur_"):
        cur = data.split("_")[1]
        if cur in CURR:
            cursor.execute("UPDATE users SET currency = ? WHERE id = ?", (cur, chat_id))
            db.commit()
            bot.send_message(chat_id, t(chat_id, "settings_changed"))
        bot.answer_callback_query(call.id)

    elif data.startswith("ok_"):
        if chat_id != ADMIN_ID:
            bot.answer_callback_query(call.id)
            return
        _, dep_id, uid = data.split("_")
        user_states[ADMIN_ID] = ("approve_amount", int(uid), int(dep_id))
        bot.send_message(ADMIN_ID, f"✍️ Сколько начислить пользователю {uid}?\nНапиши сумму одним числом (от {fmt_rub(MIN_DEPOSIT)} до {fmt_rub(MAX_DEPOSIT)}):")
        bot.answer_callback_query(call.id)

    elif data.startswith("no_"):
        if chat_id != ADMIN_ID:
            bot.answer_callback_query(call.id)
            return
        _, dep_id, uid = data.split("_")
        cursor.execute("UPDATE deposits SET status = 'rejected' WHERE id = ?", (int(dep_id),))
        db.commit()
        bot.send_message(ADMIN_ID, f"❌ Заявка №{dep_id} отклонена.")
        bot.send_message(int(uid), t(int(uid), "dep_rejected"))
        bot.answer_callback_query(call.id)

    elif data.startswith("wok_"):
        if chat_id != ADMIN_ID:
            bot.answer_callback_query(call.id)
            return
        _, wd_id, uid = data.split("_")
        wd_id, uid = int(wd_id), int(uid)
        row = cursor.execute("SELECT amount, status FROM withdrawals WHERE id = ?", (wd_id,)).fetchone()
        if not row:
            bot.answer_callback_query(call.id, "Заявка не найдена.")
            return
        amount, status = row
        if status != "pending":
            bot.answer_callback_query(call.id, "Эта заявка уже обработана.")
            return
        cursor.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (wd_id,))
        db.commit()
        bot.send_message(ADMIN_ID, f"✅ Вывод №{wd_id} одобрен. Переведи {fmt_rub(amount)} ₽ на карту пользователя вручную.")
        bot.send_message(uid, t(uid, "wd_approved", v=fmt_money(amount, uid)))
        bot.answer_callback_query(call.id)

    elif data.startswith("wno_"):
        if chat_id != ADMIN_ID:
            bot.answer_callback_query(call.id)
            return
        _, wd_id, uid = data.split("_")
        wd_id, uid = int(wd_id), int(uid)
        row = cursor.execute("SELECT amount, status FROM withdrawals WHERE id = ?", (wd_id,)).fetchone()
        if not row:
            bot.answer_callback_query(call.id, "Заявка не найдена.")
            return
        amount, status = row
        if status != "pending":
            bot.answer_callback_query(call.id, "Эта заявка уже обработана.")
            return
        change_balance(uid, amount)
        cursor.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (wd_id,))
        db.commit()
        bot.send_message(ADMIN_ID, f"❌ Вывод №{wd_id} отклонён. {fmt_rub(amount)} ₽ возвращены на баланс пользователя.")
        bot.send_message(uid, t(uid, "wd_rejected", v=fmt_money(amount, uid), bal=fmt_money(get_balance(uid), uid)))
        bot.answer_callback_query(call.id)

    elif data.startswith("favshow_"):
        nid = int(data.split("_")[1])
        if nid not in NFT:
            bot.answer_callback_query(call.id, "Картина больше не доступна.")
            return
        item = NFT[nid]
        caption = (
            f"{item['name']}\n"
            f"{t(chat_id, 'cap_year', v=item['year'])}\n"
            f"{t(chat_id, 'cap_price_market', v=fmt_money(item['price'], chat_id))}\n"
            f"{t(chat_id, 'cap_unique')}"
        )
        lot = cursor.execute("SELECT id, seller_id, price FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
        markup = types.InlineKeyboardMarkup()
        if lot:
            markup.add(types.InlineKeyboardButton(t(chat_id, "btn_buy_market", v=fmt_money(lot[2], chat_id)), callback_data=f"mbuy_{lot[0]}"))
        else:
            markup.add(types.InlineKeyboardButton(t(chat_id, "fav_no_lot"), callback_data="noop"))
        safe_send_photo(chat_id, item["file"], caption, markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("mynft_"):
        bot.answer_callback_query(call.id)
        send_my_nft_page(chat_id, int(data.split("_")[1]))

    elif data.startswith("mpage_"):
        bot.answer_callback_query(call.id)
        send_market_page(chat_id, int(data.split("_")[1]))

    elif data.startswith("fav_"):
        nid = int(data.split("_")[1])
        row = cursor.execute("SELECT 1 FROM favorites WHERE user_id = ? AND nft_id = ?", (chat_id, nid)).fetchone()
        if row:
            cursor.execute("DELETE FROM favorites WHERE user_id = ? AND nft_id = ?", (chat_id, nid))
            bot.answer_callback_query(call.id, "Убрано из избранного" if get_lang(chat_id) == "ru" else "Removed from favorites")
        else:
            cursor.execute("INSERT INTO favorites (user_id, nft_id) VALUES (?, ?)", (chat_id, nid))
            bot.answer_callback_query(call.id, "❤️ Добавлено в избранное" if get_lang(chat_id) == "ru" else "❤️ Added to favorites")
        db.commit()

    elif data == "sell_menu":
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        owned = get_owned_ids(chat_id)
        if not owned:
            bot.send_message(chat_id, t(chat_id, "mynft_empty"))
        else:
            markup = types.InlineKeyboardMarkup()
            for nid in owned:
                if nid in NFT:
                    markup.add(types.InlineKeyboardButton(NFT[nid]["name"], callback_data=f"sellpick_{nid}"))
            bot.send_message(chat_id, t(chat_id, "mynft_title"), reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("sellpick_"):
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        nid = int(data.split("_")[1])
        if nid not in get_owned_ids(chat_id):
            bot.answer_callback_query(call.id, "Эта картина тебе больше не принадлежит." if get_lang(chat_id) == "ru" else "This painting is no longer yours.")
            return
        user_states[chat_id] = ("sell_price", nid)
        base = NFT[nid]["price"]
        bot.send_message(chat_id, f"💰 Напиши цену продажи для {NFT[nid]['name']} (только число).\nВ каталоге она оценена в {fmt_money(base, chat_id)}.\nДопустимый диапазон: {fmt_money(MIN_PRICE, chat_id)} – {fmt_money(MAX_SELL_PRICE, chat_id)}.")
        bot.answer_callback_query(call.id)

    elif data == "my_lots":
        lots = cursor.execute("SELECT id, nft_id, price FROM marketplace WHERE seller_id = ?", (chat_id,)).fetchall()
        if not lots:
            bot.send_message(chat_id, "У тебя нет активных лотов.")
        else:
            markup = types.InlineKeyboardMarkup()
            for listing_id, nid, price in lots:
                if nid in NFT:
                    markup.add(types.InlineKeyboardButton(f"❌ Снять: {NFT[nid]['name']} ({fmt_money(price, chat_id)})", callback_data=f"unlist_{listing_id}"))
            bot.send_message(chat_id, "📦 Твои лоты:", reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("unlist_"):
        listing_id = int(data.split("_")[1])
        lot = cursor.execute("SELECT seller_id FROM marketplace WHERE id = ?", (listing_id,)).fetchone()
        if lot and lot[0] == chat_id:
            cursor.execute("DELETE FROM marketplace WHERE id = ?", (listing_id,))
            db.commit()
            bot.send_message(chat_id, t(chat_id, "btn_unlist") + " ✅")
        else:
            bot.send_message(chat_id, "Это не твой лот 🙂")
        bot.answer_callback_query(call.id)

    elif data.startswith("mbuy_"):
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        listing_id = int(data.split("_")[1])
        lot = cursor.execute("SELECT nft_id, seller_id, price FROM marketplace WHERE id = ?", (listing_id,)).fetchone()
        if not lot:
            bot.answer_callback_query(call.id, "Лот уже не существует.")
            return
        nft_id, seller_id, price = lot
        if nft_id not in NFT:
            bot.answer_callback_query(call.id, "Эта картина больше не доступна.")
            return
        if seller_id == chat_id:
            bot.answer_callback_query(call.id, "Это твой собственный лот 🙂")
            return
        if get_balance(chat_id) < price:
            bot.answer_callback_query(call.id, f"Не хватает {fmt_money(price - get_balance(chat_id), chat_id)}. Пополни баланс!")
            return
        change_balance(chat_id, -price)
        change_balance(seller_id, price)
        cursor.execute(
            "INSERT INTO purchases (user_id, nft_id, nft_name, price, seller_id) VALUES (?, ?, ?, ?, ?)",
            (chat_id, nft_id, NFT[nft_id]["name"], price, seller_id)
        )
        cursor.execute("DELETE FROM marketplace WHERE id = ?", (listing_id,))
        db.commit()
        page_msgs.pop((chat_id, "market"), None)
        page_msgs.pop((chat_id, "mynft"), None)
        page_msgs.pop((seller_id, "mynft"), None)
        safe_send_photo(chat_id, NFT[nft_id]["file"], f"🎉 Куплено на площадке!\n{NFT[nft_id]['name']} ({NFT[nft_id]['year']} год) теперь твой!")
        try:
            bot.send_message(seller_id, f"💰 Твою картину {NFT[nft_id]['name']} купили за {fmt_money(price, seller_id)}!\nБаланс: {fmt_money(get_balance(seller_id), seller_id)}")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Покупка прошла!" if get_lang(chat_id) == "ru" else "Purchase complete!")

    elif data == "noop":
        bot.answer_callback_query(call.id)

    else:
        bot.answer_callback_query(call.id)

# ---------- Запуск ----------
while True:  # если сеть мигнула — бот сам перезапускает приём сообщений
    try:
        bot.polling(non_stop=True, timeout=60)
    except Exception as e:
        print(f"Сбой соединения: {e}. Перезапуск через 5 секунд...")
        time.sleep(5)
