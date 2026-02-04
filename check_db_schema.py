#!/usr/bin/env python3
"""Check database schema for user_settings table"""
import sqlite3

conn = sqlite3.connect('trading.db')
cursor = conn.cursor()

# Get user_settings schema
print("=== user_settings columns ===")
cursor.execute("PRAGMA table_info(user_settings)")
columns = cursor.fetchall()
for col in columns:
    print(f"  {col[1]}: {col[2]}")

# Get current data
print("\n=== user_settings data ===")
cursor.execute("SELECT id, user_id, upbit_access_key IS NOT NULL as has_upbit_key, bybit_api_key IS NOT NULL as has_bybit_key FROM user_settings")
rows = cursor.fetchall()
for row in rows:
    print(f"  id={row[0]}, user_id={row[1]}, has_upbit_key={row[2]}, has_bybit_key={row[3]}")

conn.close()
