import asyncio
import logging
import sqlite3
import httpx
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

CATEGORIES = {
    "Электроника": "elektronika",
    "Смартфоны": "smartfony",
    "Ноутбуки": "noutbuki",
    "Одежда": "odezhda",
    "Обувь": "obuv",
    "Игрушки": "igrushki",
    "Книги": "knigi",
    "Спорт": "sport",
    "Красота": "krasota",
    "Дом и сад": "dom-i-sad",
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

async def search_ozon(category_slug: str, max_price: int = None, query: str = None):
    results = []
    try:
        search_term = query if query else category_slug
        url = f"https://www.ozon.ru/api/composer-api.bff/page/json/v2?url=/search/?text={search_term}&sort=price_asc"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "x-o3-app-name": "ozon-front",
            "x-o3-app-version": "6.71.0",
        }
        
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            
            items = []
            for key, val in data.get("widgetStates", {}).items():
                if "searchResultsV2" in key or "tileGrid" in key:
                    import json
                    try:
                        widget = json.loads(val)
                        items_raw = widget.get("items", [])
                        for item in items_raw:
                            try:
                                name = item.get("title", "")
                                price_raw = item.get("price", {})
                                price_str = str(price_raw.get("price", "0"))
                                price = int(re.sub(r"[^\d]", "", price_str))
                                link = "https://ozon.ru" + item.get("action", {}).get("link", "")
                                
                                if name and price > 0:
                                    if max_price is None or price <= max_price:
                                        items.append({
                                            "name": name[:80],
                                            "price": price,
                                            "link": link
                                        })
                            except:
                                continue
                    except:
                        continue
            
            items.sort(key=lambda x: x["price"])
            results = items[:10]
    except Exception as e:
        logging.error(f"Search error: {e}")
    
    return results

def format_results(items, category, max_price, query):
    if not items:
        return "😕 Ничего не нашёл. Попробуй другую категорию или убери фильтр цены."
    
    text = f"🔍 <b>{category}</b>"
    if query:
        text += f" · {query}"
    if max_price:
        text += f" · до {max_price}₽"
    text += "\n\n"
    
    for i, item in enumerate(items[:5], 1):
        text += f"{i}. <b>{item['name']}</b>\n"
        text += f"   💰 <b>{item['price']:,}₽</b>\n"
        text += f"   <a href='{item['link']}'>Открыть на Ozon</a>\n\n"
    
    return text

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Ищу самые дешёвые товары на Ozon.\n\nВыбери категорию:",
        reply_markup=get_categories_keyboard()
    )

@dp.callback_query(F.data.startswith("cat:"))
async def category_chosen(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await callback.message.edit_text(
        f"📂 Категория: <b>{category}</b>\n\nУкажи максимальную цену в рублях (или пропусти):",
        reply_markup=get_price_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SearchState.waiting_for_price)
    await callback.answer()

@dp.message(SearchState.waiting_for_price)
async def price_entered(message: types.Message, state: FSMContext):
    text = message.text.strip().replace(" ", "").replace("₽", "")
    if not text.isdigit():
        await message.answer("Введи число, например: 1000")
        return
    await state.update_data(max_price=int(text))
    await message.answer(
        "🔎 Хочешь уточнить поиск? Напиши ключевое слово (или пропусти):",
        reply_markup=get_query_keyboard()
    )
    await state.set_state(SearchState.waiting_for_query)

@dp.callback_query(F.data == "skip_price")
async def skip_price(callback: CallbackQuery, state: FSMContext):
    await state.update_data(max_price=None)
    await callback.message.edit_text(
        "🔎 Хочешь уточнить поиск? Напиши ключевое слово (или пропусти):",
        reply_markup=get_query_keyboard(),
    )
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
    items = await search_ozon(
        CATEGORIES[data["category"]],
        data.get("max_price"),
        data.get("query")
    )
    text = format_results(items, data["category"], data.get("max_price"), data.get("query"))
    await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    
    save_subscription(callback.from_user.id, data["category"], data.get("max_price"), data.get("query"))
    await state.clear()
    await callback.answer()

async def run_search(message: types.Message, state: FSMContext):
    data = await state.get_data()
    msg = await message.answer("⏳ Ищу...")
    items = await search_ozon(
        CATEGORIES[data["category"]],
        data.get("max_price"),
        data.get("query")
    )
    text = format_results(items, data["category"], data.get("max_price"), data.get("query"))
    await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    save_subscription(message.from_user.id, data["category"], data.get("max_price"), data.get("query"))
    await state.clear()

def save_subscription(user_id, category, max_price, query):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("DELETE FROM subscriptions WHERE user_id=? AND category=?", (user_id, category))
    c.execute("INSERT INTO subscriptions VALUES (?,?,?,?,?)",
              (user_id, category, max_price, query, ""))
    conn.commit()
    conn.close()

async def monitor_prices():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    subs = c.execute("SELECT user_id, category, max_price, query FROM subscriptions").fetchall()
    conn.close()
    
    for user_id, category, max_price, query in subs:
        try:
            items = await search_ozon(CATEGORIES.get(category, category), max_price, query)
            if items:
                cheapest = items[0]
                text = f"🔔 <b>Новая находка!</b>\n\n"
                text += f"📂 {category}\n"
                text += f"📦 {cheapest['name']}\n"
                text += f"💰 <b>{cheapest['price']:,}₽</b>\n"
                text += f"<a href='{cheapest['link']}'>Открыть на Ozon</a>"
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
