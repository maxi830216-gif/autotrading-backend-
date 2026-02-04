"""
Trading API Router
Whitelist, trade history, portfolio, and log streaming
"""
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
import asyncio
import json

from models.database import get_db, TradeLog, SystemLog, Position, User, UserSettings
from models.schemas import (
    WhitelistResponse, WhitelistItem,
    TradeHistoryResponse, TradeLogResponse,
    PortfolioResponse, PortfolioItem,
    SystemLogEntry
)
from services.whitelist_service import whitelist_service
from services.upbit_client import upbit_client, UpbitClient
from routers.auth import get_current_user
from utils.logger import setup_logger
import re

logger = setup_logger(__name__)

router = APIRouter()


def _add_holding_labels_to_log(message: str, user_positions_by_mode: dict) -> str:
    """
    전략 로그 메시지에서 코인명을 찾아 사용자가 보유중인 경우 모드별 라벨 추가
    예: "🎯 [샛별형] ENA(4H)56%⭐ MNT(1D)50%" -> "🎯 [샛별형] ENA(모의보유)56%⭐ MNT(1D)50%"
    
    Args:
        user_positions_by_mode: {'simulation': set(), 'real': set()} 형태
    """
    if not message.startswith("🎯"):
        return message
    
    simulation_coins = user_positions_by_mode.get('simulation', set())
    real_coins = user_positions_by_mode.get('real', set())
    
    # 패턴: 코인명(타임프레임)퍼센트 또는 코인명(타임프레임)퍼센트⭐
    # 예: ENA(4H)56%, MNT(1D)50%⭐
    pattern = r'([A-Z0-9]+)\(([14]H|1D)\)(\d+%[⭐🔻]?)'
    
    def replace_if_holding(match):
        coin_name = match.group(1)
        timeframe = match.group(2)
        rest = match.group(3)
        
        market = f"KRW-{coin_name}"
        in_sim = market in simulation_coins
        in_real = market in real_coins
        
        if in_sim and in_real:
            return f"{coin_name}(양쪽보유·{timeframe}){rest}"
        elif in_sim:
            return f"{coin_name}(모의보유·{timeframe}){rest}"
        elif in_real:
            return f"{coin_name}(실전보유·{timeframe}){rest}"
        else:
            return f"{coin_name}({timeframe}){rest}"
    
    return re.sub(pattern, replace_if_holding, message)



@router.get("/whitelist", response_model=WhitelistResponse)
async def get_whitelist(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: str = Query("simulation", description="Trading mode: simulation or real")
):
    """Get current whitelist of top 20 coins by trading volume"""
    try:
        import copy
        # Deep copy to avoid modifying cached whitelist
        whitelist_original = whitelist_service.get_whitelist()
        whitelist = copy.deepcopy(whitelist_original) if whitelist_original else []
        updated_at = whitelist_service.get_last_updated()
        
        # Reset all status to 'watching' first
        for coin in whitelist:
            coin['status'] = 'watching'
        
        # Update current prices
        if whitelist:
            markets = [coin['market'] for coin in whitelist]
            prices = UpbitClient.get_current_price(markets)
            
            for coin in whitelist:
                coin['current_price'] = prices.get(coin['market'], coin.get('current_price', 0))
        
        # Mark holdings for this user based on mode
        position_coins = set()
        
        if mode == "real":
            # Real mode: Get holdings from Upbit API
            user_settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
            if user_settings and user_settings.upbit_access_key and user_settings.upbit_secret_key:
                try:
                    from utils.encryption import encryptor
                    import pyupbit
                    access_key = encryptor.decrypt(user_settings.upbit_access_key)
                    secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                    user_upbit = pyupbit.Upbit(access_key, secret_key)
                    
                    balances = user_upbit.get_balances()
                    if balances:
                        for bal in balances:
                            if bal.get('currency') != 'KRW' and float(bal.get('balance', 0)) > 0:
                                position_coins.add(f"KRW-{bal.get('currency')}")
                except Exception as e:
                    logger.error(f"Failed to get Upbit balances for whitelist: {e}")
        else:
            # Simulation mode: Get THIS USER's Upbit positions only
            user_positions = db.query(Position).filter(
                Position.user_id == current_user.id,
                Position.mode == mode,
                Position.exchange == 'upbit'
            ).all()
            position_coins = {p.coin for p in user_positions}
        
        for coin in whitelist:
            if coin['market'] in position_coins:
                coin['status'] = 'holding'
        
        return WhitelistResponse(
            updated_at=updated_at or datetime.utcnow().isoformat(),
            coins=[WhitelistItem(**coin) for coin in whitelist]
        )
    except Exception as e:
        logger.error(f"Error getting whitelist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/whitelist/refresh")
async def refresh_whitelist(current_user: User = Depends(get_current_user)):
    """Force refresh the whitelist"""
    try:
        whitelist = whitelist_service.refresh_whitelist()
        return {
            "success": True,
            "count": len(whitelist),
            "message": "Whitelist refreshed"
        }
    except Exception as e:
        logger.error(f"Error refreshing whitelist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=TradeHistoryResponse)
async def get_trade_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: Optional[str] = Query(None, description="Filter by mode: real/simulation"),
    strategy: Optional[str] = Query(None, description="Filter by strategy: squirrel/morning"),
    coin: Optional[str] = Query(None, description="Filter by coin"),
    side: Optional[str] = Query(None, description="Filter by side: buy/sell"),
    exchange: Optional[str] = Query(None, description="Filter by exchange: upbit/bybit"),
    start_date: Optional[datetime] = Query(None, description="Filter from date"),
    end_date: Optional[datetime] = Query(None, description="Filter to date"),
    limit: int = Query(50, le=500, description="Max records to return"),
    offset: int = Query(0, description="Offset for pagination")
):
    """Get trade history for the authenticated user"""
    try:
        # Get THIS USER's trade logs only
        query = db.query(TradeLog).filter(
            TradeLog.user_id == current_user.id
        )
        
        # Apply filters
        if mode:
            query = query.filter(TradeLog.mode == mode)
        if exchange:
            query = query.filter(TradeLog.exchange == exchange)
        if strategy:
            query = query.filter(TradeLog.strategy == strategy)
        if coin:
            query = query.filter(TradeLog.coin.contains(coin.upper()))
        if side:
            # ★ side 필터를 Bybit 형식으로도 매핑
            # 매수 = buy, long_open, short_open (진입)
            # 매도 = sell, long_close, short_close (청산)
            if side == 'buy':
                query = query.filter(TradeLog.side.in_(['buy', 'long_open', 'short_open']))
            elif side == 'sell':
                query = query.filter(TradeLog.side.in_(['sell', 'long_close', 'short_close']))
            else:
                query = query.filter(TradeLog.side == side)
        if start_date:
            query = query.filter(TradeLog.created_at >= start_date)
        if end_date:
            query = query.filter(TradeLog.created_at <= end_date)
        
        # Get total count
        total = query.count()
        
        # Apply pagination and ordering
        logs = query.order_by(TradeLog.created_at.desc()).offset(offset).limit(limit).all()
        
        return TradeHistoryResponse(
            total=total,
            logs=[
                TradeLogResponse(
                    id=log.id,
                    mode=log.mode,
                    strategy=log.strategy,
                    timeframe=log.timeframe,
                    coin=log.coin,
                    side=log.side,
                    price=log.price,
                    quantity=log.quantity,
                    total_amount=log.total_amount,
                    pnl=log.pnl,
                    pnl_percent=log.pnl_percent,
                    confidence=log.confidence,
                    reason=log.reason,
                    order_id=log.order_id,
                    stop_loss=log.stop_loss,
                    take_profit=log.take_profit,
                    created_at=log.created_at.isoformat() if log.created_at else ""
                )
                for log in logs
            ]
        )
    except Exception as e:
        logger.error(f"Error getting trade history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: str = Query("simulation", description="Trading mode: simulation or real")
):
    """Get current portfolio for the authenticated user"""
    try:
        # Get user settings for virtual balance
        user_settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
        
        if mode == "real":
            # Real mode: Get actual balance from Upbit API using user's API keys
            if not user_settings or not user_settings.upbit_access_key or not user_settings.upbit_secret_key:
                return PortfolioResponse(
                    krw_balance=0,
                    total_asset_value=0,
                    today_pnl=0,
                    today_pnl_percent=0,
                    positions=[]
                )
            
            from utils.encryption import encryptor
            import pyupbit
            
            try:
                access_key = encryptor.decrypt(user_settings.upbit_access_key)
                secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
                user_upbit = pyupbit.Upbit(access_key, secret_key)
            except Exception as e:
                logger.error(f"Failed to decrypt Upbit keys: {e}")
                return PortfolioResponse(
                    krw_balance=0,
                    total_asset_value=0,
                    today_pnl=0,
                    today_pnl_percent=0,
                    positions=[]
                )
            
            krw_balance = user_upbit.get_balance("KRW")
            if krw_balance is None:
                krw_balance = 0
            
            # Get real positions from Upbit
            real_balances = user_upbit.get_balances()
            positions = []
            total_asset_value = krw_balance
            
            # Get list of KRW market tickers
            try:
                krw_tickers = pyupbit.get_tickers(fiat="KRW")  # List of 'KRW-BTC', 'KRW-ETH'...
                krw_tickers_set = set(krw_tickers) if krw_tickers else set()
            except Exception as e:
                logger.error(f"Failed to get KRW tickers: {e}")
                krw_tickers_set = set()

            if real_balances:
                # First, collect all valid coins
                valid_coins = []
                coin_data = {}  # coin -> {balance, avg_buy_price}
                
                for bal in real_balances:
                    if bal.get('currency') == 'KRW':
                        continue
                    
                    balance = float(bal.get('balance', 0))
                    if balance <= 0:
                        continue
                    
                    coin = f"KRW-{bal.get('currency')}"
                    
                    # Check if coin is in KRW market
                    if coin not in krw_tickers_set:
                        continue
                    
                    valid_coins.append(coin)
                    coin_data[coin] = {
                        'balance': balance,
                        'avg_buy_price': float(bal.get('avg_buy_price', 0))
                    }
                
                # Batch fetch all prices at once (avoids rate limiting)
                current_prices = {}
                if valid_coins:
                    try:
                        current_prices = pyupbit.get_current_price(valid_coins)
                        if current_prices is None:
                            current_prices = {}
                        elif isinstance(current_prices, (int, float)):
                            # Single coin case
                            current_prices = {valid_coins[0]: current_prices}
                    except Exception as e:
                        logger.error(f"Failed to batch fetch prices: {e}")
                        current_prices = {}
                
                # ========================================
                # Position 테이블 기반으로 개별 포지션 표시
                # AI 매수와 수동 구매를 분리 표시
                # ========================================
                user_positions = db.query(Position).filter(
                    Position.user_id == current_user.id,
                    Position.mode == "real",
                    Position.exchange == 'upbit'
                ).all()
                
                # Build position map: coin -> list of positions
                position_map = {}  # coin -> [{strategy, quantity, entry_price}, ...]
                for pos in user_positions:
                    if pos.coin not in position_map:
                        position_map[pos.coin] = []
                    position_map[pos.coin].append({
                        'strategy': pos.strategy,
                        'quantity': pos.quantity,
                        'entry_price': pos.entry_price
                    })
                
                # Now process each coin from Upbit balance
                for coin in valid_coins:
                    data = coin_data[coin]
                    upbit_balance = data['balance']
                    upbit_avg_price = data['avg_buy_price']
                    
                    current_price = current_prices.get(coin)
                    
                    # Fallback logic
                    if current_price is None or current_price == 0:
                        current_price = upbit_avg_price if upbit_avg_price > 0 else 0
                    
                    # Skip if we still can't determine a valid price
                    if current_price == 0:
                        logger.warning(f"Skipping {coin}: no valid price available")
                        continue
                    
                    # Check if we have tracked positions for this coin
                    if coin in position_map:
                        # Display each position separately
                        for pos_info in position_map[coin]:
                            qty = pos_info['quantity']
                            entry = pos_info['entry_price']
                            strategy = pos_info['strategy']
                            
                            market_value = current_price * qty
                            
                            # Skip tiny positions
                            if market_value < 1:
                                continue
                            
                            unrealized_pnl = (current_price - entry) * qty
                            unrealized_pnl_percent = ((current_price - entry) / entry * 100) if entry > 0 else 0
                            
                            # Determine source based on strategy
                            source = "manual" if strategy == "manual" else "ai"
                            can_sell = market_value >= 5000
                            
                            positions.append(PortfolioItem(
                                coin=coin,
                                balance=qty,
                                avg_buy_price=entry,
                                current_price=current_price,
                                unrealized_pnl=unrealized_pnl,
                                unrealized_pnl_percent=unrealized_pnl_percent,
                                source=source,
                                strategy=strategy,
                                can_sell=can_sell
                            ))
                            total_asset_value += market_value
                    else:
                        # No tracked position - show as manual/untracked
                        market_value = current_price * upbit_balance
                        
                        if market_value < 1:
                            continue
                        
                        unrealized_pnl = (current_price - upbit_avg_price) * upbit_balance if upbit_avg_price > 0 else 0
                        unrealized_pnl_percent = ((current_price - upbit_avg_price) / upbit_avg_price * 100) if upbit_avg_price > 0 else 0
                        
                        can_sell = market_value >= 5000
                        
                        positions.append(PortfolioItem(
                            coin=coin,
                            balance=upbit_balance,
                            avg_buy_price=upbit_avg_price,
                            current_price=current_price,
                            unrealized_pnl=unrealized_pnl,
                            unrealized_pnl_percent=unrealized_pnl_percent,
                            source="manual",
                            strategy=None,
                            can_sell=can_sell
                        ))
                        total_asset_value += market_value
            
            return PortfolioResponse(
                krw_balance=krw_balance,
                total_asset_value=total_asset_value,
                today_pnl=0,
                today_pnl_percent=0,
                positions=positions
            )
        
        else:
            # Simulation mode: Use THIS USER's virtual balance
            user_settings = db.query(UserSettings).filter(
                UserSettings.user_id == current_user.id
            ).first()
            
            if user_settings:
                virtual_balance = user_settings.virtual_krw_balance or 10000000
            else:
                virtual_balance = 10000000  # Default for new users
            
            # Get Upbit positions for THIS USER only
            positions_db = db.query(Position).filter(
                Position.user_id == current_user.id,
                Position.mode == "simulation",
                Position.exchange == 'upbit'
            ).all()
            
            # Get current prices for positions
            if positions_db:
                markets = [p.coin for p in positions_db]
                prices = UpbitClient.get_current_price(markets)
            else:
                prices = {}
            
            # Calculate portfolio items
            positions = []
            total_asset_value = virtual_balance  # Start with current KRW balance
            
            for pos in positions_db:
                current_price = prices.get(pos.coin, pos.entry_price)
                market_value = current_price * pos.quantity
                unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
                unrealized_pnl_percent = ((current_price - pos.entry_price) / pos.entry_price) * 100 if pos.entry_price > 0 else 0
                
                # All simulation positions are AI-traded
                positions.append(PortfolioItem(
                    coin=pos.coin,
                    balance=pos.quantity,
                    avg_buy_price=pos.entry_price,
                    current_price=current_price,
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pnl_percent=unrealized_pnl_percent,
                    source="ai",
                    strategy=pos.strategy  # Get strategy from Position table
                ))
                
                total_asset_value += market_value
            
            # Calculate today's realized PnL for this user
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_trades = db.query(TradeLog).filter(
                TradeLog.user_id == current_user.id,
                TradeLog.created_at >= today_start,
                TradeLog.side == "sell",
                TradeLog.pnl.isnot(None)
            ).all()
            
            today_pnl = sum(t.pnl for t in today_trades if t.pnl)
            today_pnl_percent = (today_pnl / total_asset_value * 100) if total_asset_value > 0 else 0
            
            return PortfolioResponse(
                krw_balance=virtual_balance,
                total_asset_value=total_asset_value,
                today_pnl=today_pnl,
                today_pnl_percent=today_pnl_percent,
                positions=positions
            )
    except Exception as e:
        logger.error(f"Error getting portfolio: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
async def stream_logs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Stream system logs via Server-Sent Events for the authenticated user"""
    
    async def event_generator():
        last_id = 0
        
        while True:
            try:
                # Get new logs for this user (or system-level logs) since last_id
                from sqlalchemy import or_
                new_logs = db.query(SystemLog).filter(
                    or_(SystemLog.user_id == current_user.id, SystemLog.user_id == None),
                    SystemLog.id > last_id
                ).order_by(SystemLog.id.asc()).limit(50).all()
                
                if new_logs:
                    last_id = new_logs[-1].id
                    
                    # Get THIS USER's positions by mode for "(모의보유)/(실전보유)" labels
                    user_positions = db.query(Position).filter(
                        Position.user_id == current_user.id
                    ).all()
                    positions_by_mode = {
                        'simulation': {p.coin for p in user_positions if p.mode == 'simulation'},
                        'real': {p.coin for p in user_positions if p.mode == 'real'}
                    }
                    
                    logs_data = [
                        {
                            "id": log.id,
                            "level": log.level,
                            "message": _add_holding_labels_to_log(log.message, positions_by_mode),
                            "created_at": log.created_at.isoformat() if log.created_at else ""
                        }
                        for log in new_logs
                    ]
                    
                    yield {
                        "event": "logs",
                        "data": json.dumps(logs_data)
                    }
                
                await asyncio.sleep(2)  # Poll every 2 seconds
                
            except Exception as e:
                logger.error(f"Log streaming error: {e}")
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(e)})
                }
                break
    
    return EventSourceResponse(event_generator())


@router.get("/logs/recent")
async def get_recent_logs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(100, le=500),
    mode: Optional[str] = Query(None, description="Filter by mode: simulation or real")
):
    """Get recent system logs for the authenticated user"""
    try:
        from sqlalchemy import or_
        # User's own logs + system logs (user_id=None)
        query = db.query(SystemLog).filter(
            or_(SystemLog.user_id == current_user.id, SystemLog.user_id == None),
            or_(SystemLog.exchange == "upbit", SystemLog.exchange == None)  # Upbit only
        )
        
        # Filter by mode if specified, but always include system logs (mode=None)
        if mode and mode in ["simulation", "real"]:
            query = query.filter(
                or_(SystemLog.mode == mode, SystemLog.mode == None)
            )
        
        logs = query.order_by(
            SystemLog.created_at.desc()
        ).limit(limit).all()
        
        # Get THIS USER's positions by mode for "(모의보유)/(실전보유)" labels
        user_positions = db.query(Position).filter(
            Position.user_id == current_user.id
        ).all()
        positions_by_mode = {
            'simulation': {p.coin for p in user_positions if p.mode == 'simulation'},
            'real': {p.coin for p in user_positions if p.mode == 'real'}
        }
        
        return {
            "logs": [
                {
                    "id": log.id,
                    "level": log.level,
                    "message": _add_holding_labels_to_log(log.message, positions_by_mode),
                    "mode": log.mode,
                    "created_at": log.created_at.isoformat() if log.created_at else ""
                }
                for log in logs  # Latest first (newest at top)
            ]
        }
    except Exception as e:
        logger.error(f"Error getting recent logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/returns")
async def get_period_returns(
    mode: str = Query("simulation", description="Trading mode: simulation or real"),
    days: int = Query(1, description="Period in days: 1, 7, or 30"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get returns for a specific period (realized + unrealized)"""
    from datetime import timedelta, timezone
    from sqlalchemy import func, or_
    from services.upbit_client import UpbitClient
    
    try:
        # Calculate start date
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Query realized PnL from trade logs (매도 완료된 것)
        # Query realized PnL from THIS USER's trade logs only
        result = db.query(
            func.sum(TradeLog.pnl).label("total_pnl"),
            func.count(TradeLog.id).label("trade_count")
        ).filter(
            TradeLog.user_id == current_user.id,
            TradeLog.pnl != None,
            TradeLog.created_at >= start_date,
            TradeLog.mode == mode
        ).first()
        
        realized_pnl = float(result.total_pnl) if result.total_pnl else 0
        trade_count = result.trade_count if result.trade_count else 0
        
        # Get initial investment estimate (sum of buy amounts in period)
        buy_result = db.query(
            func.sum(TradeLog.total_amount).label("total_invested")
        ).filter(
            TradeLog.user_id == current_user.id,
            TradeLog.side == "buy",
            TradeLog.created_at >= start_date,
            TradeLog.mode == mode
        ).first()
        
        total_invested = float(buy_result.total_invested) if buy_result.total_invested else 0
        
        # Calculate unrealized PnL from THIS USER's open positions only
        unrealized_pnl = 0
        open_positions = db.query(Position).filter(
            Position.user_id == current_user.id,
            Position.mode == mode
        ).all()
        
        if open_positions:
            # Get current prices for all positions
            markets = [p.coin for p in open_positions]
            current_prices = UpbitClient.get_current_price(markets)
            
            for position in open_positions:
                current_price = current_prices.get(position.coin, 0)
                if current_price > 0 and position.entry_price > 0:
                    position_pnl = (current_price - position.entry_price) * position.quantity
                    unrealized_pnl += position_pnl
        
        # Total PnL = Realized + Unrealized
        total_pnl = realized_pnl + unrealized_pnl
        
        # Calculate percentage return
        pnl_percent = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        
        return {
            "period_days": days,
            "mode": mode,
            "total_pnl": total_pnl,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "pnl_percent": pnl_percent,
            "trade_count": trade_count,
            "total_invested": total_invested
        }
    except Exception as e:
        logger.error(f"Error getting period returns: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/returns-chart")
async def get_returns_chart(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: Optional[str] = Query(None, description="Filter by mode: real/simulation"),
    strategy: Optional[str] = Query(None, description="Filter by strategy"),
):
    """Get cumulative returns chart data based on trade history"""
    from models.schemas import ReturnsChartResponse, ReturnsChartDataPoint
    
    try:
        # Query all sell trades for this user (ordered by time)
        # 최근 500건만 조회하여 성능 최적화
        query = db.query(TradeLog).filter(
            TradeLog.user_id == current_user.id,
            TradeLog.side == "sell",
            TradeLog.pnl.isnot(None),
            TradeLog.total_amount.isnot(None)
        )
        
        # Apply filters
        if mode:
            query = query.filter(TradeLog.mode == mode)
        if strategy:
            query = query.filter(TradeLog.strategy == strategy)
        
        # Order by created_at and limit to 500 for performance
        sell_trades = query.order_by(TradeLog.created_at.asc()).limit(500).all()
        
        # Calculate cumulative returns
        data_points = []
        cumulative_pnl = 0
        cumulative_invested = 0
        trade_count = 0
        
        for trade in sell_trades:
            cumulative_pnl += trade.pnl or 0
            cumulative_invested += trade.total_amount or 0
            trade_count += 1
            
            # Calculate cumulative return percentage
            # Use total_amount (sell amount) as the base for percentage calculation
            cumulative_return_percent = (cumulative_pnl / cumulative_invested * 100) if cumulative_invested > 0 else 0
            
            data_points.append(ReturnsChartDataPoint(
                timestamp=trade.created_at.isoformat() if trade.created_at else "",
                cumulative_return_percent=cumulative_return_percent,
                cumulative_pnl=cumulative_pnl,
                trade_count=trade_count
            ))
        
        # Calculate total return
        total_return_percent = (cumulative_pnl / cumulative_invested * 100) if cumulative_invested > 0 else 0
        
        return ReturnsChartResponse(
            data_points=data_points,
            total_return_percent=total_return_percent,
            total_pnl=cumulative_pnl,
            total_trades=trade_count
        )
    except Exception as e:
        logger.error(f"Error getting returns chart: {e}")
        raise HTTPException(status_code=500, detail=str(e))

