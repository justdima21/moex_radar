import io
from datetime import datetime, timedelta
from typing import Optional
import aiohttp
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


async def generate_ticker_chart(ticker: str) -> Optional[io.BytesIO]:
    """
    Скачивает реальные японские свечи (OHLCV) с MOEX через шлюз candles.json,
    включая сегодняшние онлайн-котировки в реальном времени, и рисует профессиональный
    график в стиле TradingView.
    """
    ticker = ticker.upper().strip()
    from_date = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
    
    # 1. Запрос реальных дневных свечей (интервал 24 = день, включает текущий торговый день!)
    url_candles = (
        f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}/candles.json"
        f"?from={from_date}&interval=24&iss.meta=off"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url_candles, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        candle_rows = data.get("candles", {}).get("data", [])
        if not candle_rows or len(candle_rows) < 3:
            return None

        # Парсим свечи: open, close, high, low, value, volume, begin, end
        dates = []
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []

        for row in candle_rows:
            o, c, h, l, val, vol, begin_str = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
            if c is None or o is None or h is None or l is None:
                continue
            dt = datetime.strptime(begin_str.split()[0], "%Y-%m-%d")
            dates.append(dt)
            opens.append(float(o))
            closes.append(float(c))
            highs.append(float(h))
            lows.append(float(l))
            # Оборот в миллионах рублей
            volumes.append(float(val) / 1_000_000 if val is not None else 0.0)

        if len(dates) < 2:
            return None

        # 2. Построение графика в стиле TradingView (Dark)
        plt.style.use('dark_background')
        fig, (ax_price, ax_vol) = plt.subplots(
            2, 1, figsize=(9.5, 6), dpi=140, sharex=True,
            gridspec_kw={'height_ratios': [3.2, 1], 'hspace': 0.05}
        )
        
        bg_color = '#0e1117'
        card_color = '#161b22'
        fig.patch.set_facecolor(bg_color)
        ax_price.set_facecolor(card_color)
        ax_vol.set_facecolor(card_color)

        # Переводим даты в числа для matplotlib
        num_dates = mdates.date2num(dates)
        width = 0.6  # ширина тела свечи в днях

        # Рисуем японские свечи
        up_color = '#00c087'    # Бычья свеча (зелёная)
        down_color = '#ff3b30'  # Медвежья свеча (красная)

        for i in range(len(dates)):
            x = num_dates[i]
            o = opens[i]
            c = closes[i]
            h = highs[i]
            l = lows[i]

            is_up = c >= o
            color = up_color if is_up else down_color

            # Тени свечи (High - Low)
            ax_price.vlines(x, l, h, color=color, linewidth=1.2, zorder=2)

            # Тело свечи (Open - Close)
            body_bottom = min(o, c)
            body_height = max(abs(c - o), (h - l) * 0.015)  # минимальная высота для доджи
            rect = Rectangle((x - width / 2, body_bottom), width, body_height,
                             facecolor=color, edgecolor=color, zorder=3)
            ax_price.add_patch(rect)

        # Скользящая средняя SMA 9
        if len(closes) >= 9:
            sma9 = [sum(closes[max(0, i-8):i+1]) / len(closes[max(0, i-8):i+1]) for i in range(len(closes))]
            ax_price.plot(dates, sma9, color='#e3b341', linewidth=1.3, linestyle='--', label='SMA 9', zorder=4)

        # Последняя живая цена
        last_price = closes[-1]
        prev_close = closes[-2] if len(closes) > 1 else last_price
        change_pct = ((last_price - prev_close) / prev_close) * 100 if prev_close else 0.0
        badge_color = up_color if change_pct >= 0 else down_color

        # Линия текущей цены
        ax_price.axhline(last_price, color=badge_color, linestyle=':', alpha=0.8, linewidth=1.2)
        
        # Бейдж с живой ценой справа
        ax_price.text(
            1.01, last_price, f"{last_price:,.2f} ₽",
            transform=ax_price.get_yaxis_transform(),
            color="#ffffff", fontweight="bold", fontsize=10,
            va="center", ha="left",
            bbox=dict(boxstyle="square,pad=0.3", fc=badge_color, ec="none")
        )

        # Заголовок
        chg_sign = "+" if change_pct >= 0 else ""
        title_text = f"MOEX: {ticker}  |  {last_price:,.2f} ₽  ({chg_sign}{change_pct:.2f}%)"
        ax_price.set_title(title_text, color='#f0f6fc', fontsize=13, fontweight='bold', pad=12, loc='left')

        ax_price.grid(True, linestyle='--', alpha=0.18, color='#8b949e')
        ax_price.tick_params(colors='#8b949e', labelsize=9)
        if len(closes) >= 9:
            ax_price.legend(loc='upper left', framealpha=0.3, fontsize=8)

        # 3. Нижний график: Объёмы (млн руб)
        vol_colors = [up_color if closes[i] >= opens[i] else down_color for i in range(len(dates))]
        ax_vol.bar(num_dates, volumes, color=vol_colors, width=width, alpha=0.85, zorder=2)
        ax_vol.set_ylabel("Объём (млн ₽)", color='#8b949e', fontsize=8)
        ax_vol.grid(True, linestyle='--', alpha=0.18, color='#8b949e')
        ax_vol.tick_params(colors='#8b949e', labelsize=8)

        # Форматирование оси X (Даты)
        ax_vol.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        ax_vol.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(dates)//10)))
        fig.autofmt_xdate(rotation=0, ha='center')

        # Отступы графика
        fig.subplots_adjust(left=0.08, right=0.88, top=0.92, bottom=0.10)

        # Сохранение в буфер памяти
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Ошибка построения графика для {ticker}: {e}")
        return None
