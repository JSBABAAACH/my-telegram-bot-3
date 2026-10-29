import asyncio
import json
import sqlite3
import io
import qrcode
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)

# Загружаем .env для локального запуска (на хостинге переменные подтянутся сами)
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Твой ID админа (можно тоже вынести в переменные, но пока оставим так)
ADMIN_ID = [1357506399] 

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Если хостинг предоставляет диск (например, /data), пишем туда.
# Если нет — пишем в /tmp (временная папка Linux), чтобы бот не упал.
DB_NAME = os.getenv("DB_PATH", "/tmp/coffee.db")

def get_db():
    return sqlite3.connect(DB_NAME, timeout=10)

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        name TEXT,
        total_orders INTEGER DEFAULT 0,
        orders_until_free INTEGER DEFAULT 5,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        name TEXT,
        price INTEGER,
        is_available BOOLEAN DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_telegram_id INTEGER,
        items TEXT,
        total_price INTEGER,
        was_free BOOLEAN DEFAULT 0,
        status INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        confirmed_at TIMESTAMP
    )''')
    c.execute('SELECT COUNT(*) FROM menu')
    if c.fetchone()[0] == 0:
        menu = [
            ("Напитки", "☕ Латте", 250),
            ("Напитки", "☕ Капучино", 230),
            ("Напитки", "☕ Американо", 180),
            ("Напитки", "🍵 Чай", 150),
            ("Еда", "🥐 Круассан", 120),
            ("Еда", "🥪 Сэндвич", 180),
            ("Еда", "🧁 Маффин", 100),
        ]
        c.executemany("INSERT INTO menu (category, name, price) VALUES (?, ?, ?)", menu)
    conn.commit()
    conn.close()

def get_user(telegram_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, name, total_orders, orders_until_free FROM users WHERE telegram_id = ?", (telegram_id,))
    r = c.fetchone()
    conn.close()
    return r

def create_user(telegram_id, username, name):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (telegram_id, username, name) VALUES (?, ?, ?)", (telegram_id, username, name))
    conn.commit()
    conn.close()

def add_stamp(telegram_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, orders_until_free FROM users WHERE telegram_id = ?", (telegram_id,))
    r = c.fetchone()
    if not r:
        conn.close()
        return None, None
    user_id, until_free = r
    c.execute("UPDATE users SET total_orders = total_orders + 1, orders_until_free = orders_until_free - 1 WHERE id = ?", (user_id,))
    c.execute("SELECT orders_until_free FROM users WHERE id = ?", (user_id,))
    new_until = c.fetchone()[0]
    is_free = new_until <= 0
    c.execute('''INSERT INTO orders (user_id, user_telegram_id, items, total_price, was_free, status, confirmed_at)
        VALUES (?, ?, ?, ?, ?, 2, CURRENT_TIMESTAMP)''',
        (user_id, telegram_id, json.dumps([{"name": "📌 Печать (офлайн)", "price": 0}]), 0, 1 if is_free else 0))
    conn.commit()
    conn.close()
    return new_until, is_free

def generate_qr(telegram_id):
    qr_data = f"https://t.me/akatsukicoffeebot?start=qrid_{telegram_id}"
    qr = qrcode.make(qr_data)
    buf = io.BytesIO()
    qr.save(buf, format='PNG')
    buf.seek(0)
    return buf.getvalue()

def main_kb(telegram_id=None):
    kb = [
        [KeyboardButton(text="📋 Меню")],
        [KeyboardButton(text="🛒 Корзина")],
        [KeyboardButton(text="📱 Мой QR-код")],
        [KeyboardButton(text="👤 Мой профиль")],
        [KeyboardButton(text="📦 История заказов")]
    ]
    if telegram_id in ADMIN_ID:
        kb.append([KeyboardButton(text="👨‍💼 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(Command("start"))
async def start(message: types.Message):
    args = message.text.split()
    
    if len(args) > 1 and args[1].startswith("qrid_"):
        try:
            tid = int(args[1].replace("qrid_", ""))
        except:
            await message.answer("❌ Неверный QR-код")
            return
        if message.from_user.id not in ADMIN_ID:
            await message.answer("❌ Только для админов")
            return
        user = get_user(tid)
        if not user:
            await message.answer("❌ Пользователь не найден. Попроси клиента нажать /start")
            return
        user_id, name, orders, until_free = user
        free_txt = "🎁 Есть право на бесплатный кофе!" if until_free <= 0 else f"☕ Осталось {until_free} заказов"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Поставить печать", callback_data=f"stamp_{tid}")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data=f"prof_{tid}")]
        ])
        await message.answer(
            f"🔍 **Найден клиент!**\n\n"
            f"👤 {name}\n"
            f"📦 Заказов: {orders}\n"
            f"{free_txt}\n\n"
            f"Нажми кнопку:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return
    
    create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = get_user(message.from_user.id)
    if user:
        _, name, orders, until_free = user
        await message.answer(
            f"☕ Добро пожаловать в кофейню, {message.from_user.first_name}!\n\n"
            f"📦 Заказов сделано: {orders}\n"
            f"☕ До бесплатного кофе: {until_free} заказов\n\n"
            f"🎁 **Каждый 5-й кофе в подарок!**\n"
            f"📱 Нажми «Мой QR-код», чтобы показать админу",
            reply_markup=main_kb(message.from_user.id),
            parse_mode="Markdown"
        )

@dp.message(lambda m: m.text == "📱 Мой QR-код")
async def show_qr(message: types.Message):
    create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    img = generate_qr(message.from_user.id)
    user = get_user(message.from_user.id)
    orders = user[2] if user else 0
    until_free = user[3] if user else 5
    free_txt = "🎁 Есть право на бесплатный кофе!" if until_free <= 0 else f"☕ Осталось {until_free} заказов"
    await message.answer_photo(
        photo=BufferedInputFile(img, filename="qr.png"),
        caption=f"📱 **Ваш QR-код**\n\n"
                f"Покажите его администратору, чтобы получить печать.\n"
                f"Код действителен всегда! 🔒\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"📦 Заказов: {orders}\n"
                f"{free_txt}\n\n"
                f"🎁 Каждый 5-й кофе в подарок!",
        parse_mode="Markdown"
    )

@dp.message(lambda m: m.text == "👤 Мой профиль")
async def profile(message: types.Message):
    create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Напиши /start")
        return
    _, name, orders, until_free = user
    free_txt = "🎁 **Есть право на бесплатный кофе!**" if until_free <= 0 else f"☕ Осталось {until_free} заказов до подарка"
    await message.answer(
        f"👤 **Мой профиль**\n\n"
        f"Имя: {name}\n"
        f"📦 Всего заказов: {orders}\n"
        f"{free_txt}\n\n"
        f"🎁 **Каждый 5-й кофе в подарок!**",
        parse_mode="Markdown"
    )

@dp.message(lambda m: m.text == "📋 Меню")
async def menu(message: types.Message):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT category FROM menu")
    cats = c.fetchall()
    conn.close()
    kb = []
    for cat in cats:
        kb.append([InlineKeyboardButton(text=cat[0], callback_data=f"cat_{cat[0]}")])
    await message.answer("📋 Выбери категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(lambda c: c.data.startswith("cat_"))
async def show_items(callback: types.CallbackQuery):
    await callback.answer()
    cat = callback.data.replace("cat_", "")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, name, price FROM menu WHERE category = ?", (cat,))
    items = c.fetchall()
    conn.close()
    kb = []
    for item in items:
        kb.append([InlineKeyboardButton(text=f"{item[1]} - {item[2]} ₽", callback_data=f"item_{item[0]}")])
    await callback.message.edit_text(f"📋 {cat}", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(lambda c: c.data.startswith("item_"))
async def add_to_cart(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    item_id = int(callback.data.replace("item_", ""))
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name, price FROM menu WHERE id = ?", (item_id,))
    item = c.fetchone()
    conn.close()
    
    if not item:
        await callback.message.answer("Товар не найден")
        return
    
    data = await state.get_data()
    cart = data.get('cart', [])
    cart.append({"name": item[0], "price": item[1]})
    await state.update_data(cart=cart)
    
    await callback.message.answer(f"✅ {item[0]} добавлен в корзину!")

@dp.message(lambda m: m.text == "🛒 Корзина")
async def show_cart(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cart = data.get('cart', [])
    if not cart:
        await message.answer("🛒 Корзина пуста")
        return
    text = "🛒 **Корзина:**\n\n"
    total = 0
    for i, item in enumerate(cart, 1):
        text += f"{i}. {item['name']} — {item['price']} ₽\n"
        total += item['price']
    text += f"\n💰 Итого: {total} ₽"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton(text="🗑 Очистить", callback_data="clear_cart")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "checkout")
async def checkout(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    cart = data.get('cart', [])
    if not cart:
        await callback.message.answer("Корзина пуста")
        return
    
    total = sum(item['price'] for item in cart)
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE telegram_id = ?", (callback.from_user.id,))
    user = c.fetchone()
    if not user:
        await callback.message.answer("Напиши /start")
        conn.close()
        return
    user_id = user[0]
    
    c.execute('''INSERT INTO orders (user_id, user_telegram_id, items, total_price, status)
        VALUES (?, ?, ?, ?, 0)''', (user_id, callback.from_user.id, json.dumps(cart), total))
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    
    await state.clear()
    
    admin_text = f"🆕 **НОВЫЙ ЗАКАЗ!**\n\n"
    admin_text += f"👤 {callback.from_user.full_name}\n"
    admin_text += f"📦 {len(cart)} позиций:\n"
    for item in cart:
        admin_text += f"• {item['name']} — {item['price']} ₽\n"
    admin_text += f"\n💰 Итого: {total} ₽"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{order_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{order_id}")]
    ])
    
    for admin_id in ADMIN_ID:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="Markdown", reply_markup=kb)
        except:
            pass
    
    await callback.message.answer(
        f"✅ **Заказ оформлен!**\n\n"
        f"💰 Сумма: {total} ₽\n"
        f"⏳ Ожидайте подтверждения.",
        parse_mode="Markdown",
        reply_markup=main_kb(callback.from_user.id)
    )

@dp.callback_query(lambda c: c.data == "clear_cart")
async def clear_cart(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(cart=[])
    await callback.message.edit_text("🗑 Корзина очищена!")

@dp.callback_query(lambda c: c.data.startswith("confirm_"))
async def confirm_order(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_ID:
        await callback.answer("❌ Нет прав!", show_alert=True)
        return
    await callback.answer()
    order_id = int(callback.data.replace("confirm_", ""))
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_telegram_id, items, total_price FROM orders WHERE id = ?", (order_id,))
    order = c.fetchone()
    if not order:
        await callback.message.edit_text("❌ Заказ не найден")
        conn.close()
        return
    user_telegram_id, items_json, total = order
    
    c.execute("UPDATE orders SET status = 1, confirmed_at = CURRENT_TIMESTAMP WHERE id = ?", (order_id,))
    
    c.execute("SELECT id, orders_until_free FROM users WHERE telegram_id = ?", (user_telegram_id,))
    user = c.fetchone()
    is_free = False
    if user:
        user_id, until_free = user
        c.execute("UPDATE users SET total_orders = total_orders + 1, orders_until_free = orders_until_free - 1 WHERE id = ?", (user_id,))
        c.execute("SELECT orders_until_free FROM users WHERE id = ?", (user_id,))
        new_until = c.fetchone()[0]
        is_free = new_until <= 0
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(f"✅ **Заказ #{order_id} подтверждён!**")
    
    client_text = f"✅ **Ваш заказ подтверждён!**\n\n"
    if is_free:
        client_text += f"🎁 **Поздравляем! Следующий кофе в подарок!**\n\n"
    client_text += f"🔔 Ждём вас в кофейне!"
    
    try:
        await bot.send_message(user_telegram_id, client_text, parse_mode="Markdown")
    except:
        pass

@dp.callback_query(lambda c: c.data.startswith("cancel_"))
async def cancel_order(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_ID:
        await callback.answer("❌ Нет прав!", show_alert=True)
        return
    await callback.answer()
    order_id = int(callback.data.replace("cancel_", ""))
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_telegram_id FROM orders WHERE id = ?", (order_id,))
    order = c.fetchone()
    if not order:
        await callback.message.edit_text("❌ Заказ не найден")
        conn.close()
        return
    user_telegram_id = order[0]
    c.execute("UPDATE orders SET status = -1 WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(f"❌ **Заказ #{order_id} отменён**")
    try:
        await bot.send_message(user_telegram_id, "❌ Ваш заказ был отменён.")
    except:
        pass

@dp.callback_query(lambda c: c.data.startswith("stamp_"))
async def qr_stamp(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_ID:
        await callback.answer("❌ Нет прав!", show_alert=True)
        return
    await callback.answer()
    
    telegram_id = int(callback.data.replace("stamp_", ""))
    user = get_user(telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден")
        return
    
    user_id, name, orders, until_free = user
    new_until, is_free = add_stamp(telegram_id)
    
    if is_free:
        await callback.message.edit_text(
            f"✅ **Печать поставлена!**\n\n"
            f"👤 {name}\n"
            f"🎁 **БЕСПЛАТНЫЙ КОФЕ!**\n\n"
            f"Скажи клиенту, что он может забрать бесплатный кофе!"
        )
        try:
            await bot.send_message(
                telegram_id,
                f"🎁 **Поздравляем!**\n\n"
                f"Ваш 5-й заказ — **бесплатный кофе**!\n"
                f"Приходите к нам за своим подарком! ☕"
            )
        except:
            pass
    else:
        await callback.message.edit_text(
            f"✅ **Печать поставлена!**\n\n"
            f"👤 {name}\n"
            f"☕ До бесплатного кофе: {new_until} заказов"
        )
        try:
            await bot.send_message(
                telegram_id,
                f"✅ **Вам поставлена печать!**\n\n"
                f"☕ До бесплатного кофе: {new_until} заказов\n"
                f"Заказывайте ещё и получайте подарки! 🎁"
            )
        except:
            pass

@dp.callback_query(lambda c: c.data.startswith("prof_"))
async def admin_profile(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_ID:
        await callback.answer("❌ Нет прав!", show_alert=True)
        return
    await callback.answer()
    
    telegram_id = int(callback.data.replace("prof_", ""))
    user = get_user(telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден")
        return
    
    user_id, name, orders, until_free = user
    free_txt = "🎁 **Есть право на бесплатный кофе!**" if until_free <= 0 else f"☕ Осталось {until_free} заказов до подарка"
    await callback.message.edit_text(
        f"👤 **Профиль клиента**\n\n"
        f"Имя: {name}\n"
        f"📦 Всего заказов: {orders}\n"
        f"{free_txt}\n\n"
        f"🎁 **Каждый 5-й кофе в подарок!**",
        parse_mode="Markdown"
    )

@dp.message(lambda m: m.text == "👨‍💼 Админ-панель")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_ID:
        await message.answer("❌ Нет прав!")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Сканировать QR", callback_data="scan_qr")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
    ])
    await message.answer("👨‍💼 **Админ-панель**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "scan_qr")
async def scan_qr(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_ID:
        await callback.answer("❌ Нет прав!", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "📱 **Как сканировать QR-код:**\n\n"
        "1. Открой камеру на телефоне\n"
        "2. Наведи на QR-код клиента\n"
        "3. Перейди по ссылке в Telegram\n"
        "4. Нажми «Поставить печать»\n\n"
        "⚡️ Время на одного клиента: 2 секунды!"
    )

@dp.callback_query(lambda c: c.data == "stats")
async def stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_ID:
        await callback.answer("❌ Нет прав!", show_alert=True)
        return
    await callback.answer()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status = 1 OR status = 2")
    orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE was_free = 1")
    free = c.fetchone()[0]
    conn.close()
    await callback.message.edit_text(
        f"📊 **Статистика**\n\n"
        f"👥 Клиентов: {users}\n"
        f"📦 Заказов: {orders}\n"
        f"🎁 Бесплатных: {free}"
    )

@dp.message(lambda m: m.text == "📦 История заказов")
async def history(message: types.Message):
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT id, items, total_price, status, created_at FROM orders 
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
        ORDER BY created_at DESC LIMIT 10''', (message.from_user.id,))
    orders = c.fetchall()
    conn.close()
    if not orders:
        await message.answer("📦 У тебя пока нет заказов.")
        return
    text = "📦 **История заказов:**\n\n"
    for order in orders:
        order_id, items_json, total, status, date = order
        items = json.loads(items_json)
        if status == 0:
            status_txt = "⏳ Ожидает подтверждения"
        elif status == 1:
            status_txt = "✅ Подтверждён"
        elif status == -1:
            status_txt = "❌ Отменён"
        else:
            status_txt = "✅ Подтверждён (офлайн)"
        text += f"Заказ — {date[:10]} {date[11:16]}\n"
        text += f"📦 {len(items)} позиций\n"
        text += f"💰 {total} ₽\n"
        text += f"📊 {status_txt}\n\n"
    await message.answer(text, parse_mode="Markdown")

async def main():
    init_db()
    print("☕ Кофе-бот запущен! Каждый 5-й кофе в подарок!")
    print(f"👥 Админы: {ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())