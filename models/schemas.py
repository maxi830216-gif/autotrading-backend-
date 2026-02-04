"""
Pydantic Schemas for API Request/Response
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ===================
# System Schemas
# ===================

class BotStatus(BaseModel):
    """Bot status response"""
    is_running: bool
    mode: str  # real / simulation
    uptime_seconds: Optional[int] = None
    last_check: Optional[str] = None
    whitelist_count: int = 0
    active_positions: int = 0


class ModeChangeRequest(BaseModel):
    """Mode change request"""
    mode: str = Field(..., pattern="^(real|simulation)$")


class PanicSellResponse(BaseModel):
    """Panic sell response"""
    success: bool
    message: str
    sold_positions: List[dict] = []


# ===================
# Trading Schemas
# ===================

class WhitelistItem(BaseModel):
    """Whitelist coin item"""
    market: str  # e.g., KRW-BTC
    korean_name: str
    english_name: str
    trade_volume_24h: float
    current_price: Optional[float] = None
    change_rate: Optional[float] = None
    status: str = "watching"  # watching / pending_buy / holding


class WhitelistResponse(BaseModel):
    """Whitelist response"""
    updated_at: str
    coins: List[WhitelistItem]


class TradeLogFilter(BaseModel):
    """Trade log filter parameters"""
    mode: Optional[str] = None
    strategy: Optional[str] = None
    coin: Optional[str] = None
    side: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = Field(default=50, le=500)
    offset: int = 0


class TradeLogResponse(BaseModel):
    """Single trade log entry"""
    id: int
    mode: str
    strategy: str
    timeframe: str
    coin: str
    side: str
    price: float
    quantity: float
    total_amount: float
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    order_id: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    created_at: str


class TradeHistoryResponse(BaseModel):
    """Trade history response with pagination"""
    total: int
    logs: List[TradeLogResponse]


class PortfolioItem(BaseModel):
    """Portfolio item"""
    coin: str
    balance: float
    avg_buy_price: float
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_percent: Optional[float] = None
    source: str = "manual"  # "ai" for AI trades, "manual" for direct exchange trades
    strategy: Optional[str] = None  # Strategy name if AI trade (e.g., "squirrel", "morning")
    can_sell: bool = True  # False if market_value < 5,000 KRW (Upbit minimum order)


class PortfolioResponse(BaseModel):
    """Portfolio response"""
    krw_balance: float
    total_asset_value: float
    today_pnl: float
    today_pnl_percent: float
    positions: List[PortfolioItem]


# ===================
# Settings Schemas  
# ===================

class SettingsResponse(BaseModel):
    """Settings response (masked for security)"""
    upbit_access_key: str  # Masked
    upbit_secret_key: str  # Masked
    telegram_token: str  # Masked
    telegram_chat_id: str
    is_telegram_enabled: bool
    

class SettingsUpdateRequest(BaseModel):
    """Settings update request"""
    upbit_access_key: Optional[str] = None
    upbit_secret_key: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    is_telegram_enabled: Optional[bool] = None


class TelegramTestRequest(BaseModel):
    """Telegram test message request"""
    message: str = "🔔 Upbit Trading Bot 테스트 메시지입니다."


class TelegramTestResponse(BaseModel):
    """Telegram test response"""
    success: bool
    message: str


# ===================
# Log Schemas
# ===================

class SystemLogEntry(BaseModel):
    """System log entry"""
    id: int
    level: str
    message: str
    created_at: str


class LogStreamData(BaseModel):
    """Log stream data for SSE"""
    logs: List[SystemLogEntry]


# ===================
# Returns Chart Schemas
# ===================

class ReturnsChartDataPoint(BaseModel):
    """Single data point for returns chart"""
    timestamp: str
    cumulative_return_percent: float
    cumulative_pnl: float  # 누적 수익금액 (원 또는 USDT)
    trade_count: int


class ReturnsChartResponse(BaseModel):
    """Returns chart response"""
    data_points: List[ReturnsChartDataPoint]
    total_return_percent: float
    total_pnl: float  # 총 수익금액
    total_trades: int

