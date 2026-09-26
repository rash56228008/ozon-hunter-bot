import asyncio
import logging
import sqlite3
import os
import random
import time
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

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 12; Samsung Galaxy S21) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "WildBerries/6.5.8 (iPhone; iOS 16.0; Scale/3.00)",
    "WildBerries/6.5.8 (Android 12; Samsung Galaxy S21)",
]

WB_ENDPOINTS = [
    "https://search.wb.ru/exactmatch/ru/common/v5/search",
    "https://search.wb.ru/exactmatch/ru/common/v4/search",
]

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

def _make_wb_session():
    session = requests.Session()
    ua = random.choice(USER_AGENTS)
    session.headers.update({
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
        "Connection": "keep-alive",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "x-queryid": f"qid{int(time.time() * 1000)}",
    })
    # первый запрос — имитация посещения главной
    try:
        session.get("https://www.wildberries.ru/", timeout=10)
        time.sleep(random.uniform(0.5, 1.5))
    except:
        pass
    return session

def _sync_search_wb(search_term: str, max_price: int = None):
    session = _make_wb_session()
    
    for endpoint in WB_ENDPOINTS:
        try:
            params = {
                "query": search_term,
                "resultset": "catalog",
                "limit": "50",
                "sort": "priceup",
                "page": "1",
                "curr": "rub",
                "lang": "ru",
                "locale": "ru",
                "ab_testing": "false",
                "appType": "1",
            }
            
            time.sleep(random.uniform(1, 2))
            
            resp = session.get(endpoint, params=params, timeout=30)
            logging.info(f"WB {endpoint}: {resp.status_code}, len: {len(resp.text)}")
            logging.info(f"WB preview: {resp.text[:500]}")
            
            if resp.status_code == 200:
                data = resp.json()
                products = data.get("data", {}).get("products", [])
                logging.info(f"Products: {len(products)}")
                
                if products:
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
            logging.error(f"WB endpoint error {endpoint}: {e}")
            continue
    
    return []

def _sync_search_ozon(search_term: str, max_price: int = None):
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.ozon.ru/",
            "x-o3-app-name": "ozon-front",
            "x-o3-app-version": "6.71.0",
            "x-o3-device-type": "desktop",
        })
        
        url = f"https://www.ozon.ru/api/composer-api.bff/page/json/v2?url=/search/?text={requests.utils.quote(search_term)}&sort=price_asc"
        resp = session.get(url, timeout=30)
        logging.info(f"Ozon status: {resp.status_code}, len: {len(resp.text)}")
        
        if resp.status_code == 200:
            import json
            data = resp.json()
            items = []
            import re
            for key, val in data.get("widgetStates", {}).items():
                if any(x in key for x in ["searchResultsV2", "tileGrid"]):
                    try:
                        widget = json.loads(val) if isinstance(val, str) else val
                        for item in widget.get("items", []):
                            try:
                                name = item.get("title", "") or item.get("name", "")
                                price_data = item.get("price", {})
                                price_str = re.sub(r"[^\d]", "", str(price_data.get("price", "0")))
                                price = int(price_str) if price_str else 0
                                action = item.get("action", {})
                                link = "https://ozon.ru" + (action.get("link", "") if isinstance(action, dict) else "")
                                if name and price > 0:
                                    if max_price is None or price <= max_price:
                                        items.append({"name": name[:80], "price": price, "link": link})
                            except:
                                continue
                    except:
                        continue
            items.sort(key=lambda x: x["price"])
            return items[:10]
    except Exception as e:
        logging.error(f"Ozon error: {e}")
    return []

async def search_products(category_slug: str, max_price: int = None, query: str = None):
    search_term = query if query else category_slug
    logging.info(f"Searching: {search_term}")
    loop = asyncio.get_event_loop()
    
    # пробуем WB
    results = await loop.run_in_executor(executor, _sync_search_wb, search_term, max_price)
    if results:
        return results, "WB"
    
    # если WB не дал — пробуем Ozon
    results = await loop.run_in_executor(executor, _sync_search_ozon, search_term, max_price)
    if results:
        return results, "Ozon"
    
    return [], None

def format_results(items, category, max_price, query, source=None):
    if not items:
        return "😕 Ничего не нашёл. Попробуй другую категорию или убери фильтр цены."
    text = f"🔍 <b>{category}</b>"
    if source:
        text += f" · {source}"
    if query:
        text += f" · {query}"
    if max_price:
        text += f" · до {max_price}₽"
    text += "\n\n"
    for i, item in enumerate(items[:5], 1):
        text += f"{i}. <b>{item['name']}</b>\n"
        text += f"   💰 <b>{item['price']:,}₽</b>\n"
        text += f"   <a href='{item['link']}'>Открыть</a>\n\n"
    return text

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Ищу самые дешёвые товары на Wildberries и Ozon.\n\nВыбери категорию:", reply_markup=get_categories_keyboard())

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
    results, source = await search_products(CATEGORIES[data["category"]], data.get("max_price"), data.get("query"))
    text = format_results(results, data["category"], data.get("max_price"), data.get("query"), source)
    await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    save_subscription(callback.from_user.id, data["category"], data.get("max_price"), data.get("query"))
    await state.clear()
    await callback.answer()

async def run_search(message: types.Message, state: FSMContext):
    data = await state.get_data()
    msg = await message.answer("⏳ Ищу...")
    results, source = await search_products(CATEGORIES[data["category"]], data.get("max_price"), data.get("query"))
    text = format_results(results, data["category"], data.get("max_price"), data.get("query"), source)
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
            results, source = await search_products(CATEGORIES.get(category, category), max_price, query)
            if results:
                cheapest = results[0]
                text = f"🔔 <b>Новая находка!</b>\n\n📂 {category}\n📦 {cheapest['name']}\n💰 <b>{cheapest['price']:,}₽</b>\n<a href='{cheapest['link']}'>Открыть</a>"
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
