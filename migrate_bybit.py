"""
Bybit Integration - Database Migration Script
Adds exchange columns and Bybit-specific fields to existing tables.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "trading.db")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("🚀 Starting Bybit integration migration...")
    
    # 1. Add columns to positions table
    print("📦 Updating positions table...")
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN exchange VARCHAR(20) DEFAULT 'upbit'")
        print("   ✓ Added exchange column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - exchange column already exists")
        else:
            raise
    
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN leverage INTEGER DEFAULT 1")
        print("   ✓ Added leverage column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - leverage column already exists")
        else:
            raise
    
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN margin_type VARCHAR(20) DEFAULT 'isolated'")
        print("   ✓ Added margin_type column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - margin_type column already exists")
        else:
            raise
    
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN liquidation_price FLOAT")
        print("   ✓ Added liquidation_price column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - liquidation_price column already exists")
        else:
            raise
    
    # 2. Add columns to trade_logs table
    print("📦 Updating trade_logs table...")
    try:
        cursor.execute("ALTER TABLE trade_logs ADD COLUMN exchange VARCHAR(20) DEFAULT 'upbit'")
        print("   ✓ Added exchange column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - exchange column already exists")
        else:
            raise
    
    try:
        cursor.execute("ALTER TABLE trade_logs ADD COLUMN leverage INTEGER")
        print("   ✓ Added leverage column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - leverage column already exists")
        else:
            raise
    
    try:
        cursor.execute("ALTER TABLE trade_logs ADD COLUMN funding_fee FLOAT")
        print("   ✓ Added funding_fee column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - funding_fee column already exists")
        else:
            raise
    
    # 3. Add columns to system_logs table
    print("📦 Updating system_logs table...")
    try:
        cursor.execute("ALTER TABLE system_logs ADD COLUMN exchange VARCHAR(20)")
        print("   ✓ Added exchange column")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("   - exchange column already exists")
        else:
            raise
    
    # 4. Add columns to user_settings table
    print("📦 Updating user_settings table...")
    bybit_columns = [
        ("bybit_api_key", "TEXT"),
        ("bybit_api_secret", "TEXT"),
        ("bybit_strategy_settings", "TEXT DEFAULT '{}'"),
        ("bybit_virtual_usdt_balance", "FLOAT DEFAULT 10000"),
        ("bybit_bot_simulation_running", "BOOLEAN DEFAULT 0"),
        ("bybit_bot_real_running", "BOOLEAN DEFAULT 0"),
        ("upbit_strategy_settings", "TEXT"),
        ("upbit_virtual_krw_balance", "FLOAT DEFAULT 10000000"),
        ("upbit_bot_simulation_running", "BOOLEAN DEFAULT 0"),
        ("upbit_bot_real_running", "BOOLEAN DEFAULT 0"),
    ]
    
    for col_name, col_type in bybit_columns:
        try:
            cursor.execute(f"ALTER TABLE user_settings ADD COLUMN {col_name} {col_type}")
            print(f"   ✓ Added {col_name} column")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"   - {col_name} column already exists")
            else:
                raise
    
    # 5. Update existing data to ensure defaults
    print("📦 Updating existing data...")
    cursor.execute("UPDATE positions SET exchange = 'upbit' WHERE exchange IS NULL")
    cursor.execute("UPDATE trade_logs SET exchange = 'upbit' WHERE exchange IS NULL")
    print("   ✓ Set default exchange to 'upbit' for existing records")
    
    conn.commit()
    conn.close()
    
    print("\n✅ Migration completed successfully!")
    print("   - positions: added exchange, leverage, margin_type, liquidation_price")
    print("   - trade_logs: added exchange, leverage, funding_fee")
    print("   - system_logs: added exchange")
    print("   - user_settings: added bybit_* columns")

if __name__ == "__main__":
    migrate()
