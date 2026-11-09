import os
from dotenv import load_dotenv

load_dotenv()

# Новый защищенный токен бота (работает как с .env, так и напрямую на хостингах без env)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9074607301:AAFTUJ5zsDQzakP7KTonQM6ag1W-YHzfm7I").strip()
if not BOT_TOKEN:
    BOT_TOKEN = "9074607301:AAFTUJ5zsDQzakP7KTonQM6ag1W-YHzfm7I"

# Расширенный пул ликвидных акций Мосбиржи (режим торгов TQBR)
DEFAULT_TICKERS = [
    'SBER', 'GAZP', 'LKOH', 'YDEX', 'ROSN', 'NVTK', 'GMKN', 'PLZL',
    'OZON', 'CHMF', 'TATN', 'ALRS', 'MOEX', 'AFLT', 'POSI', 'T',
    'VKCO', 'HEAD', 'MGNT', 'NLMK', 'MAGN', 'SNGS', 'SNGSP', 'TRNFP',
    'PIKK', 'MTSS', 'VTBR', 'CBOM', 'PHOR', 'RUAL', 'AFKS', 'RTKM',
    'UGLD', 'IRAO', 'ENPG'
]

# Параметры авто-сканера
SCAN_INTERVAL_SECONDS = 15  # Проверка каждые 5 минут
ALERT_VOLUME_THRESHOLD = 2.5  # Порог объема для push-алерта (2.5x от нормы)
