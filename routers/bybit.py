"""
Bybit Trading API Router
Handles all Bybit-related API endpoints for futures trading
"""
from typing import Optional, List
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sse_starlette.sse import EventSourceResponse
import asyncio
import json
import re

from models.database import get_db, TradeLog, SystemLog, Position, User, UserSettings
from services.bybit_client import bybit_client, BybitClient
from services.bybit_whitelist import bybit_whitelist_service
from services.bybit_order_manager import bybit_order_manager
from routers.auth import get_current_user
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(prefix="/bybit", tags=["Bybit"])


def _add_holding_labels_to_log(message: str, user_positions_by_mode: dict) -> str:
    """
    전략 로그 메시지에서 코인명을 찾아 사용자가 보유중인 경우 모드별 라벨 추가
    예: "🎯 [샛별형] BTC(4H)56%⭐ ETH(1D)50%" -> "🎯 [샛별형] BTC(모의보유)56%⭐ ETH(1D)50%"
    
    Args:
        user_positions_by_mode: {'simulation': set(), 'real': set()} 형태
    """
    if not message.startswith("🎯"):
        return message
    
    simulation_symbols = user_positions_by_mode.get('simulation', set())
    real_symbols = user_positions_by_mode.get('real', set())
    
    # 패턴: 코인명(타임프레임)퍼센트 또는 코인명(타임프레임)퍼센트⭐/🔻
    # 예: BTC(4H)56%, ETH(1D)50%⭐
    pattern = r'([A-Z0-9]+)\(([14]H|1D)\)(\d+%[⭐🔻]?)'
    
    def replace_if_holding(match):
        coin_name = match.group(1)
        timeframe = match.group(2)
        rest = match.group(3)
        
        symbol = f"{coin_name}USDT"
        in_sim = symbol in simulation_symbols
        in_real = symbol in real_symbols
        
        if in_sim and in_real:
            return f"{coin_name}(양쪽보유·{timeframe}){rest}"
        elif in_sim:
            return f"{coin_name}(모의보유·{timeframe}){rest}"
        elif in_real:
            return f"{coin_name}(실전보유·{timeframe}){rest}"
        else:
            return f"{coin_name}({timeframe}){rest}"
    
    return re.sub(pattern, replace_if_holding, message)



# ==================
# Whitelist
# ==================

@router.get("/whitelist")
async def get_bybit_whitelist(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: str = Query("simulation", description="Trading mode")
):
    """Get Bybit futures whitelist (top 30 by market cap)"""
    try:
        # Refresh prices - returns (whitelist, added, removed)
        whitelist, _, _ = bybit_whitelist_service.refresh_prices()
        
        # Get user's positions to mark holdings
        positions = db.query(Position).filter(
            Position.user_id == current_user.id,
            Position.exchange == "bybit",
            Position.mode == mode
        ).all()
        
        position_symbols = {p.coin for p in positions}
        
        # Mark holdings
        for coin in whitelist:
            coin['status'] = 'holding' if coin['symbol'] in position_symbols else 'watching'
        
        return {
            "updated_at": bybit_whitelist_service.get_last_updated(),
            "coins": whitelist
        }
    except Exception as e:
        logger.error(f"Error getting Bybit whitelist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# Portfolio
# ==================

@router.get("/portfolio")
async def get_bybit_portfolio(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: str = Query("simulation", description="Trading mode")
):
    """Get Bybit portfolio with positions and balance"""
    try:
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        # Initialize response variables
        positions = []
        total_unrealized_pnl = 0
        total_position_value = 0
        total_equity = 0  # For real mode

        if mode == "simulation":
            usdt_balance = user_settings.bybit_virtual_usdt_balance if user_settings else 10000
            
            # Get user's positions
            positions_db = db.query(Position).filter(
                Position.user_id == current_user.id,
                Position.exchange == "bybit",
                Position.mode == mode
            ).all()
            
            # Get current prices
            if positions_db:
                symbols = [p.coin for p in positions_db]
                prices = BybitClient.get_current_price(symbols)
            else:
                prices = {}
                
            for pos in positions_db:
                current_price = prices.get(pos.coin, pos.entry_price)
                position_value = current_price * pos.quantity
                margin_used = pos.entry_price * pos.quantity / pos.leverage
                
                # Calculate PnL based on direction
                is_short = pos.direction == 'short'
                if is_short:
                    # Short: profit when price goes down
                    unrealized_pnl = (pos.entry_price - current_price) * pos.quantity
                    unrealized_pnl_percent = ((pos.entry_price - current_price) / pos.entry_price) * 100 * pos.leverage
                else:
                    # Long: profit when price goes up
                    unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
                    unrealized_pnl_percent = ((current_price - pos.entry_price) / pos.entry_price) * 100 * pos.leverage
                
                # Calculate liquidation price if not stored
                # For isolated margin long: liq_price = entry * (1 - 1/leverage + maintenance_rate)
                # For short: liq_price = entry * (1 + 1/leverage - maintenance_rate)
                liquidation_price = pos.liquidation_price
                if liquidation_price is None and pos.leverage:
                    if is_short:
                        liquidation_price = pos.entry_price * (1 + 1/pos.leverage - 0.005)
                    else:
                        liquidation_price = pos.entry_price * (1 - 1/pos.leverage + 0.005)
                
                # Calculate total buy amount
                total_buy_amount = pos.entry_price * pos.quantity
                
                positions.append({
                    "id": pos.id,
                    "symbol": pos.coin,
                    "side": "Short" if is_short else "Long",
                    "direction": pos.direction or "long",
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "current_price": current_price,
                    "leverage": pos.leverage,
                    "margin_used": margin_used,
                    "position_value": position_value,
                    "total_buy_amount": total_buy_amount,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_percent": unrealized_pnl_percent,
                    "liquidation_price": liquidation_price,
                    "strategy": pos.strategy,
                    "timeframe": pos.timeframe,
                    "created_at": pos.created_at.isoformat() if pos.created_at else None
                })
                
                total_unrealized_pnl += unrealized_pnl
                total_position_value += total_buy_amount  # Use actual buy amount, not margin
        
        else:
            # Real mode: get from Bybit API
            usdt_balance = 0
            if user_settings and user_settings.bybit_api_key:
                try:
                    from utils.encryption import encryptor
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    # 1. Balance - get all wallet info
                    balance = bybit_client.get_wallet_balance()
                    usdt_balance = balance.get('available', 0)  # Available cash (not in positions)
                    total_equity = balance.get('equity', 0)  # Total equity
                    invested_margin = balance.get('total', 0)  # Actual invested margin (walletBalance)
                    api_unrealized_pnl = balance.get('unrealized_pnl', 0)  # Unrealized PnL from API
                    
                    # 2. Get AI-tracked positions from DB
                    db_positions = db.query(Position).filter(
                        Position.user_id == current_user.id,
                        Position.exchange == 'bybit',
                        Position.mode == 'real'
                    ).all()
                    
                    # Build map: symbol -> position info
                    position_map = {}
                    for pos in db_positions:
                        if pos.coin not in position_map:
                            position_map[pos.coin] = []
                        position_map[pos.coin].append({
                            'id': pos.id,
                            'strategy': pos.strategy,
                            'quantity': pos.quantity,
                            'entry_price': pos.entry_price,
                            'timeframe': pos.timeframe,
                            'confidence': pos.confidence,
                            'direction': pos.direction or 'long'  # ★ direction 추가
                        })
                    
                    # 3. Positions from Bybit API
                    api_positions = bybit_client.get_positions()
                    
                    # 4. Prices
                    if api_positions:
                        symbols = [p['symbol'] for p in api_positions]
                        prices = BybitClient.get_current_price(symbols)
                    else:
                        prices = {}
                        
                    for p in api_positions:
                        symbol = p['symbol']
                        size = float(p['size'])
                        if size == 0: continue
                        
                        entry_price = float(p['entryPrice'])
                        leverage = float(p['leverage']) if p.get('leverage') else 5  # Default to 5x if empty
                        unrealized_pnl = float(p['unrealisedPnl'])
                        side = "Long" if p['side'] == "Buy" else "Short"
                        
                        current_price = prices.get(symbol, entry_price)
                        position_value = current_price * size
                        margin_used = (entry_price * size) / leverage
                        
                        # Calculate PnL percent
                        pnl_percent = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0
                        
                        # Check if this position is AI-tracked
                        if symbol in position_map:
                            # AI-tracked position
                            for pos_info in position_map[symbol]:
                                positions.append({
                                    "id": pos_info['id'],
                                    "symbol": symbol,
                                    "side": side,
                                    "direction": pos_info.get('direction', 'long'),  # ★ direction 추가
                                    "quantity": pos_info['quantity'],
                                    "entry_price": pos_info['entry_price'],
                                    "current_price": current_price,
                                    "leverage": leverage,
                                    "margin_used": margin_used,
                                    "position_value": position_value,
                                    "total_buy_amount": pos_info['entry_price'] * pos_info['quantity'],
                                    "unrealized_pnl": unrealized_pnl,
                                    "unrealized_pnl_percent": pnl_percent,
                                    "liquidation_price": float(p.get('liqPrice', 0)) if p.get('liqPrice') else None,
                                    "strategy": pos_info['strategy'],
                                    "timeframe": pos_info['timeframe'],
                                    "source": "ai",
                                    "created_at": datetime.now().isoformat()
                                })
                        else:
                            # Manual position (not AI-tracked)
                            # ★ Bybit API side: Buy=Long, Sell=Short
                            direction = 'short' if p['side'] == 'Sell' else 'long'
                            positions.append({
                                "id": int(datetime.now().timestamp() * 1000) + int(size * 100),
                                "symbol": symbol,
                                "side": side,
                                "direction": direction,  # ★ direction 추가
                                "quantity": size,
                                "entry_price": entry_price,
                                "current_price": current_price,
                                "leverage": leverage,
                                "margin_used": margin_used,
                                "position_value": position_value,
                                "total_buy_amount": entry_price * size,
                                "unrealized_pnl": unrealized_pnl,
                                "unrealized_pnl_percent": pnl_percent,
                                "liquidation_price": float(p.get('liqPrice', 0)) if p.get('liqPrice') else None,
                                "strategy": "manual",
                                "timeframe": "-",
                                "source": "manual",
                                "created_at": datetime.now().isoformat()
                            })
                        
                        total_unrealized_pnl += unrealized_pnl
                        total_position_value += margin_used
                        
                except Exception as e:
                    logger.error(f"Error fetching Bybit real portfolio: {e}")
        
        # Calculate total asset value
        if mode == "simulation":
            total_asset_value = usdt_balance + total_position_value + total_unrealized_pnl
        else:
            # Real mode: use Bybit's equity directly (already includes wallet + unrealized PnL)
            total_asset_value = total_equity if total_equity > 0 else usdt_balance
            # total_position_value는 이미 루프에서 margin_used를 누적했으므로 그대로 사용
            # total_unrealized_pnl도 이미 루프에서 누적했으므로 그대로 사용
        
        return {
            "usdt_balance": usdt_balance,  # Available cash (for real mode) or virtual balance (for simulation)
            "total_asset_value": total_asset_value,
            "total_position_value": total_position_value,  # Invested margin (without leverage)
            "total_unrealized_pnl": total_unrealized_pnl,
            "positions": positions
        }
    except Exception as e:
        logger.error(f"Error getting Bybit portfolio: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# Trading History
# ==================

@router.get("/history")
async def get_bybit_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    side: Optional[str] = Query(None),  # ★ side 필터 추가
    limit: int = Query(50, le=500),
    offset: int = Query(0)
):
    """Get Bybit trading history"""
    try:
        query = db.query(TradeLog).filter(
            TradeLog.user_id == current_user.id,
            TradeLog.exchange == "bybit"
        )
        
        if mode:
            query = query.filter(TradeLog.mode == mode)
        if strategy:
            query = query.filter(TradeLog.strategy == strategy)
        # ★ side 필터를 Bybit 형식으로 매핑
        if side:
            if side == 'buy':
                query = query.filter(TradeLog.side.in_(['buy', 'long_open', 'short_open']))
            elif side == 'sell':
                query = query.filter(TradeLog.side.in_(['sell', 'long_close', 'short_close']))
            else:
                query = query.filter(TradeLog.side == side)
        
        total = query.count()
        logs = query.order_by(TradeLog.created_at.desc()).offset(offset).limit(limit).all()
        
        return {
            "total": total,
            "logs": [
                {
                    "id": log.id,
                    "mode": log.mode,
                    "strategy": log.strategy,
                    "timeframe": log.timeframe,
                    "symbol": log.coin,
                    "side": log.side,
                    "price": log.price,
                    "quantity": log.quantity,
                    "total_amount": log.total_amount,
                    "pnl": log.pnl,
                    "pnl_percent": log.pnl_percent,
                    "confidence": log.confidence,
                    "leverage": log.leverage,
                    "funding_fee": log.funding_fee,
                    "reason": log.reason,
                    "stop_loss": log.stop_loss,
                    "take_profit": log.take_profit,
                    "created_at": log.created_at.isoformat() if log.created_at else ""
                }
                for log in logs
            ]
        }
    except Exception as e:
        logger.error(f"Error getting Bybit history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# System Logs
# ==================

@router.get("/logs")
async def stream_bybit_logs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Stream Bybit system logs via Server-Sent Events"""
    
    async def event_generator():
        last_id = 0
        
        while True:
            try:
                # Get new logs since last_id
                new_logs = db.query(SystemLog).filter(
                    or_(SystemLog.user_id == current_user.id, SystemLog.user_id == None),
                    SystemLog.exchange == "bybit",
                    SystemLog.id > last_id
                ).order_by(SystemLog.id.asc()).limit(50).all()
                
                if new_logs:
                    last_id = new_logs[-1].id
                    
                    # Get user's positions by mode for "(모의보유)/(실전보유)" labels
                    user_positions = db.query(Position).filter(
                        Position.user_id == current_user.id,
                        Position.exchange == "bybit"
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
                            "mode": log.mode,
                            "created_at": log.created_at.isoformat() if log.created_at else ""
                        }
                        for log in new_logs
                    ]
                    
                    yield {
                        "event": "logs",
                        "data": json.dumps(logs_data)
                    }
                
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Bybit log streaming error: {e}")
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(e)})
                }
                break
    
    return EventSourceResponse(event_generator())


@router.get("/logs/recent")
async def get_bybit_logs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(100, le=500),
    mode: Optional[str] = Query(None)
):
    """Get recent Bybit system logs"""
    try:
        # User's own logs + system logs (user_id=None)
        query = db.query(SystemLog).filter(
            or_(SystemLog.user_id == current_user.id, SystemLog.user_id == None),
            SystemLog.exchange == "bybit"  # Bybit only - no NULL
        )
        
        if mode:
            query = query.filter(or_(SystemLog.mode == mode, SystemLog.mode == None))
        
        logs = query.order_by(SystemLog.created_at.desc()).limit(limit).all()
        
        # Get user's positions by mode for "(모의보유)/(실전보유)" labels
        user_positions = db.query(Position).filter(
            Position.user_id == current_user.id,
            Position.exchange == "bybit"
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
                for log in logs  # Latest first (descending order)
            ]
        }
    except Exception as e:
        logger.error(f"Error getting Bybit logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# Period Returns
# ==================

@router.get("/returns")
async def get_bybit_period_returns(
    mode: str = Query("simulation", description="Trading mode: simulation or real"),
    days: int = Query(1, description="Period in days: 1, 7, or 30"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get returns for a specific period (realized + unrealized) for Bybit"""
    from datetime import timedelta, timezone
    from sqlalchemy import func
    
    try:
        # Calculate start date
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Query realized PnL from Bybit trade logs
        result = db.query(
            func.sum(TradeLog.pnl).label("total_pnl"),
            func.count(TradeLog.id).label("trade_count")
        ).filter(
            TradeLog.user_id == current_user.id,
            TradeLog.exchange == 'bybit',
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
            TradeLog.exchange == 'bybit',
            TradeLog.side == "buy",
            TradeLog.created_at >= start_date,
            TradeLog.mode == mode
        ).first()
        
        total_invested = float(buy_result.total_invested) if buy_result.total_invested else 0
        
        # Calculate unrealized PnL from open positions
        unrealized_pnl = 0
        total_margin_used = 0
        initial_balance = 10000  # Default
        
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if mode == "real":
            # Real mode: Get unrealized PnL directly from Bybit API
            if user_settings and user_settings.bybit_api_key:
                try:
                    from utils.encryption import encryptor
                    api_key = encryptor.decrypt(user_settings.bybit_api_key)
                    api_secret = encryptor.decrypt(user_settings.bybit_api_secret)
                    bybit_client.set_credentials(api_key, api_secret)
                    
                    # Get wallet balance for initial balance calculation
                    wallet = bybit_client.get_wallet_balance()
                    wallet_balance = wallet.get('total', 0)
                    
                    # Get positions from Bybit API
                    api_positions = bybit_client.get_positions()
                    
                    for p in api_positions:
                        size = float(p.get('size', 0))
                        if size > 0:
                            unrealized_pnl += float(p.get('unrealisedPnl', 0))
                            entry_price = float(p.get('entryPrice', 0))
                            leverage = float(p.get('leverage', 5))
                            margin = (entry_price * size) / leverage
                            total_margin_used += margin
                    
                    # For real mode, use wallet balance + margin used as base
                    initial_balance = wallet_balance + total_margin_used
                    
                except Exception as e:
                    logger.error(f"Error fetching Bybit real positions for returns: {e}")
                    # Fallback to DB positions
                    initial_balance = 0
            else:
                initial_balance = 0
        else:
            # Simulation mode: Get from DB positions
            initial_balance = user_settings.bybit_virtual_usdt_balance if user_settings else 10000
            
            open_positions = db.query(Position).filter(
                Position.user_id == current_user.id,
                Position.exchange == 'bybit',
                Position.mode == mode,
                Position.quantity > 0
            ).all()
            
            if open_positions:
                symbols = [p.coin for p in open_positions]
                prices = BybitClient.get_current_price(symbols)
                
                for position in open_positions:
                    try:
                        current_price = prices.get(position.coin, position.entry_price)
                        if current_price > 0 and position.entry_price > 0:
                            # 숏/롱에 따라 다르게 계산
                            if position.direction == 'short':
                                # 숏: 가격 하락 시 수익
                                position_pnl = (position.entry_price - current_price) * position.quantity
                            else:
                                # 롱: 가격 상승 시 수익
                                position_pnl = (current_price - position.entry_price) * position.quantity
                            unrealized_pnl += position_pnl
                            
                            leverage = position.leverage or 5
                            margin = (position.entry_price * position.quantity) / leverage
                            total_margin_used += margin
                    except:
                        pass
        
        # Total PnL = Realized + Unrealized
        total_pnl = realized_pnl + unrealized_pnl
        
        # Calculate percentage return
        if total_invested == 0:
            # No trades in period - use initial balance
            pnl_percent = (total_pnl / initial_balance * 100) if initial_balance > 0 else 0
        else:
            # For period returns, calculate based on average margin used
            avg_leverage = 5  # Default leverage
            margin_invested = total_invested / avg_leverage
            pnl_percent = (total_pnl / margin_invested * 100) if margin_invested > 0 else 0
        
        return {
            "period_days": days,
            "mode": mode,
            "total_pnl": total_pnl,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "pnl_percent": pnl_percent,
            "trade_count": trade_count,
            "total_invested": total_invested,
            "initial_balance": initial_balance  # Added for debugging
        }
    except Exception as e:
        logger.error(f"Error getting Bybit period returns: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# Manual Trading
# ==================

@router.post("/order/open")
async def open_long_position(
    symbol: str,
    mode: str = Query("simulation"),
    current_user: User = Depends(get_current_user)
):
    """Manually open a long position"""
    try:
        if not bybit_whitelist_service.is_valid_symbol(symbol):
            raise HTTPException(status_code=400, detail="Invalid symbol")
        
        result = bybit_order_manager.open_long(
            symbol=symbol,
            mode=mode,
            user_id=current_user.id,
            strategy="manual",
            timeframe="manual",
            confidence=1.0
        )
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result.get('reason', 'Order failed'))
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error opening position: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/order/close/{position_id}")
async def close_position(
    position_id: int,
    reason: str = Query("수동 청산"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Close a position"""
    try:
        # Verify ownership
        position = db.query(Position).filter(
            Position.id == position_id,
            Position.user_id == current_user.id,
            Position.exchange == "bybit"
        ).first()
        
        if not position:
            raise HTTPException(status_code=404, detail="Position not found")
        
        result = bybit_order_manager.close_long(position_id, reason)
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result.get('reason', 'Close failed'))
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing position: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# Settings
# ==================

@router.get("/settings")
async def get_bybit_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get Bybit-specific settings"""
    # Default strategy settings including short strategies (★ Phase 9: min_confidence 제거)
    default_strategy_settings = {
        # Long strategies
        "squirrel": {"name": "다람쥐", "enabled": True},
        "morning": {"name": "샛별형", "enabled": True},
        "inverted_hammer": {"name": "윗꼬리양봉", "enabled": True},
        "divergence": {"name": "다이버전스", "enabled": True},
        "harmonic": {"name": "하모닉", "enabled": True},
        "leading_diagonal": {"name": "리딩다이아고날", "enabled": True},
        # Short strategies
        "bearish_divergence": {"name": "하락다이버전스", "enabled": False, "direction": "short"},
        "evening_star": {"name": "석양형", "enabled": False, "direction": "short"},
        "shooting_star": {"name": "유성형", "enabled": False, "direction": "short"},
        "bearish_engulfing": {"name": "하락장악형", "enabled": False, "direction": "short"},
        "leading_diagonal_breakdown": {"name": "리딩다이아이탈", "enabled": False, "direction": "short"},
    }
    
    user_settings = db.query(UserSettings).filter(
        UserSettings.user_id == current_user.id
    ).first()
    
    if not user_settings:
        return {
            "api_configured": False,
            "strategy_settings": default_strategy_settings,
            "virtual_balance": 10000,
            "leverage": 5
        }
    
    import json
    strategy_settings = default_strategy_settings.copy()
    if user_settings.bybit_strategy_settings:
        try:
            user_strategy = json.loads(user_settings.bybit_strategy_settings)
            # Merge user settings with defaults
            for key, value in user_strategy.items():
                if key in strategy_settings:
                    strategy_settings[key].update(value)
                else:
                    strategy_settings[key] = value
        except:
            pass
    
    return {
        "api_configured": bool(user_settings.bybit_api_key),
        "strategy_settings": strategy_settings,
        "virtual_balance": user_settings.bybit_virtual_usdt_balance or 10000,
        "leverage": 5  # Fixed
    }



class BybitApiKeysRequest(BaseModel):
    api_key: str
    api_secret: str

@router.put("/settings/api")
async def update_bybit_api_keys(
    keys: BybitApiKeysRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update Bybit API keys with validation"""
    try:
        # Validate API Keys by fetching wallet balance
        try:
            from pybit.unified_trading import HTTP
            session = HTTP(
                testnet=False,
                api_key=keys.api_key,
                api_secret=keys.api_secret
            )
            # Try to fetch wallet balance to verify keys
            response = session.get_wallet_balance(accountType="UNIFIED")
            if response.get("retCode") != 0:
                raise Exception(f"Validation failed: {response.get('retMsg')}")
                
        except Exception as e:
            logger.error(f"Bybit API validation failed: {str(e)}")
            raise HTTPException(status_code=400, detail=f"API 키 검증 실패: 유효하지 않은 키입니다. ({str(e)})")

        from utils.encryption import encryptor
        
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if not user_settings:
            user_settings = UserSettings(user_id=current_user.id)
            db.add(user_settings)
        
        user_settings.bybit_api_key = encryptor.encrypt(keys.api_key)
        user_settings.bybit_api_secret = encryptor.encrypt(keys.api_secret)
        
        db.commit()
        
        return {"success": True, "message": "Bybit API keys validated and updated"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating Bybit API keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings/strategy")
async def update_bybit_strategy_settings(
    settings: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update Bybit strategy settings"""
    try:
        import json
        
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if not user_settings:
            user_settings = UserSettings(user_id=current_user.id)
            db.add(user_settings)
        
        user_settings.bybit_strategy_settings = json.dumps(settings)
        db.commit()
        
        return {"success": True, "message": "Bybit strategy settings updated"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating Bybit strategy settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# Bot Control
# ==================

@router.post("/bot/start")
async def start_bybit_bot(
    current_user: User = Depends(get_current_user),
    mode: str = Query("simulation", description="Trading mode: simulation or real")
):
    """Start Bybit trading bot"""
    try:
        from services.bybit_scheduler import bybit_scheduler_service
        result = bybit_scheduler_service.start_bot(mode, current_user.id)
        return result
    except Exception as e:
        logger.error(f"Error starting Bybit bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bot/stop")
async def stop_bybit_bot(
    current_user: User = Depends(get_current_user),
    mode: str = Query("simulation", description="Trading mode: simulation or real")
):
    """Stop Bybit trading bot"""
    try:
        from services.bybit_scheduler import bybit_scheduler_service
        result = bybit_scheduler_service.stop_bot(mode, current_user.id)
        return result
    except Exception as e:
        logger.error(f"Error stopping Bybit bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bot/status")
async def get_bybit_bot_status(
    current_user: User = Depends(get_current_user),
    mode: str = Query(None, description="Trading mode (optional)")
):
    """Get Bybit bot status"""
    try:
        from services.bybit_scheduler import bybit_scheduler_service
        result = bybit_scheduler_service.get_status(mode, current_user.id)
        return result
    except Exception as e:
        logger.error(f"Error getting Bybit bot status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================
# Returns Chart
# ==================

@router.get("/history/returns-chart")
async def get_bybit_returns_chart(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    mode: Optional[str] = Query(None, description="Filter by mode: real/simulation"),
    strategy: Optional[str] = Query(None, description="Filter by strategy"),
):
    """Get cumulative returns chart data based on trade history"""
    try:
        from sqlalchemy import func
        
        # Query all close trades for this user (ordered by time)
        # Bybit uses long_close/short_close instead of sell
        # 최근 500건만 조회하여 성능 최적화
        query = db.query(TradeLog).filter(
            TradeLog.user_id == current_user.id,
            TradeLog.exchange == "bybit",
            TradeLog.side.in_(["sell", "long_close", "short_close"]),  # ★ Bybit uses long_close/short_close
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
            cumulative_return_percent = (cumulative_pnl / cumulative_invested * 100) if cumulative_invested > 0 else 0
            
            data_points.append({
                "timestamp": trade.created_at.isoformat() if trade.created_at else "",
                "cumulative_return_percent": cumulative_return_percent,
                "cumulative_pnl": cumulative_pnl,
                "trade_count": trade_count
            })
        
        # Calculate total return
        total_return_percent = (cumulative_pnl / cumulative_invested * 100) if cumulative_invested > 0 else 0
        
        return {
            "data_points": data_points,
            "total_return_percent": total_return_percent,
            "total_pnl": cumulative_pnl,
            "total_trades": trade_count
        }
    except Exception as e:
        logger.error(f"Error getting Bybit returns chart: {e}")
        raise HTTPException(status_code=500, detail=str(e))
