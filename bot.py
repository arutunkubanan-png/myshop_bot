import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class _RenderPingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def _run_render_port():
    try:
        port = int(os.environ.get("PORT", 8000))
        HTTPServer(("0.0.0.0", port), _RenderPingHandler).serve_forever()
    except Exception:
        pass

threading.Thread(target=_run_render_port, daemon=True).start()

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
TOKEN = "9239140325:AAEvV2FCNUKx3oNKaTMsMhKYAOzoersJGUM"   # ← новый токен (старый отзови в @BotFather!)
ADMIN_ID = 8770504764
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
    rows = cursor.execute("SELECT DISTINCT nft_id FROM purchases WHERE user_id = ?", (user_id,)).fetchall()
    return [r[0] for r in rows]

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

# Каждому файлу по порядку присваиваем номер: 1, 2, 3... — независимо от имени файла
_picture_numbers = list(range(1, len(_picture_paths) + 1))

# ---------- Лестница цен: 15 000 → 150 000, шаги в разноброс ----------
def build_price_ladder(count):
    """Цены по возрастанию от 15 000 до 150 000.
    Шаги в разноброс. Если картин много — шаги автоматически подгоняются,
    чтобы все картины шли по возрастанию до ровно 150 000."""
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

# ---------- Площадка: все картины официальными лотами ----------
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
        markup.row(types.KeyboardButton("💰 Продать"))
    if admin_view:
        markup.row(types.KeyboardButton("🖼 Каталог"))
    markup.add(types.KeyboardButton("🆘 Поддержка"))
    return markup


# ---------- Личный кабинет ----------
def send_profile(chat_id, greet=False, name=None):
    register(chat_id)
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        if greet and name:
        catalog_line = "🖼 <b>Каталог</b> — вся коллекция\n" if chat_id == ADMIN_ID else ""
        text = (
            f"👋 <b>Привет, {name}!</b>\n\n"
            "Добро пожаловать в <b>NFT Маркет</b> — место, где живут коллекционные картины 💎\n\n"
            "🏪 <b>Площадка</b> — листай картины от дешёвых к дорогим\n"
            + catalog_line +
            "💰 <b>Продать</b> — выставь свои картины на продажу\n"
            "👤 <b>Личный кабинет</b> — баланс и твоя статистика\n"
            f"💳 <b>Пополнить</b> — закинь баланс от {fmt_rub(MIN_DEPOSIT)} ₽ и вперёд\n\n"
            "👇 Жми на кнопки ниже — начнём!"
        )

        nick = get_username(chat_id)
        nick_line = f"Ник: <b>@{nick}</b>\n" if nick else ""
        owned = len(get_owned_ids(chat_id))
        bought = get_bought_count(chat_id)
        sold = get_sold_count(chat_id)
        text = (
            "👤 <b>Личный кабинет</b>\n\n"
            f"💰 Баланс: <b>{fmt_rub(get_balance(chat_id))} RUB</b>\n"
            f"⏳ На выводе: <b>{fmt_rub(get_pending_withdraw(chat_id))} RUB</b>\n"
            f"📈 Оборот средств: <b>{fmt_rub(get_turnover(chat_id))} RUB</b>\n\n"
            f"🖼 Картин в коллекции: <b>{owned}</b>\n"
            f"🛒 Куплено картин: <b>{bought}</b>\n"
            f"📤 Продано картин: <b>{sold}</b>\n\n"
            "Верификация: ⚠️ <b>Не верифицирован</b>\n"
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
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
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
    bot.send_message(message.chat.id, "📝 Верификация\n\nСтатус: ⚠️ Не верифицирован.\n\nДля верификации отправьте документы админу: " + SUPPORT_USERNAME)

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
            markup.add(types.InlineKeyboardButton("🏪 Выставить на площадку", callback_data=f"sellpick_{nid}"))
        caption = (
            f"{item['name']}\n"
            f"📅 Год выпуска: {item['year']}\n"
            f"💰 Цена в каталоге: {fmt_rub(item['price'])} ₽\n"
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
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    send_market_page(message.chat.id, 0)

@bot.message_handler(func=lambda m: m.text == "🖼 Каталог" and m.chat.id == ADMIN_ID)
def menu_catalog(message):
    send_catalog_page(message.chat.id, 0)
@bot.message_handler(func=lambda m: m.text == "💰 Продать")
def menu_sell(message):
    if is_banned(message.chat.id):
        bot.send_message(message.chat.id, ban_message(message.chat.id))
        return
    start_multi_sell(message.chat.id)

@bot.message_handler(func=lambda m: m.text == "🆘 Поддержка")
def menu_support(message):
    user_states[message.chat.id] = "support"
    bot.send_message(message.chat.id, f"🆘 Поддержка.\n\nНапиши свой вопрос — админ ответит.\nИли пиши напрямую: {SUPPORT_USERNAME}")

# ---------- Каталог: по возрастанию цены ----------
def send_catalog_page(chat_id, page):
    ids = sorted(NFT.keys(), key=lambda n: NFT[n]["price"])
    if not ids:
        bot.send_message(chat_id, "Каталог пуст — положи картины в папку pictures и перезапусти бота.")
        return
    total_pages = (len(ids) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    nid = ids[page]
    item = NFT[nid]
    caption = (
        f"🖼 {item['name']}\n\n"
        f"📅 Год выпуска: {item['year']}\n"
        f"💰 Цена: {fmt_rub(item['price'])} ₽\n"
        f"✨ Уникальный экземпляр — 1 из 1"
    )
    lot = cursor.execute("SELECT id, seller_id, price FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
    markup = types.InlineKeyboardMarkup()
    if lot:
        if lot[1] != chat_id:
            markup.add(types.InlineKeyboardButton(f"🛒 Купить на площадке за {fmt_rub(lot[2])} ₽", callback_data=f"mbuy_{lot[0]}"))
        else:
            markup.add(types.InlineKeyboardButton("❌ Снять с продажи", callback_data=f"unlist_{lot[0]}"))
    markup.add(types.InlineKeyboardButton("❤️ В избранное", callback_data=f"fav_{nid}"))
    for row in nav_markup(page, total_pages, "cat_").keyboard:
        markup.row(*row)
    show_photo_page(chat_id, "catalog", item["file"], caption, markup)

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
        bot.send_message(message.chat.id, "Фото получил, но сейчас оно не нужно 🙂")

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
        bot.send_message(uid, f"💳 На вашем балансе пополнение: +{fmt_rub(int(amount))} ₽\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
        bot.send_message(uid, f"➖ Списание {fmt_rub(int(amount))} ₽.\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
        bot.send_message(chat_id, "📸 Пришли скриншот перевода картинкой — так заявка обработается быстрее.")
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
        try:
            bot.send_message(uid, f"💳 На вашем балансе пополнение: +{fmt_rub(amount)} ₽\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
        bot.send_message(uid, f"💳 На вашем балансе пополнение: +{fmt_rub(amount)} ₽\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
            bot.send_message(chat_id, f"⚠️ Минимальная цена продажи — {fmt_rub(MIN_PRICE)} ₽. Укажи цену не ниже:")
            return
        if price > MAX_SELL_PRICE:
            bot.send_message(chat_id, f"⚠️ Максимальная цена продажи — {fmt_rub(MAX_SELL_PRICE)} ₽. Укажи цену не выше:")
            return
        already = cursor.execute("SELECT id, seller_id FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()
        if already:
            if already[1] == chat_id:
                cursor.execute("UPDATE marketplace SET price = ? WHERE nft_id = ?", (price, nid))
                db.commit()
                bot.send_message(chat_id, f"✅ Цена обновлена: {fmt_rub(price)} ₽")
            else:
                bot.send_message(chat_id, "Эта картина уже выставлена другим человеком.")
            user_states[chat_id] = None
            return
        cursor.execute("INSERT INTO marketplace (nft_id, seller_id, price) VALUES (?, ?, ?)", (nid, chat_id, price))
        db.commit()
        user_states[chat_id] = None
        listing_id = cursor.execute("SELECT id FROM marketplace WHERE nft_id = ?", (nid,)).fetchone()[0]
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("❌ Снять с продажи", callback_data=f"unlist_{listing_id}"))
        bot.send_message(chat_id, f"✅ {NFT[nid]['name']} выставлена за {fmt_rub(price)} ₽. Когда купят — деньги сразу на твой баланс.", reply_markup=kb)
        return

    if state == "withdraw_card":
        if is_banned(chat_id):
            user_states[chat_id] = None
            bot.send_message(chat_id, ban_message(chat_id))
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

def start_multi_sell(chat_id):
    owned = [nid for nid in get_owned_ids(chat_id) if nid in NFT]
    if not owned:
        bot.send_message(chat_id, "У тебя пока нет картин. Купи первую на 🏪 площадке!")
        return
    state = user_states.get(chat_id)
    sel = set(state[1]) if isinstance(state, tuple) and state and state[0] == "multiselect" else set()
    user_states[chat_id] = ("multiselect", sel)
    send_multi_sell_menu(chat_id, sel)

    

def send_multi_sell_menu(chat_id, selected):
    owned = [nid for nid in get_owned_ids(chat_id) if nid in NFT]
    markup = types.InlineKeyboardMarkup()
    for nid in owned:
        mark = "☑️" if nid in selected else "⬜"
        markup.add(types.InlineKeyboardButton(f"{mark} {NFT[nid]['name']}", callback_data=f"multisel_{nid}"))
    if selected:
        markup.add(types.InlineKeyboardButton(f"✅ Выставить на продажу ({len(selected)})", callback_data="multiconfirm"))
    markup.add(types.InlineKeyboardButton("🚪 Отмена", callback_data="multiclear"))
    text = (
        "💰 Продажа картин.\n\n"
        "Нажимай на картины, чтобы отметить их галочкой ☑️ (повторное нажатие снимает галочку).\n"
        "Можно выбрать одну или несколько — цена будет единая для всех.\n\n"
        f"Выбрано: {len(selected)}"
    )
    bot.send_message(chat_id, text, reply_markup=markup)
# ---------- Площадка: просто листаешь, цены растут от дешёвых к дорогим ----------
def send_market_page(chat_id, page):
    lots = cursor.execute(
        "SELECT id, nft_id, seller_id, price FROM marketplace ORDER BY price ASC"
    ).fetchall()

    if not lots:
        bot.send_message(chat_id, "📭 На площадке пока пусто. Положи картины в папку pictures и перезапусти бота.")
        return

    total_pages = (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    lot_id, nft_id, seller_id, price = lots[page]
    if nft_id not in NFT:
        return
    item = NFT[nft_id]
    markup = types.InlineKeyboardMarkup()
    if seller_id != chat_id:
        markup.add(types.InlineKeyboardButton(f"🛒 Купить за {fmt_rub(price)} ₽", callback_data=f"mbuy_{lot_id}"))
    else:
        markup.add(types.InlineKeyboardButton("❌ Снять с продажи", callback_data=f"unlist_{lot_id}"))
    markup.add(types.InlineKeyboardButton("❤️ В избранное", callback_data=f"fav_{nft_id}"))
    for row in nav_markup(page, total_pages, "mpage_").keyboard:
        markup.row(*row)
    caption = (
        f"🖼 {item['name']}\n\n"
        f"📅 Год выпуска: {item['year']}\n"
        f"💰 Цена: {fmt_rub(price)} ₽\n"
        f"✨ Уникальный экземпляр — 1 из 1"
    )
    show_photo_page(chat_id, "market", item["file"], caption, markup)

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
        bot.send_message(int(uid), "❌ Твоя заявка на пополнение отклонена. Если считаешь, что это ошибка — напиши в 🆘 Поддержку.")
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
        bot.send_message(uid, f"✅ Твоя заявка на вывод {fmt_rub(amount)} ₽ одобрена! Деньги придут на карту в ближайшее время.")
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
        bot.send_message(uid, f"❌ Твоя заявка на вывод {fmt_rub(amount)} ₽ отклонена. Деньги возвращены на баланс.\n💰 Баланс: {fmt_rub(get_balance(uid))} ₽")
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
            f"{item['name']}\n"
            f"📅 Год выпуска: {item['year']}\n"
            f"💰 Цена в каталоге: {fmt_rub(item['price'])} ₽\n"
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
            bot.send_message(chat_id, "📈 Какую картину выставить на продажу?", reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif data.startswith("sellpick_"):
        if is_banned(chat_id):
            bot.send_message(chat_id, ban_message(chat_id))
            bot.answer_callback_query(call.id)
            return
        nid = int(data.split("_")[1])
        user_states[chat_id] = ("sell_price", nid)
        base = NFT[nid]["price"]
        bot.send_message(chat_id, f"💰 Напиши цену продажи для {NFT[nid]['name']} (только число).\nВ каталоге она оценена в {fmt_rub(base)} ₽.\nДопустимый диапазон: {fmt_rub(MIN_PRICE)} – {fmt_rub(MAX_SELL_PRICE)} ₽.")
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
            bot.answer_callback_query(call.id, f"Не хватает {fmt_rub(price - get_balance(chat_id))} ₽. Пополни баланс!")
            return
        change_balance(chat_id, -price)
        change_balance(seller_id, price)
        cursor.execute(
            "INSERT INTO purchases (user_id, nft_id, nft_name, price, seller_id) VALUES (?, ?, ?, ?, ?)",
            (call.from_user.id, nft_id, NFT[nft_id]["name"], price, seller_id)
        )

        

        cursor.execute("DELETE FROM marketplace WHERE id = ?", (listing_id,))
        db.commit()
        page_msgs.pop((chat_id, "market"), None)
        safe_send_photo(chat_id, NFT[nft_id]["file"], f"🎉 Куплено на площадке!\n{NFT[nft_id]['name']} ({NFT[nft_id]['year']} год) теперь твой!")
        try:
            bot.send_message(seller_id, f"💰 Твою картину {NFT[nft_id]['name']} купили за {fmt_rub(price)} ₽!\nБаланс: {fmt_rub(get_balance(seller_id))} ₽")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Покупка прошла!")

        elif data.startswith("multisel_"):
        nid = data[len("multisel_"):]
        state = user_states.get(chat_id)
        if state and state[0] == "multiselect":
            sel = set(state[1])
            if nid in sel:
                sel.discard(nid)
            else:
                sel.add(nid)
            user_states[chat_id] = ("multiselect", sel)
            send_multi_sell_menu(chat_id, sel)
    elif data == "multiconfirm":
        state = user_states.get(chat_id)
        if state and state[0] == "multiselect" and state[1]:
            user_states[chat_id] = ("multi_price", set(state[1]))
            bot.send_message(chat_id, "💰 Введи цену для выбранных картин (одним числом):")
    elif data == "multiclear":
        user_states.pop(chat_id, None)
        bot.send_message(chat_id, "Продажа отменена.")

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



