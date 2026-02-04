"""
SQLite Database Models and Connection
"""
import os
from datetime import datetime
from typing import Optional, Generator
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
import enum

# Import KST timezone utility
from utils.timezone import now_kst

# Database URL from environment variable (supports MySQL, PostgreSQL, SQLite)
# Examples:
#   SQLite: sqlite:///trading.db
#   MySQL: mysql+pymysql://user:password@host:3306/dbname
#   PostgreSQL: postgresql://user:password@host:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Fallback to SQLite for local development
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading.db")
    DATABASE_URL = f"sqlite:///{DB_PATH}"

# SQLAlchemy setup - SQLite needs special connect_args
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # MySQL/PostgreSQL - use connection pooling for production
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True  # Auto-reconnect on stale connections
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class TradingMode(enum.Enum):
    REAL = "real"
    SIMULATION = "simulation"


class TradeStrategy(enum.Enum):
    SQUIRREL = "squirrel"  # 상승 다람쥐
    MORNING = "morning"    # 샛별형


class TradeSide(enum.Enum):
    BUY = "buy"
    SELL = "sell"


class Timeframe(enum.Enum):
    DAY_1 = "1D"
    HOUR_4 = "4H"


# ===================
# User Models (NEW)
# ===================

class User(Base):
    """User account for multi-tenant support"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    current_token = Column(String(500), nullable=True)  # For single-session enforcement
    created_at = Column(DateTime, default=now_kst)
    
    # Relationships
    settings = relationship("UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="user", cascade="all, delete-orphan")
    trade_logs = relationship("TradeLog", back_populates="user", cascade="all, delete-orphan")
    system_logs = relationship("SystemLog", back_populates="user", cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class UserSettings(Base):
    """Per-user settings for API keys, telegram, strategies"""
    __tablename__ = "user_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # === Upbit Settings ===
    upbit_access_key = Column(Text, nullable=True)
    upbit_secret_key = Column(Text, nullable=True)
    upbit_strategy_settings = Column(Text, nullable=True, default='{"squirrel": {"enabled": true}, "morning": {"enabled": true}}')
    upbit_virtual_krw_balance = Column(Float, default=10000000)
    upbit_bot_simulation_running = Column(Boolean, default=False)
    upbit_bot_real_running = Column(Boolean, default=False)
    
    # === Bybit Settings ===
    bybit_api_key = Column(Text, nullable=True)
    bybit_api_secret = Column(Text, nullable=True)
    bybit_strategy_settings = Column(Text, nullable=True, default='{"squirrel": {"enabled": true}, "morning": {"enabled": true}}')
    bybit_virtual_usdt_balance = Column(Float, default=10000)
    bybit_bot_simulation_running = Column(Boolean, default=False)
    bybit_bot_real_running = Column(Boolean, default=False)
    
    # === Legacy fields (for backward compatibility) ===
    strategy_settings = Column(Text, nullable=True, default='{"squirrel": {"enabled": true}, "morning": {"enabled": true}}')
    virtual_krw_balance = Column(Float, default=10000000)
    bot_simulation_running = Column(Boolean, default=False)
    bot_real_running = Column(Boolean, default=False)
    
    # === Telegram Settings (shared) ===
    telegram_token = Column(Text, nullable=True)
    telegram_chat_id = Column(String(100), nullable=True)
    telegram_enabled = Column(Boolean, default=False)
    
    # === Risk Settings ===
    hard_cap_ratio = Column(Float, default=0.5)
    
    updated_at = Column(DateTime, default=now_kst, onupdate=now_kst)
    
    # Relationship
    user = relationship("User", back_populates="settings")
    
    def to_dict(self):
        return {
            "user_id": self.user_id,
            "upbit_configured": bool(self.upbit_access_key),
            "telegram_enabled": self.telegram_enabled,
            "telegram_configured": bool(self.telegram_token and self.telegram_chat_id),
            "squirrel_enabled": self.squirrel_enabled,
            "squirrel_timeframe": self.squirrel_timeframe,
            "morning_enabled": self.morning_enabled,
            "morning_timeframe": self.morning_timeframe,
            "hard_cap_ratio": self.hard_cap_ratio,
            "virtual_krw_balance": self.virtual_krw_balance
        }


# ===================
# Legacy Settings (for system-wide config)
# ===================

class Setting(Base):
    """Key-Value settings table for system configuration"""
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=now_kst, onupdate=now_kst)


# ===================
# Trade Models (Updated with user_id)
# ===================

class TradeLog(Base):
    """Trade execution logs - permanently stored"""
    __tablename__ = "trade_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    exchange = Column(String(20), nullable=False, default="upbit", index=True)  # upbit / bybit
    mode = Column(String(20), nullable=False)  # real / simulation
    strategy = Column(String(20), nullable=False)  # squirrel / morning / etc
    timeframe = Column(String(10), nullable=False)  # 1D / 4H
    coin = Column(String(20), nullable=False)  # Upbit: KRW-BTC / Bybit: BTCUSDT
    side = Column(String(10), nullable=False)  # buy / sell (Bybit: open / close)
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    pnl = Column(Float, nullable=True)  # Profit and Loss (for sell orders)
    pnl_percent = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)  # Confidence score (0.2 ~ 1.0)
    reason = Column(String(100), nullable=True)  # Trade reason
    order_id = Column(String(100), nullable=True)  # Order UUID
    # Entry price targets (for buy orders)
    stop_loss = Column(Float, nullable=True)  # Stop loss price
    take_profit = Column(Float, nullable=True)  # Take profit price (1st target)
    # Bybit specific fields
    leverage = Column(Integer, nullable=True)  # Bybit: 1-100
    funding_fee = Column(Float, nullable=True)  # Bybit: funding fee paid/received
    direction = Column(String(10), nullable=True, default="long")  # long / short (Bybit only)
    created_at = Column(DateTime, default=now_kst)
    
    # Relationship
    user = relationship("User", back_populates="trade_logs")
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "mode": self.mode,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "coin": self.coin,
            "side": self.side,
            "price": self.price,
            "quantity": self.quantity,
            "total_amount": self.total_amount,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "confidence": self.confidence,
            "reason": self.reason,
            "order_id": self.order_id,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "direction": self.direction,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class CandleSnapshot(Base):
    """Candlestick snapshot stored at trade time for historical chart viewing"""
    __tablename__ = "candle_snapshots"
    
    id = Column(Integer, primary_key=True, index=True)
    trade_log_id = Column(Integer, ForeignKey("trade_logs.id", ondelete="CASCADE"), nullable=False, index=True)
    exchange = Column(String(20), nullable=False, default="upbit")  # upbit / bybit
    coin = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)  # 1D / 4H
    candles_json = Column(Text, nullable=False)  # JSON array of {time, open, high, low, close, volume}
    indicators_json = Column(Text, nullable=True)  # JSON object {rsi: [...], ma5: [...], ma20: [...]}
    created_at = Column(DateTime, default=now_kst)
    
    # Relationship
    trade_log = relationship("TradeLog", backref="candle_snapshot")
    
    def to_dict(self):
        return {
            "id": self.id,
            "trade_log_id": self.trade_log_id,
            "exchange": self.exchange,
            "coin": self.coin,
            "timeframe": self.timeframe,
            "candles_json": self.candles_json,
            "indicators_json": self.indicators_json,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class SystemLog(Base):
    """System operation logs - auto-deleted after 24 hours"""
    __tablename__ = "system_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    exchange = Column(String(20), nullable=True, index=True)  # upbit / bybit / None (shared)
    level = Column(String(20), nullable=False)  # INFO / ERROR / WARNING
    message = Column(Text, nullable=False)
    mode = Column(String(20), nullable=True, index=True)  # simulation / real / None
    created_at = Column(DateTime, default=now_kst, index=True)
    
    # Relationship
    user = relationship("User", back_populates="system_logs")
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "level": self.level,
            "message": self.message,
            "mode": self.mode,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Position(Base):
    """Current open positions tracking"""
    __tablename__ = "positions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    exchange = Column(String(20), nullable=False, default="upbit", index=True)  # upbit / bybit
    coin = Column(String(20), nullable=False, index=True)  # Upbit: KRW-BTC / Bybit: BTCUSDT
    mode = Column(String(20), nullable=False, default="simulation")  # real / simulation
    strategy = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=True, default="1D")  # 1D / 4H
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    take_profit_2 = Column(Float, nullable=True)  # 2차 익절가 (Harmonic, LDB 등)
    reference_candle_open = Column(Float, nullable=True)  # For Squirrel strategy
    reference_candle_high = Column(Float, nullable=True)  # For Squirrel strategy
    reference_candle_low = Column(Float, nullable=True)   # For Morning Star strategy
    partial_exit_stage = Column(Integer, default=0)       # 0: no exit, 1: first partial (50%) done
    highest_price = Column(Float, nullable=True)          # Highest price since entry
    confidence = Column(Float, nullable=True)
    # Bybit specific fields
    leverage = Column(Integer, nullable=True, default=1)  # Bybit: leverage (5x fixed)
    margin_type = Column(String(20), nullable=True, default="isolated")  # isolated / cross
    liquidation_price = Column(Float, nullable=True)  # Bybit: liquidation price
    direction = Column(String(10), nullable=True, default="long")  # long / short (Bybit only)
    created_at = Column(DateTime, default=now_kst)
    
    # Relationship
    user = relationship("User", back_populates="positions")
    
    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "coin": self.coin,
            "mode": self.mode,
            "strategy": self.strategy,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "confidence": self.confidence,
            "direction": self.direction,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class PositionHistory(Base):
    """Position event history for tracking all events (entry, stop loss, take profit)"""
    __tablename__ = "position_history"
    
    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, nullable=False, index=True)  # Links to Position (may be deleted later)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    exchange = Column(String(20), nullable=False, default="upbit")
    coin = Column(String(20), nullable=False)
    mode = Column(String(20), nullable=False, default="simulation")
    strategy = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=True, default="1D")
    
    # Event details
    event_type = Column(String(30), nullable=False, index=True)  # entry, stop_loss, take_profit, trailing_stop, panic_sell
    event_price = Column(Float, nullable=False)  # Price at event
    event_quantity = Column(Float, nullable=True)  # Quantity for this event
    event_reason = Column(String(100), nullable=True)  # Reason for exit
    
    # Reference prices (for chart visualization)
    entry_price = Column(Float, nullable=True)  # Original entry price
    stop_loss_price = Column(Float, nullable=True)
    take_profit_price = Column(Float, nullable=True)
    
    # Candle snapshot at this event (optional)
    candles_json = Column(Text, nullable=True)  # 60 candles snapshot
    indicators_json = Column(Text, nullable=True)  # RSI, MA, etc.
    
    # PnL at this event
    pnl_percent = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=now_kst, index=True)
    
    def to_dict(self):
        return {
            "id": self.id,
            "position_id": self.position_id,
            "user_id": self.user_id,
            "exchange": self.exchange,
            "coin": self.coin,
            "mode": self.mode,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "event_type": self.event_type,
            "event_price": self.event_price,
            "event_quantity": self.event_quantity,
            "event_reason": self.event_reason,
            "entry_price": self.entry_price,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "pnl_percent": self.pnl_percent,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


# ===================
# Database Functions
# ===================

def init_db():
    """Initialize database and create all tables"""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Database session dependency for FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_setting(db: Session, key: str) -> Optional[str]:
    """Get a setting value by key"""
    setting = db.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting else None


def set_setting(db: Session, key: str, value: str):
    """Set or update a setting value"""
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting:
        setting.value = value
        setting.updated_at = now_kst()
    else:
        setting = Setting(key=key, value=value)
        db.add(setting)
    db.commit()
    return setting

