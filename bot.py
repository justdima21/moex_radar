import asyncio
import logging
import re
import sys
import time
from typing import Dict, Set

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from chart_service import generate_ticker_chart
from config import ALERT_VOLUME_THRESHOLD, BOT_TOKEN, SCAN_INTERVAL_SECONDS
from moex_service import (
    build_radar_report,
    get_market_status,
    get_top_anomalous_ticker,
    scan_market,
)
from storage import (
    add_price_alert,
    add_subscriber,
    delete_price_alert,
    get_active_price_alerts,
    get_subscribers,
    get_user_alerts,
    init_db,
    is_subscribed,
    mark_alert_triggered,
    remove_subscriber,
)

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("moex-bot")

if not BOT_TOKEN:
    logger.error("ОШИБКА: TELEGRAM_BOT_TOKEN не задан! Укажите токен в файле .env")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
dp = Dispatcher()

# Кэш последних разосланных алертов тикеров (чтобы не спамить чаще 1 раза в 2 часа)
last_alert_times: Dict[str, float] = {}


def get_radar_keyboard(current_cat: str = "summary") -> InlineKeyboardMarkup:
    """Генерирует интерактивную клавиатуру переключения категорий и функций."""
    buttons = [
        [
            InlineKeyboardButton(text="📊 Общий" + (" •" if current_cat == "summary" else ""), callback_data="cat:summary"),
            InlineKeyboardButton(text="🚨 Объёмы" + (" •" if current_cat == "volumes" else ""), callback_data="cat:volumes"),
            InlineKeyboardButton(text="🗜 Пружины" + (" •" if current_cat == "squeezes" else ""), callback_data="cat:squeezes"),
        ],
        [
            InlineKeyboardButton(text="🐂 Быки" + (" •" if current_cat == "bulls" else ""), callback_data="cat:bulls"),
            InlineKeyboardButton(text="🐻 Медведи" + (" •" if current_cat == "bears" else ""), callback_data="cat:bears"),
            InlineKeyboardButton(text="⚡ Пробои/RSI" + (" •" if current_cat == "signals" else ""), callback_data="cat:signals"),
        ],
        [
            InlineKeyboardButton(text="📈 График топ-1", callback_data="action:chart_top"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"cat:{current_cat}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def handle_start(message: Message):
    subscribed = is_subscribed(message.chat.id)
    sub_status = "✅ Вы подписаны на авто-алерты" if subscribed else "❌ Вы не подписаны на авто-алерты"

    text = (
        "👋 <b>Добро пожаловать в профессиональный Радар аномалий Мосбиржи!</b>\n\n"
        "Бот в реальном времени мониторит 35 ключевых акций MOEX (TQBR) и выявляет:\n"
        "• 🚨 <b>Всплески объёмов</b> к средней норме (интерес институционалов)\n"
        "• 🗜 <b>Сжатые пружины</b> — минимум размаха перед сильным импульсом\n"
        "• 🐂 <b>Бычье давление</b> — закрытие у максимумов дня\n"
        "• 🐻 <b>Медвежье давление</b> — распродажи к дневным минимумам\n"
        "• ⚡ <b>Пробои 20-дневных уровней</b> и экстремальный RSI (&gt;70 или &lt;30)\n\n"
        f"Статус подписки: <b>{sub_status}</b>\n\n"
        "<b>Команды:</b>\n"
        "• /radar — открыть интерактивный радар\n"
        "• /chart SBER — построить график по любому тикеру\n"
        "• /alert SBER 310 — установить уведомление по цене\n"
        "• /myalerts — список и удаление ваших алертов\n"
        "• /subscribe — получать автоматические сигналы о резких всплесках рынка\n"
        "• /unsubscribe — отключить авторассылку"
    )
    await message.answer(text, reply_markup=get_radar_keyboard("summary"))


@dp.message(Command("help"))
async def handle_help(message: Message):
    help_text = (
        "📖 <b>Справка по возможностям бота:</b>\n\n"
        "🔹 <b>/radar</b> — Интерактивный радар аномалий рынка с кнопками категорий\n"
        "🔹 <b>/chart ТИКЕР</b> — Дневной график цены и объема акции (например: <code>/chart YDEX</code>)\n"
        "🔹 <b>/alert ТИКЕР ЦЕНА</b> — Установить ценовой алерт (например: <code>/alert LKOH 7400</code>)\n"
        "🔹 <b>/myalerts</b> — Просмотр и удаление настроенных алертов\n"
        "🔹 <b>/subscribe</b> — Подписка на автоматические push-уведомления при аномалиях\n"
        "🔹 <b>/unsubscribe</b> — Отписаться от уведомлений"
    )
    await message.answer(help_text)


@dp.message(Command("radar"))
@dp.message(F.text.lower().in_(["радар", "radar", "сканировать"]))
async def handle_radar_cmd(message: Message):
    status_msg = await message.answer("🔄 <i>Сканирую рынок Мосбиржи...</i>")
    try:
        report = await build_radar_report("summary")
        await status_msg.edit_text(report, reply_markup=get_radar_keyboard("summary"))
    except Exception as e:
        logger.error(f"Ошибка радара: {e}", exc_info=True)
        await status_msg.edit_text("❌ Ошибка при получении данных с биржи.")


@dp.callback_query(F.data.startswith("cat:"))
async def handle_category_switch(query: CallbackQuery):
    category = query.data.split(":")[1]
    try:
        report = await build_radar_report(category)
        if query.message:
            await query.message.edit_text(report, reply_markup=get_radar_keyboard(category))
        await query.answer("Обновлено")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            await query.answer("Данные уже актуальны!")
        else:
            await query.answer("Ошибка обновления", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка переключения категории: {e}", exc_info=True)
        await query.answer("Не удалось получить срез", show_alert=True)


@dp.callback_query(F.data == "action:chart_top")
async def handle_chart_top_callback(query: CallbackQuery):
    await query.answer("Генерирую график топ-аномалии...")
    ticker = await get_top_anomalous_ticker()
    if not ticker:
        await query.message.answer("⚠️ Не удалось определить топ-тикер.")
        return

    buf = await generate_ticker_chart(ticker)
    if not buf:
        await query.message.answer(f"⚠️ Не удалось построить график для {ticker}.")
        return

    photo = BufferedInputFile(buf.getvalue(), filename=f"{ticker}_chart.png")
    caption = f"📈 <b>График дневной динамики {ticker}</b> (Топ всплеск объёма)"
    await query.message.answer_photo(photo, caption=caption)


@dp.message(Command("chart"))
async def handle_chart_command(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("ℹ️ Укажите тикер акции. Пример: <code>/chart SBER</code> или <code>/chart GAZP</code>")
        return

    ticker = args[1].strip().upper()
    status_msg = await message.answer(f"⏳ Строю график для <b>{ticker}</b>...")
    buf = await generate_ticker_chart(ticker)
    if not buf:
        await status_msg.edit_text(f"❌ Не удалось построить график для <b>{ticker}</b>. Проверьте правильность тикера TQBR.")
        return

    photo = BufferedInputFile(buf.getvalue(), filename=f"{ticker}_chart.png")
    await status_msg.delete()
    await message.answer_photo(photo, caption=f"📊 График акции <b>{ticker}</b> (MOEX:TQBR)")


@dp.message(Command("subscribe"))
async def handle_subscribe(message: Message):
    if add_subscriber(message.chat.id):
        await message.answer(
            "🔔 <b>Вы успешно подписались на автоматические алерты!</b>\n"
            "Бот пришлёт уведомление, когда зафиксирует аномальный всплеск объема (≥ 2.5x) или пробой уровней."
        )
    else:
        await message.answer("ℹ️ Вы уже подписаны на уведомления бота.")


@dp.message(Command("unsubscribe"))
async def handle_unsubscribe(message: Message):
    if remove_subscriber(message.chat.id):
        await message.answer("🔕 Вы отписались от автоматических уведомлений.")
    else:
        await message.answer("ℹ️ Вы не были подписаны на уведомления.")


@dp.message(Command("alert"))
async def handle_alert_creation(message: Message):
    # Формат: /alert SBER 310.5
    parts = message.text.strip().split()
    if len(parts) < 3:
        await message.answer("ℹ️ Формат команды:\n<code>/alert ТИКЕР ЦЕНА</code>\n\nПример: <code>/alert SBER 320</code>")
        return

    ticker = parts[1].upper()
    try:
        target_price = float(parts[2].replace(",", "."))
    except ValueError:
        await message.answer("❌ Неверный формат цены. Введите число, например: <code>285.5</code>")
        return

    # Запросим текущую цену тикера, чтобы понять условие (>= или <=)
    market_data = await scan_market([ticker])
    if not market_data:
        condition = "GTE"
    else:
        curr_price = market_data[0]["last"]
        condition = "GTE" if target_price >= curr_price else "LTE"

    cond_text = "≥" if condition == "GTE" else "≤"
    alert_id = add_price_alert(message.chat.id, ticker, target_price, condition)

    await message.answer(
        f"✅ <b>Ценовой алерт установлен!</b>\n"
        f"Тикер: <b>{ticker}</b>\n"
        f"Условие: когда цена станет <b>{cond_text} {target_price:,.2f} ₽</b>\n"
        f"Бот пришлет сообщение, как только уровень будет достигнут."
    )


@dp.message(Command("myalerts"))
async def handle_my_alerts(message: Message):
    alerts = get_user_alerts(message.chat.id)
    active = [a for a in alerts if a[4] == 0]

    if not active:
        await message.answer("📭 У вас нет активных ценовых алертов.\nСоздать: <code>/alert SBER 310</code>")
        return

    text = "🔔 <b>Ваши активные ценовые алерты:</b>\n\n"
    keyboard = []
    for a in active:
        a_id, ticker, target, cond, _ = a
        cond_sign = "≥" if cond == "GTE" else "≤"
        text += f"• <b>{ticker}</b>: цель {cond_sign} <code>{target:,.2f} ₽</code>\n"
        keyboard.append([InlineKeyboardButton(text=f"❌ Удалить {ticker} ({target:,.2f})", callback_data=f"del_alert:{a_id}")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@dp.callback_query(F.data.startswith("del_alert:"))
async def handle_delete_alert(query: CallbackQuery):
    alert_id = int(query.data.split(":")[1])
    if delete_price_alert(alert_id, query.message.chat.id):
        await query.answer("Алерт удалён!")
        await query.message.edit_text("✅ Алерт успешно удалён.")
    else:
        await query.answer("Не удалось найти алерт.", show_alert=True)


async def fast_price_alerts_monitor():
    """Сверхбыстрый монитор алертов: проверяет только тикеры пользователей каждые 3 секунды."""
    logger.info("Сверхбыстрый скальперский монитор алертов запущен (3 сек).")
    await asyncio.sleep(5)

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                active_alerts = get_active_price_alerts()
                if not active_alerts:
                    await asyncio.sleep(3)
                    continue

                tickers_to_check = list({a[2] for a in active_alerts})
                tickers_str = ",".join(tickers_to_check)

                latest_prices = {}

                # 1. Запрашиваем акции (TQBR)
                url_stocks = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json?securities={tickers_str}&iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,LAST,HIGH,LOW,LASTCHANGEPRCNT"
                try:
                    async with session.get(url_stocks, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for row in data.get("marketdata", {}).get("data", []):
                                if row and row[0] and row[1] is not None:
                                    latest_prices[row[0]] = {
                                        "last": float(row[1]),
                                        "high": float(row[2]) if row[2] is not None else float(row[1]),
                                        "low": float(row[3]) if row[3] is not None else float(row[1]),
                                        "chg": float(row[4]) if row[4] is not None else 0.0,
                                        "type": "акция (TQBR)"
                                    }
                except Exception:
                    pass

                # 2. Проверяем фьючерсы (FORTS) для тех, кого не нашли в акциях
                missing_tickers = [t for t in tickers_to_check if t not in latest_prices]
                if missing_tickers:
                    fut_str = ",".join(missing_tickers)
                    url_futs = f"https://iss.moex.com/iss/engines/futures/markets/forts/boards/RFUD/securities.json?securities={fut_str}&iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,LAST,HIGH,LOW,LASTCHANGEPRCNT"
                    try:
                        async with session.get(url_futs, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                for row in data.get("marketdata", {}).get("data", []):
                                    if row and row[0] and row[1] is not None:
                                        latest_prices[row[0]] = {
                                            "last": float(row[1]),
                                            "high": float(row[2]) if row[2] is not None else float(row[1]),
                                            "low": float(row[3]) if row[3] is not None else float(row[1]),
                                            "chg": float(row[4]) if row[4] is not None else 0.0,
                                            "type": "фьючерс (FORTS)"
                                        }
                    except Exception:
                        pass

                # 3. Мгновенная проверка условий
                for alert_id, chat_id, ticker, target_price, condition in active_alerts:
                    price_info = latest_prices.get(ticker)
                    if not price_info:
                        continue

                    curr_price = price_info["last"]
                    curr_high = price_info["high"]
                    curr_low = price_info["low"]

                    triggered = False
                    if condition == "GTE" and (curr_price >= target_price or curr_high >= target_price):
                        triggered = True
                    elif condition == "LTE" and (curr_price <= target_price or curr_low <= target_price):
                        triggered = True

                    if triggered:
                        mark_alert_triggered(alert_id)
                        cond_sign = "≥" if condition == "GTE" else "≤"
                        arrow = "↗ ВВЕРХ" if condition == "GTE" else "↘ ВНИЗ"
                        msg = (
                            f"⚡ <b>МОЛНИЕНОСНЫЙ АЛЕРТ: #{ticker}</b>\n\n"
                            f"🎯 <b>Цель:</b> {cond_sign} <code>{target_price:,.2f} ₽</code> ({arrow})\n"
                            f"📊 <b>Текущая цена:</b> <b>{curr_price:,.2f} ₽</b>\n"
                            f"📈 <b>Дневной High:</b> {curr_high:,.2f} ₽ | <b>Low:</b> {curr_low:,.2f} ₽\n"
                            f"📌 <i>Рынок: {price_info['type']} | Изм: {price_info['chg']:+.2f}%</i>\n\n"
                            f"🚀 <i>Проверь стакан в терминале!</i>"
                        )
                        try:
                            await bot.send_message(chat_id, msg)
                        except Exception as e:
                            logger.warning(f"Не удалось отправить алерт пользователю {chat_id}: {e}")

            except Exception as e:
                logger.error(f"Ошибка в fast_price_alerts_monitor: {e}")

            await asyncio.sleep(3)


async def background_market_monitor():
    """Фоновый периодический мониторинг рынка: авто-рассылка аномалий подписчикам каждые 5 мин."""
    logger.info("Фоновый авто-сканер рынка запущен.")
    await asyncio.sleep(15)

    while True:
        try:
            status, _ = get_market_status()
            market_is_open = ("Основная" in status or "Вечерняя" in status)

            if market_is_open:
                subscribers = get_subscribers()
                if subscribers:
                    items = await scan_market()
                    current_time = time.time()
                    for item in items:
                        t = item["ticker"]
                        ratio = item["vol_ratio"]
                        breakout = item["is_breakout_high"]

                        # Проверяем условия алерта и кулдаун 2 часа (7200 сек)
                        if (ratio >= ALERT_VOLUME_THRESHOLD or breakout) and (current_time - last_alert_times.get(t, 0) > 7200):
                            last_alert_times[t] = current_time

                            reasons = []
                            if ratio >= ALERT_VOLUME_THRESHOLD:
                                reasons.append(f"Всплеск объёма: <b>{ratio}x</b> к норме")
                            if breakout:
                                reasons.append("🚀 <b>Пробой 20-дневного максимума</b>")

                            reason_text = " • ".join(reasons)
                            broadcast_msg = (
                                f"🚨 <b>АНОМАЛЬНЫЙ СИГНАЛ РЫНКА!</b>\n\n"
                                f"Акция: <a href='https://ru.tradingview.com/symbols/MOEX-{t}/'><b>{t}</b></a>\n"
                                f"Текущая цена: <code>{item['last']:,.2f} ₽</code> ({item['chg']:+.2f}%)\n"
                                f"Сигнал: {reason_text}\n"
                                f"Оборот за день: {item['val_mln']:,.1f} млн ₽\n\n"
                                f"<i>Подробнее в /radar</i>"
                            )

                            for sub_id in subscribers:
                                try:
                                    await bot.send_message(sub_id, broadcast_msg)
                                    await asyncio.sleep(0.05)
                                except Exception as e:
                                    logger.warning(f"Не удалось разослать подписчику {sub_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка в фоновом цикле мониторинга: {e}", exc_info=True)

        await asyncio.sleep(300)


async def main():
    init_db()
    logger.info("База данных инициализирована.")
    logger.info("Запуск Telegram-бота MOEX Radar v2.0...")

    # Запуск фонового мониторинга рынка
    asyncio.create_task(fast_price_alerts_monitor())
    asyncio.create_task(background_market_monitor())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")
