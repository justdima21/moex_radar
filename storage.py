import sqlite3
from typing import List, Tuple, Optional
from datetime import datetime

DB_PATH = "moex_radar.db"


def init_db():
    """Инициализирует таблицы базы данных SQLite."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                ticker TEXT,
                target_price REAL,
                condition TEXT, -- 'GTE' (>=) или 'LTE' (<=)
                created_at TEXT,
                triggered INTEGER DEFAULT 0
            )
        """)
        conn.commit()


def add_subscriber(chat_id: int) -> bool:
    """Добавляет чат в список подписчиков на авто-алерты."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO subscribers (chat_id, created_at) VALUES (?, ?)",
                (chat_id, datetime.now().isoformat())
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def remove_subscriber(chat_id: int) -> bool:
    """Удаляет чат из подписчиков."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_subscribers() -> List[int]:
    """Возвращает список ID всех подписчиков."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM subscribers")
        return [row[0] for row in cursor.fetchall()]


def is_subscribed(chat_id: int) -> bool:
    """Проверяет, подписан ли пользователь."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,))
        return cursor.fetchone() is not None


def add_price_alert(chat_id: int, ticker: str, target_price: float, condition: str) -> int:
    """Добавляет персональный ценовой алерт."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO price_alerts (chat_id, ticker, target_price, condition, created_at, triggered)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (chat_id, ticker.upper(), target_price, condition, datetime.now().isoformat())
        )
        conn.commit()
        return cursor.lastrowid


def get_active_price_alerts() -> List[Tuple[int, int, str, float, str]]:
    """Возвращает активные алерты: [(id, chat_id, ticker, target_price, condition), ...]"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, chat_id, ticker, target_price, condition
            FROM price_alerts
            WHERE triggered = 0
        """)
        return cursor.fetchall()


def mark_alert_triggered(alert_id: int):
    """Отмечает алерт как сработавший."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE price_alerts SET triggered = 1 WHERE id = ?", (alert_id,))
        conn.commit()


def get_user_alerts(chat_id: int) -> List[Tuple[int, str, float, str, int]]:
    """Возвращает список алертов конкретного пользователя."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, ticker, target_price, condition, triggered
            FROM price_alerts
            WHERE chat_id = ?
            ORDER BY id DESC
        """, (chat_id,))
        return cursor.fetchall()


def delete_price_alert(alert_id: int, chat_id: int) -> bool:
    """Удаляет конкретный алерт пользователя."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM price_alerts WHERE id = ? AND chat_id = ?", (alert_id, chat_id))
        conn.commit()
        return cursor.rowcount > 0
