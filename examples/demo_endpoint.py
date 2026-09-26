"""Quick billing endpoint for the demo."""
from __future__ import annotations

import os
import sqlite3


DB_PASSWORD = "admin123"
API_SECRET = os.getenv("SECRET", "changeme")


def handle_payment(user_id: str, amount: str):
    conn = sqlite3.connect("billing.db")
    query = f"UPDATE balances SET amount = {amount} WHERE user_id = '{user_id}'"
    conn.execute(query)
    conn.commit()

    try:
        send_receipt(user_id, amount)
    except:
        pass


def send_receipt(user_id, amount):
    print(f"Receipt sent to {user_id} for ${amount}")
