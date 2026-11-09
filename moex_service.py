import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import aiohttp

from config import DEFAULT_TICKERS

# Московский часовой пояс (UTC+3)
MSK_TZ = timezone(timedelta(hours=3))


def get_market_status() -> Tuple[str, str]:
    """Возвращает текущий статус биржи MOEX и читаемую метку времени."""
    now_msk = datetime.now(MSK_TZ)
    weekday = now_msk.weekday()  # 0 - Понедельник, 6 - Воскресенье
    time_str = now_msk.strftime("%H:%M:%S")
    date_str = now_msk.strftime("%d.%m.%Y")
    current_time_minutes = now_msk.hour * 60 + now_msk.minute

    # Выходные дни
    if weekday in (5, 6):
        status = "🔴 Торги закрыты (Выходной)"
    elif 6 * 60 + 50 <= current_time_minutes < 8 * 60 + 59:
        status = "🟡 Утренняя сессия"
    elif 8 * 60 + 59 <= current_time_minutes < 9 * 60:
        status = "🟡 Аукцион открытия"
    elif 9 * 60 <= current_time_minutes < 18 * 60 + 50:
        status = "🟢 Основная сессия"
    elif 18 * 60 + 50 <= current_time_minutes < 19 * 60 + 5:
        status = "🟡 Промежуточный клиринг"
    elif 19 * 60 + 5 <= current_time_minutes < 23 * 60 + 50:
        status = "🟡 Вечерняя сессия"
    else:
        status = "🔴 Биржа закрыта"

    full_timestamp = f"{date_str} {time_str} МСК"
    return status, full_timestamp


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Рассчитывает RSI (индекс относительной силы) за указанный период."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent_deltas = deltas[-period:]

    gains = [d for d in recent_deltas if d > 0]
    losses = [-d for d in recent_deltas if d < 0]

    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0

    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 1)


class MoexDataCache:
    """Кэш исторических данных (средние объемы, 20-day high/low, RSI) с TTL 2 часа."""
    def __init__(self, ttl_seconds: int = 7200):
        self.ttl = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, ticker: str) -> Optional[Dict[str, Any]]:
        entry = self._cache.get(ticker)
        if not entry:
            return None
        if time.time() - entry["timestamp"] > self.ttl:
            del self._cache[ticker]
            return None
        return entry["data"]

    def set(self, ticker: str, data: Dict[str, Any]):
        self._cache[ticker] = {
            "timestamp": time.time(),
            "data": data
        }


# Глобальный инстанс кэша
history_cache = MoexDataCache(ttl_seconds=7200)


def format_ticker_link(ticker: str) -> str:
    """Генерирует кликабельную HTML-ссылку на TradingView."""
    return f'<a href="https://ru.tradingview.com/symbols/MOEX-{ticker}/"><b>{ticker}</b></a>'


async def fetch_ticker_history(session: aiohttp.ClientSession, ticker: str, from_date: str) -> Dict[str, Any]:
    """Получает историю и рассчитывает средний объем, 20-дн High/Low и RSI (с кэшированием)."""
    cached = history_cache.get(ticker)
    if cached:
        return cached

    url = (
        f"https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
        f"?from={from_date}&history.columns=VOLUME,CLOSE,HIGH,LOW"
    )
    headers = {"User-Agent": "MOEXRadarBot/2.0"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as res:
            if res.status != 200:
                return {}
            json_data = await res.json()
            rows = json_data.get("history", {}).get("data", [])
            if not rows:
                return {}

            vols = [r[0] for r in rows if r[0] is not None and r[0] > 0]
            closes = [r[1] for r in rows if r[1] is not None]
            highs = [r[2] for r in rows if r[2] is not None]
            lows = [r[3] for r in rows if r[3] is not None]

            avg_vol = sum(vols) / len(vols) if vols else 1.0
            high_20 = max(highs[-20:]) if len(highs) >= 5 else max(highs) if highs else 0.0
            low_20 = min(lows[-20:]) if len(lows) >= 5 else min(lows) if lows else 0.0
            rsi = calculate_rsi(closes, period=14)

            data = {
                "avg_vol": avg_vol,
                "high_20": high_20,
                "low_20": low_20,
                "rsi_14": rsi,
            }
            history_cache.set(ticker, data)
            return data
    except Exception:
        return {}


async def fetch_ticker_metrics(session: aiohttp.ClientSession, ticker: str, from_date: str) -> Optional[Dict[str, Any]]:
    """Собирает рыночные данные и сопоставляет их с историей."""
    market_url = (
        f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
        f"?iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,LAST,LASTCHANGEPRCNT,VALTODAY,VOLTODAY,HIGH,LOW"
    )
    headers = {"User-Agent": "MOEXRadarBot/2.0"}

    try:
        # Параллельно запрашиваем рыночные данные и (если нет в кэше) историю
        m_task = session.get(market_url, headers=headers, timeout=aiohttp.ClientTimeout(total=5))
        h_task = fetch_ticker_history(session, ticker, from_date)

        res_m, h_metrics = await asyncio.gather(m_task, h_task, return_exceptions=True)

        if isinstance(res_m, Exception) or res_m.status != 200:
            return None

        json_m = await res_m.json()
        m_data = json_m.get("marketdata", {}).get("data", [])
        if not m_data or len(m_data[0]) < 7:
            return None

        m = m_data[0]
        sec_id, last, chg, val, vol, high, low = m[0], m[1], m[2], m[3], m[4], m[5], m[6]

        if not last or not vol or vol <= 0:
            return None

        high = high if high is not None else last
        low = low if low is not None else last

        h_info = h_metrics if isinstance(h_metrics, dict) else {}
        avg_vol = h_info.get("avg_vol", vol)
        high_20 = h_info.get("high_20", 0.0)
        low_20 = h_info.get("low_20", 0.0)
        rsi_14 = h_info.get("rsi_14")

        vol_ratio = round(vol / avg_vol, 2) if avg_vol > 0 else 1.0

        spread = high - low
        day_range = round((spread / last) * 100, 2) if last > 0 else 0.0
        pos_in_range = round((last - low) / (spread + 1e-5) * 100, 1)
        pos_in_range = max(0.0, min(100.0, pos_in_range))

        # Детекция пробоев
        is_breakout_high = (high_20 > 0 and last >= high_20)
        is_breakout_low = (low_20 > 0 and last <= low_20)

        return {
            "ticker": sec_id,
            "last": last,
            "chg": chg if chg is not None else 0.0,
            "val_mln": round((val or 0) / 1_000_000, 1),
            "vol_ratio": vol_ratio,
            "range": day_range,
            "pos": pos_in_range,
            "high_20": high_20,
            "low_20": low_20,
            "rsi_14": rsi_14,
            "is_breakout_high": is_breakout_high,
            "is_breakout_low": is_breakout_low,
        }
    except Exception:
        return None


async def scan_market(tickers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Сканирует рынок по переданному или стандартному списку акций."""
    target_tickers = tickers or DEFAULT_TICKERS
    from_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_ticker_metrics(session, t, from_date) for t in target_tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if isinstance(r, dict) and r is not None]


async def build_radar_report(category: str = "summary", tickers: Optional[List[str]] = None) -> str:
    """Формирует расширенный структурированный отчёт по заданной категории."""
    data = await scan_market(tickers)
    if not data:
        return "⚠️ <b>Не удалось получить данные с Мосбиржи.</b>\nВозможно, биржа закрыта или сервис временно недоступен."

    status, timestamp = get_market_status()
    header = f"📊 <b>РАДАР АНОМАЛИЙ МОСБИРЖИ</b>\n{status} | <i>{timestamp}</i>\n\n"

    # 1. ОБЩИЙ СРЕЗ (SUMMARY)
    if category == "summary":
        vol_spikes = sorted(data, key=lambda x: x["vol_ratio"], reverse=True)[:3]
        squeezes = sorted(data, key=lambda x: x["range"])[:3]
        bulls = sorted(data, key=lambda x: x["pos"], reverse=True)[:3]
        bears = sorted(data, key=lambda x: x["pos"])[:3]

        text = header
        text += "🚨 <b>Топ всплесков объёма:</b>\n"
        for r in vol_spikes:
            chg_sign = "+" if r["chg"] > 0 else ""
            text += f"• {format_ticker_link(r['ticker'])}: {r['last']:,.2f} ₽ ({chg_sign}{r['chg']:.2f}%) | Объём: <b>{r['vol_ratio']}x</b> (об. {r['val_mln']:,.0f} млн)\n"

        text += "\n🗜 <b>Сжатые пружины (Мин. размах дня):</b>\n"
        for r in squeezes:
            text += f"• {format_ticker_link(r['ticker'])}: {r['last']:,.2f} ₽ | Размах: <b>{r['range']}%</b>\n"

        text += "\n🐂 <b>Сила быков (У дневных максимумов):</b>\n"
        for r in bulls:
            text += f"• {format_ticker_link(r['ticker'])}: {r['last']:,.2f} ₽ | В диапазоне: <b>{r['pos']}%</b>\n"

        text += "\n🐻 <b>Давление медведей (У дневных минимумов):</b>\n"
        for r in bears:
            text += f"• {format_ticker_link(r['ticker'])}: {r['last']:,.2f} ₽ | В диапазоне: <b>{r['pos']}%</b>\n"

        return text

    # 2. ВСЕ АНОМАЛИИ ОБЪЕМОВ
    elif category == "volumes":
        vol_spikes = sorted(data, key=lambda x: x["vol_ratio"], reverse=True)[:8]
        text = header + "🚨 <b>РЕЙТИНГ ПО ПРИТОКУ ОБЪЕМОВ (VOL RATIO):</b>\n\n"
        for i, r in enumerate(vol_spikes, 1):
            chg_sign = "+" if r["chg"] > 0 else ""
            text += (
                f"{i}. {format_ticker_link(r['ticker'])}: <code>{r['last']:,.2f} ₽</code> ({chg_sign}{r['chg']:.2f}%)\n"
                f"   ⚡ Объём: <b>{r['vol_ratio']}x</b> к норме | Оборот: <b>{r['val_mln']:,.1f} млн ₽</b>\n"
            )
        return text

    # 3. СЖАТЫЕ ПРУЖИНЫ
    elif category == "squeezes":
        squeezes = sorted(data, key=lambda x: x["range"])[:8]
        text = header + "🗜 <b>СЖАТЫЕ ПРУЖИНЫ (КОНСОЛИДАЦИЯ ПЕРЕД ПРОБОЕМ):</b>\n\n"
        for i, r in enumerate(squeezes, 1):
            text += (
                f"{i}. {format_ticker_link(r['ticker'])}: <code>{r['last']:,.2f} ₽</code>\n"
                f"   📏 Дневной размах: <b>{r['range']}%</b> | Позиция: <b>{r['pos']}%</b>\n"
            )
        return text

    # 4. БЫКИ
    elif category == "bulls":
        bulls = sorted([r for r in data if r["pos"] >= 70], key=lambda x: x["pos"], reverse=True)[:8]
        if not bulls:
            bulls = sorted(data, key=lambda x: x["pos"], reverse=True)[:5]
        text = header + "🐂 <b>СИЛА БЫКОВ (ЗАКРЫТИЕ ВБЛИЗИ ХАЯ ДНЯ):</b>\n\n"
        for i, r in enumerate(bulls, 1):
            chg_sign = "+" if r["chg"] > 0 else ""
            text += (
                f"{i}. {format_ticker_link(r['ticker'])}: <code>{r['last']:,.2f} ₽</code> ({chg_sign}{r['chg']:.2f}%)\n"
                f"   📈 В диапазоне: <b>{r['pos']}%</b> | Объем: {r['vol_ratio']}x\n"
            )
        return text

    # 5. МЕДВЕДИ
    elif category == "bears":
        bears = sorted([r for r in data if r["pos"] <= 30], key=lambda x: x["pos"])[:8]
        if not bears:
            bears = sorted(data, key=lambda x: x["pos"])[:5]
        text = header + "🐻 <b>ДАВЛЕНИЕ МЕДВЕДЕЙ (РАСПРОДАЖА К ЛОЯМ ДНЯ):</b>\n\n"
        for i, r in enumerate(bears, 1):
            chg_sign = "+" if r["chg"] > 0 else ""
            text += (
                f"{i}. {format_ticker_link(r['ticker'])}: <code>{r['last']:,.2f} ₽</code> ({chg_sign}{r['chg']:.2f}%)\n"
                f"   📉 В диапазоне: <b>{r['pos']}%</b> | Объем: {r['vol_ratio']}x\n"
            )
        return text

    # 6. ПРОБОИ И СИГНАЛЫ RSI
    elif category == "signals":
        breakouts = [r for r in data if r["is_breakout_high"] or r["is_breakout_low"]]
        overbought = [r for r in data if r["rsi_14"] and r["rsi_14"] >= 70]
        oversold = [r for r in data if r["rsi_14"] and r["rsi_14"] <= 30]

        text = header + "⚡ <b>ТЕХНИЧЕСКИЕ СИГНАЛЫ И ПРОБОИ:</b>\n\n"

        text += "🚀 <b>Пробои 20-дневных экстремумов:</b>\n"
        if breakouts:
            for r in breakouts[:5]:
                label = "🟢 20-дн. ХАЙ" if r["is_breakout_high"] else "🔴 20-дн. ЛОЙ"
                text += f"• {format_ticker_link(r['ticker'])} ({label}): <code>{r['last']:,.2f} ₽</code> (Объем {r['vol_ratio']}x)\n"
        else:
            text += "<i>На текущий момент пробоев 20-дн. уровней нет.</i>\n"

        text += "\n🔥 <b>Перекупленность (RSI ≥ 70):</b>\n"
        if overbought:
            for r in overbought[:4]:
                text += f"• {format_ticker_link(r['ticker'])}: RSI = <b>{r['rsi_14']}</b> (Цена: {r['last']:,.2f} ₽)\n"
        else:
            text += "<i>Нет бумаг с RSI ≥ 70</i>\n"

        text += "\n❄️ <b>Перепроданность (RSI ≤ 30):</b>\n"
        if oversold:
            for r in oversold[:4]:
                text += f"• {format_ticker_link(r['ticker'])}: RSI = <b>{r['rsi_14']}</b> (Цена: {r['last']:,.2f} ₽)\n"
        else:
            text += "<i>Нет бумаг с RSI ≤ 30</i>\n"

        return text

    return header + "Неизвестная категория."


async def get_top_anomalous_ticker() -> Optional[str]:
    """Возвращает тикер с максимальным всплеском объёма для построения графика."""
    data = await scan_market()
    if not data:
        return None
    top = max(data, key=lambda x: x["vol_ratio"])
    return top["ticker"]
