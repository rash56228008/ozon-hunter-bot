import asyncio
import logging
import sqlite3
import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SCRAPER_KEY = os.environ.get("SCRAPER_KEY")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()
executor = ThreadPoolExecutor(max_workers=4)

CATEGORIES = {
    "Электроника": "электроника",
    "Смартфоны": "смартфоны",
    "Ноутбуки": "ноутбуки",
    "Одежда": "одежда",
    "Обувь": "обувь",
    "Игрушки": "игрушки",
    "Книги": "книги",
    "Спорт": "спорт",
    "Красота": "косметика",
    "Дом и сад": "дом",
}

class SearchState(StatesGroup):
    waiting_for_price = State()
    waiting_for_query = State()

def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER,
            category TEXT,
            max_price INTEGER,
            query TEXT,
            last_seen TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_categories_keyboard():
    buttons = []
    for name in CATEGORIES:
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"cat:{name}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_price_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_price")]
    ])

def get_query_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_query")]
    ])

def _sync_search_wb(search_term: str, max_price: int = None):
    try:
        payload = {
            "api_key": SCRAPER_KEY,
            "url": f"https://search.wb.ru/exactmatch/ru/common/v5/search?query={requests.utils.quote(search_term)}&resultset=catalog&limit=50&sort=priceup&page=1",
            "country_code": "ru",
            "keep_headers": "true",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.wildberries.ru/",
        }
        resp = requests.get(
            "https://api.scraperapi.com/",
            params=payload,
            headers=headers,
            timeout=60
        )
        logging.info(f"WB status: {resp.status_code}, len: {len(resp.text)}")
        logging.info(f"WB preview: {resp.text[:300]}")

        if resp.status_code != 200:
            return []

        data = resp.json()
        products = data.get("data", {}).get("products", [])
        logging.info(f"Products: {len(products)}")

        items = []
        for p in products:
            try:
                name = p.get("name", "")
                price = p.get("salePriceU", p.get("priceU", 0)) // 100
                pid = p.get("id", "")
                link = f"https://www.wildberries.ru/catalog/{pid}/detail.aspx"
                if name and price > 0:
                    if max_price is None or price <= max_price:
                        items.append({"name": name[:80], "price": price, "link": link})
            except:
                continue

        items.sort(key=lambda x: x["price"])
        return items[:10]

    except Exception as e:
        logging.error(f"Sync WB error: {e}")
        return []

async def search_wb(category_slug: str, max_price: int = None, query: str = None):
    search_term = query if query else category_slug
    logging.info(f"Searching: {search_term}")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _sync_search_wb, search_term, max_price)

def format_results(items, category, max_price, query):
    if not items:
        return "😕 Ничего не нашёл. Попробуй другую категорию или убери фильтр цены."
    text = f"🔍 <b>{category}</b> · Wildberries"
    if query:
        text += f" · {query}"
    if max_price:
        text += f" · до {max_price}₽"
    text += "\n\n"
    for i, item in enumerate(items[:5], 1):
        text += f"{i}. <b>{item['name']}</b>\n"
        text += f"   💰 <b>{item['price']:,}₽</b>\n"
        text += f"   <a href='{item['link']}'>Открыть на WB</a>\n\n"
    return text

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Ищу самые дешёвые товары на Wildberries.\n\nВыбери категорию:", reply_markup=get_categories_keyboard())

@dp.callback_query(F.data.startswith("cat:"))
async def category_chosen(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await callback.message.edit_text(f"📂 Категория: <b>{category}</b>\n\nУкажи максимальную цену в рублях (или пропусти):", reply_markup=get_price_keyboard(), parse_mode="HTML")
    await state.set_state(SearchState.waiting_for_price)
    await callback.answer()

@dp.message(SearchState.waiting_for_price)
async def price_entered(message: types.Message, state: FSMContext):
    text = message.text.strip().replace(" ", "").replace("₽", "")
    if not text.isdigit():
        await message.answer("Введи число, например: 1000")
        return
    await state.update_data(max_price=int(text))
    await message.answer("🔎 Хочешь уточнить поиск? Напиши ключевое слово (или пропусти):", reply_markup=get_query_keyboard())
    await state.set_state(SearchState.waiting_for_query)

@dp.callback_query(F.data == "skip_price")
async def skip_price(callback: CallbackQuery, state: FSMContext):
    await state.update_data(max_price=None)
    await callback.message.edit_text("🔎 Хочешь уточнить поиск? Напиши ключевое слово (или пропусти):", reply_markup=get_query_keyboard())
    await state.set_state(SearchState.waiting_for_query)
    await callback.answer()

@dp.message(SearchState.waiting_for_query)
async def query_entered(message: types.Message, state: FSMContext):
    await state.update_data(query=message.text.strip())
    await run_search(message, state)

@dp.callback_query(F.data == "skip_query")
async def skip_query(callback: CallbackQuery, state: FSMContext):
    await state.update_data(query=None)
    await callback.message.edit_text("⏳ Ищу...", reply_markup=None)
    data = await state.get_data()
    items = await search_wb(CATEGORIES[data["category"]], data.get("max_price"), data.get("query"))
    text = format_results(items, data["category"], data.get("max_price"), data.get("query"))
    await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    save_subscription(callback.from_user.id, data["category"], data.get("max_price"), data.get("query"))
    await state.clear()
    await callback.answer()

async def run_search(message: types.Message, state: FSMContext):
    data = await state.get_data()
    msg = await message.answer("⏳ Ищу...")
    items = await search_wb(CATEGORIES[data["category"]], data.get("max_price"), data.get("query"))
    text = format_results(items, data["category"], data.get("max_price"), data.get("query"))
    await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    save_subscription(message.from_user.id, data["category"], data.get("max_price"), data.get("query"))
    await state.clear()

def save_subscription(user_id, category, max_price, query):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("DELETE FROM subscriptions WHERE user_id=? AND category=?", (user_id, category))
    c.execute("INSERT INTO subscriptions VALUES (?,?,?,?,?)", (user_id, category, max_price, query, ""))
    conn.commit()
    conn.close()

async def monitor_prices():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    subs = c.execute("SELECT user_id, category, max_price, query FROM subscriptions").fetchall()
    conn.close()
    for user_id, category, max_price, query in subs:
        try:
            items = await search_wb(CATEGORIES.get(category, category), max_price, query)
            if items:
                cheapest = items[0]
                text = f"🔔 <b>Новая находка!</b>\n\n📂 {category}\n📦 {cheapest['name']}\n💰 <b>{cheapest['price']:,}₽</b>\n<a href='{cheapest['link']}'>Открыть на WB</a>"
                await bot.send_message(user_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logging.error(f"Monitor error for {user_id}: {e}")

async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    scheduler.add_job(monitor_prices, "interval", hours=2)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
