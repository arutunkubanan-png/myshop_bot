import threading
import time
import telebot
import sqlite3
import os
import re
import glob
import random
import json
from datetime import datetime
from telebot import types

# ============ НАСТРОЙКИ — ЗАМЕНИ НА СВОЁ ============
TOKEN = "9239140325:AAEvV2FCNUKx3oNKaTMsMhKYAOzoersJGUM"   # ← смени токен! он засветился в переписке
ADMIN_ID = 8770504764
PAY_CARD = "2204 2402 9628 4682"    # ← номер твоей карты
MIN_DEPOSIT = 10000                 # ← минимальное пополнение
MAX_DEPOSIT = 100000                # ← максимальное пополнение
MIN_WITHDRAW = 15000                # ← минимальный вывод
MIN_PRICE = 15000                   # ← самая дешёвая картина
MAX_PRICE = 150000                  # ← самая дорогая картина
MAX_SELL_PRICE = 1000000            # ← максимум цены при перепродаже (за один экземпляр)
PAIR_MIN_TOTAL = MIN_PRICE          # ← минимум цены за пару при продаже
ULTRA_RARE_CHANCE = 10              # ← каждая n-я картина — сверхредкая
ULTRA_RARE_MULT = 4                 # ← сверхредкая дороже во столько раз
ULTRA_RARE_CAP = 500000             # ← потолок цены сверхредкой
SUPPORT_USERNAME = "@NFThub_support_bot"
PAGE_SIZE = 1
BG_FILE = "profile_bg.png"
WEBAPP_PORT = int(os.environ.get("PORT", 8443))   # ← Render сам подставит свой порт
WEBAPP_URL = "https://nft-bot-h47h.onrender.com"  # ← адрес витрины (мини-приложение включено)
# ===================================================

bot = telebot.TeleBot(TOKEN)

def notify(uid, text, reply_markup=None):
    """Пишет пользователю; если он заблокировал бота — молча пропускает."""
    try:
        bot.send_message(uid, text, reply_markup=reply_markup)
    except Exception:
        pass

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
    nft_id INTEGER, nft_id2 INTEGER, seller_id INTEGER, price INTEGER)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER, nft_id INTEGER)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, status TEXT DEFAULT 'pending')""")
cursor.execute("""CREATE TABLE IF NOT EXISTS bans (
    user_id INTEGER PRIMARY KEY,
    until REAL,
    reason TEXT)""")

# ---------- Миграции ----------
cols_users = [r[1] for r in cursor.execute("PRAGMA table_info(users)").fetchall()]
if "username" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
if "full_name" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
if "verified" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0")
if "passed_captcha" not in cols_users:
    cursor.execute("ALTER TABLE users ADD COLUMN passed_captcha INTEGER DEFAULT 0")
cols_purch = [r[1] for r in cursor.execute("PRAGMA table_info(purchases)").fetchall()]
if "seller_id" not in cols_purch:
    cursor.execute("ALTER TABLE purchases ADD COLUMN seller_id INTEGER")
if "active" not in cols_purch:
    cursor.execute("ALTER TABLE purchases ADD COLUMN active INTEGER DEFAULT 1")
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
cols_mp = [r[1] for r in cursor.execute("PRAGMA table_info(marketplace)").fetchall()]
if "nft_id2" not in cols_mp:
    cursor.execute("ALTER TABLE marketplace ADD COLUMN nft_id2 INTEGER")

db.commit()

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
                f"🆘 Написать в поддержку вы можете — кнопка «🆘 Поддержка».")
    return (f"⛔ Вы забанены на торговой площадке навсегда.\nПричина: {reason}\n\n"
            f"🆘 Написать в поддержку вы можете — кнопка «🆘 Поддержка».")

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
    rows = cursor.execute(
        "SELECT DISTINCT nft_id FROM purchases WHERE user_id = ? AND active = 1", (user_id,)
    ).fetchall()
    return [r[0] for r in rows]

def transfer_ownership(nft_id, from_user, to_user, price):
    """Продаёт картину: у продавца владение помечается как проданное, покупателю добавляется запись."""
    cursor.execute(
        "UPDATE purchases SET active = 0 WHERE user_id = ? AND nft_id = ? AND active = 1",
        (from_user, nft_id)
    )
    cursor.execute(
        "INSERT INTO purchases (user_id, nft_id, nft_name, price, seller_id, active) VALUES (?, ?, ?, ?, ?, 1)",
        (to_user, nft_id, NFT[nft_id]["name"], price, from_user)
    )

def get_bought_count(user_id):
    return cursor.execute("SELECT COUNT(*) FROM purchases WHERE user_id = ?", (user_id,)).fetchone()[0]

def get_sold_count(user_id):
    return cursor.execute("SELECT COUNT(*) FROM purchases WHERE seller_id = ?", (user_id,)).fetchone()[0]

def fmt_rub(n):
    return f"{n:,}".replace(",", " ")

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

# ---------- Загрузка картин ----------
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

# ---------- Лестница цен: 15 000 → 150 000 ----------
def build_price_ladder(count):
    rnd = random.Random(4242)
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

# ---------- Редкость ----------
def assign_rarities():
    rnd = random.Random(991)
    for nid, item in NFT.items():
        item["base_price"] = item["price"]
        is_ultra = rnd.randint(1, ULTRA_RARE_CHANCE) == 1
        item["rarity"] = "ultra" if is_ultra else "normal"
        if is_ultra:
            item["price"] = min(int(item["base_price"] * ULTRA_RARE_MULT), ULTRA_RARE_CAP)

assign_rarities()

def rarity_name(item):
    return "💎 сверхредкая" if item.get("rarity") == "ultra" else "уникальная"

def rarity_line(item):
    if item.get("rarity") == "ultra":
        return "💎 Сверхредкая! Таких больше нет нигде.\n"
    return ""

def is_listed(nid):
    return cursor.execute(
        "SELECT id FROM marketplace WHERE nft_id = ? OR nft_id2 = ?", (nid, nid)
    ).fetchone()

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
            t = (y - cy0) / (cy1 - cy0)
            r = int(top[0] + (bottom[0] - top[0]) * t)
            g = int(top[1] + (bottom[1] - top[1]) * t)
            b = int(top[2] + (bottom[2] - top[2]) * t)
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

# ---------- Площадка: официальные лоты ----------
def seed_marketplace():
    sold_ids = {r[0] for r in cursor.execute("SELECT DISTINCT nft_id FROM purchases").fetchall()}
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

# ---------- Капча ----------
def send_captcha(chat_id):
    a, b = random.randint(3, 19), random.randint(3, 19)
    if random.random() < 0.5:
        user_states[chat_id] = ("captcha", a + b)
        bot.send_message(chat_id, f"🤖 Капча: сколько будет {a} + {b}?\n\nНапиши ответ одним числом.")
    else:
        c = random.randint(2, 9)
        user_states[chat_id] = ("captcha", a * c)
        bot.send_message(chat_id, f"🤖 Капча: сколько будет {a} × {c}?\n\nНапиши ответ одним числом.")

def captcha_ok(chat_id):
    st = user_states.get(chat_id)
    if st and st[0] == "captcha":
        bot.send_message(chat_id, "🤖 Сначала реши капчу!")
        send_captcha(chat_id)
        return False
    return True

# ---------- Клавиатуры ----------
def profile_keyboard(admin_view=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if admin_view:
        markup.row(types.KeyboardButton("➕ Выдать баланс"), types.KeyboardButton("💳 Пополнить"), types.KeyboardButton("💸 Вывести"))
    else:
        markup.row(types.KeyboardButton("💳 Пополнить"), types.KeyboardButton("💸 Вывести"))
    markup.row(types.KeyboardButton("👤 Личный кабинет"), types.KeyboardButton("📝 Верификация"))
    markup.row(types.KeyboardButton("❤️ Избранное"), types.KeyboardButton("🖼 Мои NFT"))
    markup.row(types.KeyboardButton("➕ Создать NFT"), types.KeyboardButton("⚙️ Настройки"))
    markup.row(types.KeyboardButton("📜 Транзакции"), types.KeyboardButton("🏪 Площадка"))
    markup.add(types.KeyboardButton("🆘 Поддержка"))
    return markup

# ---------- Личный кабинет ----------
def send_profile(chat_id, greet=False, name=None):
    register(chat_id)
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    if greet and name:
        text = (
            f"👋 <b>Привет, {name}!</b>\n\n"
            "Добро пожаловать в <b>NFT Маркет</b> — место, где живут коллекционные картины 💎\n\n"
            "🏪 <b>Площадка</b> — витрина со всеми картинами, покупка в два касания\n"
            "👤 <b>Личный кабинет</b> — баланс и твоя статистика\n"
            f"💳 <b>Пополнить</b> — закинь баланс от {fmt_rub(MIN_DEPOSIT)} ₽ и вперёд\n\n"
            "👇 Жми на кнопки ниже — начнём!"
        )
    else:
        nick = get_username(chat_id)
        nick_line = f"Ник: <b>@{nick}</b>\n" if nick else ""
        owned = len(get_owned_ids(chat_id))
        bought = get_bought_count(chat_id)
        sold = get_sold_count(chat_id)
        verif_row = cursor.execute("SELECT verified, full_name FROM users WHERE id = ?", (chat_id,)).fetchone()
        verified = verif_row[0] if verif_row else 0
        fio = verif_row[1] if verif_row else None
        verif_line = (
            f"Верификация: ✅ <b>Верифицирован</b>\nФИО: <b>{fio}</b>\n"
            if verified and fio else "Верификация: ⚠️ <b>Не верифицирован</b>\n"
        )
        text = (
            "👤 <b>Личный кабинет</b>\n\n"
            f"💰 Баланс: <b>{fmt_rub(get_balance(chat_id))} RUB</b>\n"
            f"⏳ На выводе: <b>{fmt_rub(get_pending_withdraw(chat_id))} RUB</b>\n"
            f"📈 Оборот средств: <b>{fmt_rub(get_turnover(chat_id))} RUB</b>\n\n"
            f"🖼 Картин в коллекции: <b>{owned}</b>\n"
            f"🛒 Куплено картин: <b>{bought}</b>\n"
            f"📤 Продано картин: <b>{sold}</b>\n\n"
            f"{verif_line}"
            f"{nick_line}"
            f"Ваш ID: <code>{chat_id}</code>\n\n"
            f"Дата и время: {now}"
        )
    kb = profile_keyboard(admin_view=(chat_id == ADMIN_ID))
    if os.path.exists(BG_FILE):
        with open(BG_FILE, "rb") as photo:
            bot.send_photo(chat_id, photo, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "👤 Личный кабинет")
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
    name = message.from_user.first_name or "друг"
    row = cursor.execute("SELECT passed_captcha FROM users WHERE id = ?", (message.chat.id,)).fetchone()
    if not row or not row[0]:
        send_captcha(message.chat.id)
        return
    send_profile(message.chat.id, greet=True, name=name)

# ---------- Кнопки профиля ----------
@bot.message_handler(func=lambda m: m.text == "➕ Выдать баланс" and m.chat.id == ADMIN_ID)
def menu_give(message):
    user_states[message.chat.id] = "give_id"
    bot.send_message(
        message.chat.id,
        "➕ Выдача баланса.\n\n"
        "Напиши ник пользователя через @ (например: @ivan_petrov)\n"
        "или его числовой ID — оба варианта работают.\n\n"
        "⚠️ Ник сработает, только если человек хотя бы раз нажал /start в боте."
    )

@bot.message_handler(func=lambda m: m.text == "💳 Пополнить")
def menu_deposit(message):
    if not captcha_ok(message.chat.id):
        return
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    user_states[message.chat.id] = "deposit_screenshot"
    bot.send_message(
        message.chat.id,
        "➕ Пополнение баланса:\n\n"
        f"1. Открой свой банк (Сбер, Т-Банк и т.д.)\n"
        f"2. Выбери «Перевод по номеру карты»\n"
        f"3. Переведи сумму от {fmt_rub(MIN_DEPOSIT)} до {fmt_rub(MAX_DEPOSIT)} ₽ на карту:\n"
        f"<code>{PAY_CARD}</code>\n"
        f"4. Сделай скриншот перевода (где видно сумму) и отправь его сюда\n\n"
        f"⚠️ Сумма меньше {fmt_rub(MIN_DEPOSIT)} ₽ или больше {fmt_rub(MAX_DEPOSIT)} ₽ не принимается!\n\n"
        f"⏳ После скриншота админ проверит перевод — баланс обновится ✅",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == "💸 Вывести")
def menu_withdraw(message):
    if not captcha_ok(message.chat.id):
        return
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    if not is_verified(message.chat.id):
        bot.send_message(message.chat.id, "⚠️ Вывод средств доступен только верифицированным.\nПройди «📝 Верификацию».")
        return
    user_states[message.chat.id] = "withdraw_card"
    bot.send_message(
        message.chat.id,
        f"💸 Вывод средств\n\n"
        f"Минимальная сумма вывода: <b>{fmt_rub(MIN_WITHDRAW)} ₽</b>\n\n"
        "Напиши номер своей карты одним сообщением:",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == "📝 Верификация")
def menu_verify(message):
    chat_id = message.chat.id
    if not captcha_ok(chat_id):
        return
    if is_banned(chat_id):
        bot.send_message(chat_id, ban_message(chat_id))
        return
    row = cursor.execute("SELECT verified, full_name FROM users WHERE id = ?", (chat_id,)).fetchone()
    if row and row[0]:
        bot.send_message(chat_id, f"📝 Верификация\n\n✅ Вы верифицированы.\nФИО: <b>{row[1]}</b>", parse_mode="HTML")
        return
    user_states[chat_id] = "verify_fio"
    bot.send_message(
        chat_id,
        "📝 Верификация\n\n"
        "Напиши свои фамилию, имя и отчество одной строкой.\n"
        "Пример: Иванов Иван Иванович\n\n"
        "⚠️ Заявка уйдёт только админу проекта.",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == "❤️ Избранное")
def menu_favorites(message):
    rows = cursor.execute("SELECT nft_id FROM favorites WHERE user_id = ?", (message.chat.id,)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, "❤️ В избранном пусто.\nНайди картину на 🏪 площадке и нажми «❤️ В избранное» на её лоте.")
        return
    markup = types.InlineKeyboardMarkup()
    for (nid,) in rows:
        if nid in NFT:
            markup.add(types.InlineKeyboardButton(NFT[nid]["name"], callback_data=f"favshow_{nid}"))
    bot.send_message(message.chat.id, "❤️ Твоё избранное:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🖼 Мои NFT")
def menu_my_nft(message):
    owned = get_owned_ids(message.chat.id)
    if not owned:
        bot.send_message(message.chat.id, "🖼 У тебя пока нет NFT.\nКупи первый на 🏪 площадке!")
        return
    bot.send_message(message.chat.id, f"🖼 Твои NFT ({len(owned)}):")
    banned = is_banned(message.chat.id)
    for nid in owned:
        if nid not in NFT:
            continue
        item = NFT[nid]
        markup = types.InlineKeyboardMarkup()
        if not banned:
            markup.add(types.InlineKeyboardButton("🛒 Продать (парой)", callback_data=f"sellpick_{nid}"))
        caption = (
            f"{item['name']} — {rarity_name(item)}\n"
            f"📅 Год выпуска: {item['year']}\n"
            f"💰 Цена в каталоге: {fmt_rub(item['price'])} ₽\n"
            f"{rarity_line(item)}"
            f"✨ Уникальный экземпляр — 1 из 1"
        )
        safe_send_photo(message.chat.id, item["file"], caption, markup)

@bot.message_handler(func=lambda m: m.text == "➕ Создать NFT")
def menu_create_nft(message):
    if message.chat.id == ADMIN_ID:
        user_states[message.chat.id] = "create_nft_photo"
        bot.send_message(message.chat.id, f"➕ Создание NFT.\n\nОтправь фото картины:\n(цена от {fmt_rub(MIN_PRICE)} до {fmt_rub(MAX_PRICE)} ₽)")
    else:
        bot.send_message(message.chat.id, "➕ Создание NFT доступно только команде проекта.")

@bot.message_handler(func=lambda m: m.text == "⚙️ Настройки")
def menu_settings(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🌐 Язык: Русский", callback_data="noop"))
    markup.add(types.InlineKeyboardButton("💱 Валюта: RUB", callback_data="noop"))
    bot.send_message(message.chat.id, "⚙️ Настройки профиля:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📜 Транзакции")
def menu_transactions(message):
    rows = cursor.execute("SELECT nft_name, price FROM purchases WHERE user_id = ? ORDER BY rowid DESC LIMIT 10", (message.chat.id,)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, "📜 Транзакций пока нет.")
        return
    lines = [f"• {name} — {fmt_rub(price)} ₽" for name, price in rows]
    bot.send_message(message.chat.id, "📜 Последние транзакции:\n\n" + "\n".join(lines))

@bot.message_handler(func=lambda m: m.text == "🏪 Площадка")
def menu_market(message):
    if not captcha_ok(message.chat.id):
        return
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    if WEBAPP_URL:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🏪 Открыть витрину", web_app=types.WebAppInfo(url=WEBAPP_URL)))
        bot.send_message(message.chat.id, "🏪 Площадка NFT:\n\nНажми кнопку — откроется витрина со всеми лотами.", reply_markup=markup)
    else:
        send_market_page(message.chat.id, 0)

@bot.message_handler(func=lambda m: m.text == "🆘 Поддержка")
def menu_support(message):
    user_states[message.chat.id] = "support"
    bot.send_message(message.chat.id, f"🆘 Поддержка.\n\nНапиши свой вопрос — админ ответит.\nИли пиши напрямую: {SUPPORT_USERNAME}")

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
        bot.send_message(message.chat.id, "📸 Скриншот получен! Админ проверит перевод и одобрит пополнение ⏳")
        try:
            bot.forward_message(ADMIN_ID, message.chat.id, message.message_id)
        except Exception:
            pass
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"ok_{dep_id}_{message.chat.id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{dep_id}_{message.chat.id}"),
        )
        nick = message.from_user.username
        nick_line = f" (@{nick})" if nick else ""
        bot.send_message(ADMIN_ID, f"💳 Заявка на пополнение №{dep_id} от пользователя {message.chat.id}{nick_line}\nПроверь перевод на карте, затем выбери:", reply_markup=kb)
    else:
        bot.send_message(message.chat.id, "Фото получил, но сейчас оно не нужно 🙂")

# ---------- Выбор из мини-витрины ----------
def webapp_buy(chat_id, listing_id):
    if is_banned(chat_id):
        bot.send_message(chat_id, ban_message(chat_id))
        return
    lot = cursor.execute(
        "SELECT nft_id, nft_id2, seller_id, price FROM marketplace WHERE id = ?", (listing_id,)
    ).fetchone()
    if not lot:
        bot.send_message(chat_id, "Лот уже не существует.")
        return
    nft_id, nft_id2, seller_id, price = lot
    if nft_id not in NFT:
        bot.send_message(chat_id, "Эта картина больше не доступна.")
        return
    if seller_id == chat_id:
        bot.send_message(chat_id, "Это твой собственный лот 🙂")
        return
    if get_balance(chat_id) < price:
        bot.send_message(chat_id, f"Не хватает {fmt_rub(price - get_balance(chat_id))} ₽. Пополни баланс!")
        return
    change_balance(chat_id, -price)
    change_balance(seller_id, price)
    if nft_id2 and nft_id2 in NFT:
        p1 = price // 2
        for nid, p in ((nft_id, p1), (nft_id2, price - p1)):
            transfer_ownership(nid, seller_id, chat_id, p)
        bot.send_message(chat_id,
            f"🎉 Пара куплена!\n"
            f"1) {NFT[nft_id]['name']}\n"
            f"2) {NFT[nft_id2]['name']}\n\n"
            f"💰 Оплачено: {fmt_rub(price)} ₽")
    else:
        transfer_ownership(nft_id, seller_id, chat_id, price)
        bot.send_message(chat_id, f"🎉 Куплено! {NFT[nft_id]['name']} — {fmt_rub(price)} ₽\nСмотри в «🖼 Мои NFT».")
    cursor.execute("DELETE FROM marketplace WHERE id = ?", (listing_id,))
    db.commit()
    page_msgs.pop((chat_id, "market"), None)
    try:
        if nft_id2 and nft_id2 in NFT:
            bot.send_message(seller_id, f"💰 Твою пару ({NFT[nft_id]['name']} + {NFT[nft_id2]['name']}) купили за {fmt_rub(price)} ₽!\nБаланс: {fmt_rub(get_balance(seller_id))} ₽")
        else:
            bot.send_message(seller_id, f"💰 Твою картину {NFT[nft_id]['name']} купили за {fmt_rub(price)} ₽!\nБаланс: {fmt_rub(get_balance(seller_id))} ₽")
    except Exception:
        pass

@bot.message_handler(content_types=['web_app_data'])
def webapp_handler(message):
    chat_id = message.chat.id
    register(chat_id, message.from_user.username)
    try:
        payload = json.loads(message.web_app_data.data)
    except Exception:
        return
    if payload.get("action") != "buy":
        return
    try:
        listing_id = int(payload["lot_id"])
    except Exception:
        return
    webapp_buy(chat_id, listing_id)

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
        notify(uid, f"💳 На вашем балансе пополнение: +{fmt_rub(int(amount))} ₽\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
        notify(uid, f"➖ Списание {fmt_rub(int(amount))} ₽.\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
        bot.send_message(uid, f"🆘 Ответ поддержки: {' '.join(parts)}")
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

@bot.message_handler(commands=['rare'])
def admin_rare(message):
    if message.chat.id != ADMIN_ID:
        return
    try:
        nid = int(message.text.split()[1])
    except Exception:
        bot.send_message(ADMIN_ID, "Формат: /rare номер_картины (например: /rare 5)")
        return
    if nid not in NFT:
        bot.send_message(ADMIN_ID, "Картины с таким номером нет.")
        return
    item = NFT[nid]
    if item.get("rarity") == "ultra":
        item["rarity"] = "normal"
        item["price"] = item["base_price"]
        status = "теперь обычная"
    else:
        item["rarity"] = "ultra"
        item["price"] = min(int(item["base_price"] * ULTRA_RARE_MULT), ULTRA_RARE_CAP)
        status = "теперь 💎 сверхредкая"
    lot = cursor.execute("SELECT id, seller_id FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
    if lot and lot[1] == ADMIN_ID:
        cursor.execute("UPDATE marketplace SET price = ? WHERE id = ?", (item["price"], lot[0]))
    db.commit()
    bot.send_message(ADMIN_ID, f"{NFT[nid]['name']} {status}. Цена в каталоге: {fmt_rub(item['price'])} ₽")

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
    if until > 0:
        notify(uid, f"⛔ Вы забанены на торговой площадке на {hours:g} ч.\nПричина: {reason}\n\n🆘 В поддержку написать можно.")
    else:
        notify(uid, f"⛔ Вы забанены на торговой площадке навсегда.\nПричина: {reason}\n\n🆘 В поддержку написать можно.")
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
    notify(uid, "✅ Бан снят! Торговая площадка снова доступна.")

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

    # капча — выше всех остальных состояний
    if state and state[0] == "captcha":
        if message.text.strip() == str(state[1]):
            cursor.execute("UPDATE users SET passed_captcha = 1 WHERE id = ?", (chat_id,))
            db.commit()
            user_states[chat_id] = None
            bot.send_message(chat_id, "✅ Капча пройдена! Теперь заполни верификацию: жми «📝 Верификация».")
        else:
            bot.send_message(chat_id, "❌ Неверно. Решим капчу ещё раз.")
            send_captcha(chat_id)
        return

    if state == "deposit_screenshot":
        bot.send_message(chat_id, "📸 Пришли скриншот перевода картинкой — так заявка обработается быстрее.")
        return

    if state == "verify_fio":
        parts = message.text.strip().split()
        if len(parts) != 3 or not all(p.isalpha() and len(p) >= 2 for p in parts):
            bot.send_message(chat_id, "Нужны ровно три слова. Пример: Иванов Иван Иванович")
            return
        full_name = " ".join(p.capitalize() for p in parts)
        cursor.execute("UPDATE users SET full_name = ? WHERE id = ?", (full_name, chat_id))
        db.commit()
        user_states[chat_id] = None
        bot.send_message(chat_id, "✅ ФИО отправлено. Админ рассмотрит заявку ⏳")
        nick = get_username(chat_id)
        nick_line = f" (@{nick})" if nick else ""
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"vok_{chat_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"vno_{chat_id}"),
        )
        bot.send_message(ADMIN_ID,
            f"📝 Заявка на верификацию от {chat_id}{nick_line}\nФИО: {full_name}",
            reply_markup=kb)
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
        bot.send_message(chat_id, f"Пользователь {uid}{nick_line} найден.\n💰 Текущий баланс: {fmt_rub(get_balance(uid))} ₽\n\nТеперь напиши сумму выдачи (только число):")
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
        bot.send_message(chat_id, f"✅ Готово! Пользователю {uid}{nick_line} выдано {fmt_rub(amount)} ₽.\n💰 Его баланс: {fmt_rub(get_balance(uid))} ₽")
        notify(uid, f"💳 На вашем балансе пополнение: +{fmt_rub(amount)} ₽\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
        notify(uid, f"💳 На вашем балансе пополнение: +{fmt_rub(amount)} ₽\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
            NFT[new_id] = {"name": name, "price": price, "base_price": price,
                           "year": _year_random.randint(2023, 2026), "file": file_path,
                           "rarity": "normal"}
            cursor.execute("INSERT INTO marketplace (nft_id, seller_id, price) VALUES (?, ?, ?)", (new_id, ADMIN_ID, price))
            db.commit()
            user_states[chat_id] = None
            bot.send_message(chat_id, f"✅ NFT создан и выставлен на площадку: {name} за {fmt_rub(price)} ₽!\nСделать его сверхредким: /rare {new_id}")
        except Exception:
            bot.send_message(chat_id, "Формат: Название Цена, например: 🌟 Star 25000")
        return

    if state and state[0] == "sellpair_price":
        if is_banned(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, ban_message(chat_id))
            return
        nid1, nid2 = state[1], state[2]
        try:
            price = int(message.text.strip())
        except ValueError:
            bot.send_message(chat_id, "Нужно число, например: 60000")
            return
        if price < PAIR_MIN_TOTAL:
            bot.send_message(chat_id, f"⚠️ Минимальная цена пары — {fmt_rub(PAIR_MIN_TOTAL)} ₽. Напиши снова:")
            return
        if price > MAX_SELL_PRICE * 2:
            bot.send_message(chat_id, f"⚠️ Максимум за пару — {fmt_rub(MAX_SELL_PRICE * 2)} ₽. Напиши снова:")
            return
        if is_listed(nid1) or is_listed(nid2):
            bot.send_message(chat_id, "Одна из картин уже выставлена. Начни заново через «🖼 Мои NFT».")
            user_states[chat_id] = None
            return
        cursor.execute(
            "INSERT INTO marketplace (nft_id, nft_id2, seller_id, price) VALUES (?, ?, ?, ?)",
            (nid1, nid2, chat_id, price)
        )
        db.commit()
        user_states[chat_id] = None
        listing_id = cursor.execute("SELECT id FROM marketplace WHERE nft_id = ? AND nft_id2 = ?", (nid1, nid2)).fetchone()[0]
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("❌ Снять пару с продажи", callback_data=f"unlist_{listing_id}"))
        bot.send_message(chat_id,
            f"✅ Пара выставлена за {fmt_rub(price)} ₽:\n"
            f"1) {NFT[nid1]['name']}\n"
            f"2) {NFT[nid2]['name']}\n\n"
            f"Когда купят — {fmt_rub(price)} ₽ сразу на твой баланс.", reply_markup=kb)
        return

    if state == "withdraw_card":
        if is_banned(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, ban_message(chat_id))
            return
        if not is_verified(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, "⚠️ Вывод средств доступен только верифицированным.\nПройди «📝 Верификацию».")
            return
        card = message.text.strip()
        if len(card) < 12 or not any(ch.isdigit() for ch in card):
            bot.send_message(chat_id, "Похоже, это не номер карты. Напиши номер одним сообщением:")
            return
        user_states[chat_id] = ("withdraw_amount", card)
        bot.send_message(chat_id, f"Карта: {card}\nТеперь сумма вывода (только число):\n⚠️ Минимум: {fmt_rub(MIN_WITHDRAW)} ₽")
        return

    if state and state[0] == "withdraw_amount":
        if is_banned(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, ban_message(chat_id))
            return
        if not is_verified(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, "⚠️ Вывод средств доступен только верифицированным.\nПройди «📝 Верификацию».")
            return
        card = state[1]
        try:
            amount = int(message.text.strip())
        except ValueError:
            bot.send_message(chat_id, "Нужно число, например: 20000:")
            return
        balance = get_balance(chat_id)
        if amount < MIN_WITHDRAW:
            bot.send_message(chat_id, f"Минимальная сумма вывода — {fmt_rub(MIN_WITHDRAW)} ₽. Напиши сумму снова:")
            return
        if amount > balance:
            bot.send_message(chat_id, f"На балансе только {fmt_rub(balance)} ₽. Напиши сумму снова:")
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
            f"✅ Заявка на вывод {fmt_rub(amount)} ₽ на карту {card} отправлена.\n"
            f"⏳ Админ рассмотрит заявку — после одобрения деньги придут на карту.\n"
            f"💰 Баланс: {fmt_rub(get_balance(chat_id))} ₽\n"
            f"⏳ На выводе: {fmt_rub(amount)} ₽"
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
            f"Карта: {card}\n"
            f"Сумма: {fmt_rub(amount)} ₽\n"
            f"Сумма уже списана с баланса и заморожена до решения.",
            reply_markup=kb
        )
        return

    if state == "support":
        user_states[chat_id] = None
        bot.send_message(chat_id, "Вопрос отправлен! Ожидай ответа ⏳")
        nick = get_username(chat_id)
        nick_line = f" (@{nick})" if nick else ""
        bot.send_message(ADMIN_ID, f"🆘 Вопрос от {chat_id}{nick_line}:\n{message.text}\n\nОтветить: /reply {chat_id} текст")
        return

    bot.send_message(chat_id, "Выбери действие кнопкой ниже 👇", reply_markup=profile_keyboard(admin_view=(chat_id == ADMIN_ID)))

# ---------- Площадка (резервный список, если WEBAPP_URL пустая) ----------
def send_market_page(chat_id, page):
    lots = cursor.execute(
        "SELECT id, nft_id, nft_id2, seller_id, price FROM marketplace ORDER BY price ASC"
    ).fetchall()

    if not lots:
        bot.send_message(chat_id, "📭 На площадке пока пусто. Положи картины в папку pictures и перезапусти бота.")
        return

    total_pages = (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    lot_id, nft_id, nft_id2, seller_id, price = lots[page]
    if nft_id not in NFT:
        return
    item = NFT[nft_id]
    markup = types.InlineKeyboardMarkup()
    if seller_id != chat_id:
        if nft_id2:
            markup.add(types.InlineKeyboardButton(f"🛒 Купить пару за {fmt_rub(price)} ₽", callback_data=f"mbuy_{lot_id}"))
        else:
            markup.add(types.InlineKeyboardButton(f"🛒 Купить за {fmt_rub(price)} ₽", callback_data=f"mbuy_{lot_id}"))
    else:
        markup.add(types.InlineKeyboardButton("❌ Снять с продажи", callback_data=f"unlist_{lot_id}"))
    markup.add(types.InlineKeyboardButton("❤️ В избранное", callback_data=f"fav_{nft_id}"))
    for row in nav_markup(page, total_pages, "mpage_").keyboard:
        markup.row(*row)

    if nft_id2 and nft_id2 in NFT:
        item2 = NFT[nft_id2]
        caption = (
            f"🛒 Лот-пара из двух картин\n\n"
            f"1) 🖼 {item['name']} — {rarity_name(item)}\n"
            f"2) 🖼 {item2['name']} — {rarity_name(item2)}\n\n"
            f"💰 Цена за пару: {fmt_rub(price)} ₽"
        )
        show_photo_page(chat_id, "market", item["file"], caption, markup)
    else:
        caption = (
            f"🖼 {item['name']}\n\n"
            f"📅 Год выпуска: {item['year']}\n"
            f"💰 Цена: {fmt_rub(price)} ₽\n"
            f"{rarity_line(item)}"
            f"✨ Уникальный экземпляр — 1 из 1"
        )
        show_photo_page(chat_id, "market", item["file"], caption, markup)

# ---------- Мини-витрина (веб-сервер в этом же процессе) ----------
WEBAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>NFT Маркет</title>
<style>
  body { background:#12101c; color:#fff; font-family:-apple-system,Arial,sans-serif; margin:0; padding:16px; }
  h1 { font-size:22px; margin:4px 0 14px; }
  .grid { display:grid; grid-template-columns:repeat(2,1fr); gap:12px; }
  .card { background:#1d1930; border-radius:14px; overflow:hidden; }
  .card img { width:100%; aspect-ratio:1; object-fit:cover; display:block; }
  .info { padding:10px; }
  .name { font-size:14px; font-weight:600; margin-bottom:4px; }
  .price { font-size:15px; color:#8de05c; font-weight:700; margin-bottom:8px; }
  .pair { font-size:11px; color:#a99fd0; margin-bottom:6px; }
  .rare { color:#ffd54f; }
  .buy { width:100%; padding:9px 0; border:none; border-radius:10px;
         background:#7c4dff; color:#fff; font-weight:600; font-size:14px; }
</style>
</head>
<body>
<h1>🏪 Площадка</h1>
<div class="grid" id="grid">Загрузка…</div>
<script>
const tg = window.Telegram.WebApp;
tg.expand();

async function load() {
  const lots = await (await fetch("/api/lots")).json();
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  if (!lots.length) {
    grid.innerHTML = "<div style='color:#a99fd0'>Пока пусто. Лоты появятся здесь.</div>";
    return;
  }
  for (const lot of lots) {
    const card = document.createElement("div");
    card.className = "card";
    const img = `/api/photo/${lot.nft_id}`;
    let rareTag = lot.rarity === "ultra" ? "<span class='rare'>💎 сверхредкая</span>" : "";
    let pairLine = "";
    if (lot.nft_id2) pairLine = `<div class="pair">Пара: + ${lot.name2}</div>`;
    card.innerHTML = `
      <img src="${img}" alt="">
      <div class="info">
        <div class="name">${lot.name1} ${rareTag}</div>
        ${pairLine}
        <div class="price">${lot.price.toLocaleString("ru-RU")} ₽${lot.nft_id2 ? " за пару" : ""}</div>
        <button class="buy">Купить</button>
      </div>`;
    card.querySelector(".buy").onclick = () => {
      tg.sendData(JSON.stringify({ action: "buy", lot_id: lot.id }));
    };
    grid.appendChild(card);
  }
}
load();
</script>
</body>
</html>"""

try:
    from flask import Flask, jsonify, send_file
    webapp = Flask(__name__)

    @webapp.route("/")
    def web_index():
        return WEBAPP_HTML

    @webapp.route("/api/lots")
    def web_lots():
        rows = cursor.execute(
            "SELECT id, nft_id, nft_id2, seller_id, price FROM marketplace ORDER BY price ASC"
        ).fetchall()
        out = []
        for lot_id, nft_id, nft_id2, seller_id, price in rows:
            i1 = NFT.get(nft_id)
            i2 = NFT.get(nft_id2) if nft_id2 else None
            out.append({
                "id": lot_id,
                "nft_id": nft_id,
                "nft_id2": nft_id2,
                "price": price,
                "name1": i1["name"] if i1 else f"Картина #{nft_id}",
                "name2": i2["name"] if i2 else "",
                "rarity": i1.get("rarity", "normal") if i1 else "normal",
            })
        return jsonify(out)

    @webapp.route("/api/photo/<int:nid>")
    def web_photo(nid):
        item = NFT.get(nid)
        if not item or not os.path.exists(item["file"]):
            return "not found", 404
        return send_file(item["file"])

    def run_webapp():
        try:
            webapp.run(host="0.0.0.0", port=WEBAPP_PORT, threaded=True, use_reloader=False)
        except Exception as e:
            print(f"Мини-витрина не запущена: {e}")

    threading.Thread(target=run_webapp, daemon=True).start()
    print(f"Мини-витрина: {WEBAPP_URL} (порт {WEBAPP_PORT})")
except ImportError:
    print("Flask не установлен — мини-витрина отключена (pip install flask). Бот работает без неё.")

# ---------- Кнопки ----------
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    chat_id = call.message.chat.id
    data = call.data

    if data.startswith("ok_"):
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
        notify(int(uid), "❌ Твоя заявка на пополнение отклонена. Если считаешь, что это ошибка — напиши в 🆘 Поддержку.")
        bot.answer_callback_query(call.id)

    elif data.startswith("vok_"):
        if chat_id != ADMIN_ID:
            bot.answer_callback_query(call.id)
            return
        uid = int(data.split("_")[1])
        cursor.execute("UPDATE users SET verified = 1 WHERE id = ?", (uid,))
        db.commit()
        bot.send_message(ADMIN_ID, f"✅ Пользователь {uid} верифицирован.")
        notify(uid, "✅ Твоя верификация подтверждена! Все функции открыты.")
        bot.answer_callback_query(call.id)

    elif data.startswith("vno_"):
        if chat_id != ADMIN_ID:
            bot.answer_callback_query(call.id)
            return
        uid = int(data.split("_")[1])
        cursor.execute("UPDATE users SET verified = 0 WHERE id = ?", (uid,))
        db.commit()
        bot.send_message(ADMIN_ID, f"❌ Верификация {uid} отклонена.")
        notify(uid, "❌ Твоя заявка на верификацию отклонена. Подай заново через «📝 Верификацию».")
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
        notify(uid, f"✅ Твоя заявка на вывод {fmt_rub(amount)} ₽ одобрена! Деньги придут на карту в ближайшее время.")
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
        notify(uid, f"❌ Твоя заявка на вывод {fmt_rub(amount)} ₽ отклонена. Деньги возвращены на баланс.\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
        bot.answer_callback_query(call.id)

    elif data.startswith("cat_"):
        bot.answer_callback_query(call.id)
        send_catalog_page(chat_id, int(data.split("_")[1]))

    elif data.startswith("favshow_"):
        nid = int(data.split("_")[1])
        if nid not in NFT:
            bot.answer_callback_query(call.id, "Картина больше не доступна.")
            return
        item = NFT[nid]
        caption = (
            f"{item['name']} — {rarity_name(item)}\n"
            f"📅 Год выпуска: {item['year']}\n"
            f"💰 Цена в каталоге: {fmt_rub(item['price'])} ₽\n"
            f"{rarity_line(item)}"
            f"✨ Уникальный экземпляр — 1 из 1"
        )
        lot = cursor.execute("SELECT id, seller_id, price FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
        markup = types.InlineKeyboardMarkup()
        if lot:
            markup.add(types.InlineKeyboardButton(f"Купить на площадке за {fmt_rub(lot[2])} ₽", callback_data=f"mbuy_{lot[0]}"))
        else:
            markup.add(types.InlineKeyboardButton("Сейчас лота нет 😔", callback_data="noop"))
        safe_send_photo(chat_id, item["file"], caption, markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("mpage_"):
        bot.answer_callback_query(call.id)
        send_market_page(chat_id, int(data.split("_")[1]))

    elif data == "market_list":
        bot.answer_callback_query(call.id)
        send_market_page(chat_id, 0)

    elif data.startswith("fav_"):
        nid = int(data.split("_")[1])
        row = cursor.execute("SELECT 1 FROM favorites WHERE user_id = ? AND nft_id = ?", (chat_id, nid)).fetchone()
        if row:
            cursor.execute("DELETE FROM favorites WHERE user_id = ? AND nft_id = ?", (chat_id, nid))
            bot.answer_callback_query(call.id, "Убрано из избранного")
        else:
            cursor.execute("INSERT INTO favorites (user_id, nft_id) VALUES (?, ?)", (chat_id, nid))
            bot.answer_callback_query(call.id, "❤️ Добавлено в избранное")
        db.commit()

    elif data == "sell_menu":
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        owned = get_owned_ids(chat_id)
        if not owned:
            bot.send_message(chat_id, "У тебя пока нет картин. Купи первую на 🏪 площадке!")
        else:
            markup = types.InlineKeyboardMarkup()
            for nid in owned:
                if nid in NFT:
                    markup.add(types.InlineKeyboardButton(NFT[nid]["name"], callback_data=f"sellpick_{nid}"))
            bot.send_message(chat_id, "🛒 Какую картину продать? (продажа только парами)", reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("sellpick_"):
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        nid = int(data.split("_")[1])
        if nid not in NFT:
            bot.answer_callback_query(call.id, "Картина не найдена.")
            return
        if is_listed(nid):
            bot.send_message(chat_id, "Эта картина уже стоит на площадке.")
            bot.answer_callback_query(call.id)
            return
        rarity = NFT[nid].get("rarity", "normal")
        candidates = []
        for x in get_owned_ids(chat_id):
            if x != nid and x in NFT and NFT[x].get("rarity", "normal") == rarity and not is_listed(x):
                candidates.append(x)
        if not candidates:
            label = "💎 сверхредких" if rarity == "ultra" else "обычных (уникальных)"
            bot.send_message(
                chat_id,
                f"🛒 Продавать можно только парами: {rarity_name(NFT[nid])} + {rarity_name(NFT[nid])}.\n\n"
                f"У тебя нет второй {label} картины, которую можно составить в пару.\n"
                f"Купи ещё одну и возвращайся!"
            )
            bot.answer_callback_query(call.id)
            return
        markup = types.InlineKeyboardMarkup()
        for x in candidates:
            markup.add(types.InlineKeyboardButton(
                f"{NFT[x]['name']} — {rarity_name(NFT[x])}",
                callback_data=f"sellwith_{nid}_{x}"
            ))
        bot.send_message(chat_id, f"🛒 С какой картиной продать «{NFT[nid]['name']}»?\nПара должна быть одной редкости.", reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("sellwith_"):
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        _, nid1, nid2 = data.split("_")
        nid1, nid2 = int(nid1), int(nid2)
        if nid1 not in NFT or nid2 not in NFT:
            bot.answer_callback_query(call.id, "Картина не найдена.")
            return
        if nid1 not in get_owned_ids(chat_id) or nid2 not in get_owned_ids(chat_id):
            bot.send_message(chat_id, "Одной из картин у тебя уже нет.")
            bot.answer_callback_query(call.id)
            return
        if is_listed(nid1) or is_listed(nid2):
            bot.send_message(chat_id, "Одна из картин уже выставлена на площадке.")
            bot.answer_callback_query(call.id)
            return
        if NFT[nid1].get("rarity", "normal") != NFT[nid2].get("rarity", "normal"):
            bot.send_message(chat_id, "Пара должна быть из картин одной редкости 🙂")
            bot.answer_callback_query(call.id)
            return
        user_states[chat_id] = ("sellpair_price", nid1, nid2)
        bot.send_message(chat_id,
            f"💰 Напиши цену за ПАРУ целиком (только число):\n"
            f"1) {NFT[nid1]['name']} — {rarity_name(NFT[nid1])}\n"
            f"2) {NFT[nid2]['name']} — {rarity_name(NFT[nid2])}\n\n"
            f"Допустимо: от {fmt_rub(PAIR_MIN_TOTAL)} до {fmt_rub(MAX_SELL_PRICE * 2)} ₽ за пару.")
        bot.answer_callback_query(call.id)

    elif data == "my_lots":
        lots = cursor.execute("SELECT id, nft_id, price FROM marketplace WHERE seller_id = ?", (chat_id,)).fetchall()
        if not lots:
            bot.send_message(chat_id, "У тебя нет активных лотов.")
        else:
            markup = types.InlineKeyboardMarkup()
            for listing_id, nid, price in lots:
                if nid in NFT:
                    markup.add(types.InlineKeyboardButton(f"❌ Снять: {NFT[nid]['name']} ({fmt_rub(price)} ₽)", callback_data=f"unlist_{listing_id}"))
            bot.send_message(chat_id, "📦 Твои лоты:", reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("unlist_"):
        listing_id = int(data.split("_")[1])
        lot = cursor.execute("SELECT seller_id FROM marketplace WHERE id = ?", (listing_id,)).fetchone()
        if lot and lot[0] == chat_id:
            cursor.execute("DELETE FROM marketplace WHERE id = ?", (listing_id,))
            db.commit()
            bot.send_message(chat_id, "❌ Лот снят с продажи.")
        else:
            bot.send_message(chat_id, "Это не твой лот 🙂")
        bot.answer_callback_query(call.id)

    elif data.startswith("mbuy_"):
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        listing_id = int(data.split("_")[1])
        lot = cursor.execute("SELECT nft_id, nft_id2, seller_id, price FROM marketplace WHERE id = ?", (listing_id,)).fetchone()
        if not lot:
            bot.answer_callback_query(call.id, "Лот уже не существует.")
            return
        nft_id, nft_id2, seller_id, price = lot
        if nft_id not in NFT:
            bot.answer_callback_query(call.id, "Эта картина больше не доступна.")
            return
        if seller_id == chat_id:
            bot.answer_callback_query(call.id, "Это твой собственный лот 🙂")
            return
        if get_balance(chat_id) < price:
            bot.answer_callback_query(call.id, f"Не хватает {fmt_rub(price - get_balance(chat_id))} ₽. Пополни баланс!")
            return
        change_balance(chat_id, -price)
        change_balance(seller_id, price)
        if nft_id2 and nft_id2 in NFT:
            # покупка пары
            p1 = price // 2
            for nid, p in ((nft_id, p1), (nft_id2, price - p1)):
                transfer_ownership(nid, seller_id, chat_id, p)
            cursor.execute("DELETE FROM marketplace WHERE id = ?", (listing_id,))
            db.commit()
            page_msgs.pop((chat_id, "market"), None)
            safe_send_photo(chat_id, NFT[nft_id]["file"],
                f"🎉 Пара куплена!\n"
                f"1) {NFT[nft_id]['name']} — {rarity_name(NFT[nft_id])}\n"
                f"2) {NFT[nft_id2]['name']} — {rarity_name(NFT[nft_id2])}\n\n"
                f"💰 Оплачено: {fmt_rub(price)} ₽")
            notify(seller_id, f"💰 Твою пару ({NFT[nft_id]['name']} + {NFT[nft_id2]['name']}) купили за {fmt_rub(price)} ₽!\nБаланс: {fmt_rub(get_balance(seller_id))} ₽")
            bot.answer_callback_query(call.id, "Пара куплена!")
            return
        # обычная одиночная покупка
        transfer_ownership(nft_id, seller_id, chat_id, price)
        cursor.execute("DELETE FROM marketplace WHERE id = ?", (listing_id,))
        db.commit()
        page_msgs.pop((chat_id, "market"), None)
        safe_send_photo(chat_id, NFT[nft_id]["file"], f"🎉 Куплено на площадке!\n{NFT[nft_id]['name']} ({NFT[nft_id]['year']} год) теперь твой!")
        notify(seller_id, f"💰 Твою картину {NFT[nft_id]['name']} купили за {fmt_rub(price)} ₽!\nБаланс: {fmt_rub(get_balance(seller_id))} ₽")
        bot.answer_callback_query(call.id, "Покупка прошла!")

    elif data == "noop":
        bot.answer_callback_query(call.id)

    else:
        bot.answer_callback_query(call.id)

# ---------- Запуск ----------
print("Бот запущен ✅")
while True:
    try:
        bot.polling(non_stop=True, timeout=60)
    except Exception as e:
        print(f"Сбой соединения: {e}. Перезапуск через 5 секунд...")
        time.sleep(5)
