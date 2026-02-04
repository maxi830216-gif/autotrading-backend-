"""
Chart API Router
Provides candlestick chart data with strategy indicators for trade visualization
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
import pandas as pd
import ta

from models.database import get_db, TradeLog, Position, User, CandleSnapshot, PositionHistory
from routers.auth import get_current_user
from services.upbit_client import UpbitClient
from services.bybit_client import BybitClient
from utils.pattern_utils import calculate_rsi, find_local_minima, find_local_maxima
from utils.logger import setup_logger
import json

logger = setup_logger(__name__)

router = APIRouter()


def _calculate_indicators(df: pd.DataFrame, strategy: str) -> Dict[str, Any]:
    """Calculate strategy-specific indicators"""
    indicators = {}
    
    # RSI (common for most strategies)
    if 'rsi' not in df.columns:
        df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    indicators['rsi'] = df['rsi'].fillna(50).tolist()
    
    # MA5 (for squirrel)
    df['ma5'] = df['close'].rolling(window=5).mean()
    indicators['ma5'] = df['ma5'].fillna(method='bfill').tolist()
    
    # MA20 (for inverted_hammer)
    df['ma20'] = df['close'].rolling(window=20).mean()
    indicators['ma20'] = df['ma20'].fillna(method='bfill').tolist()
    
    # Bollinger Bands (for morning star)
    if strategy in ['morning']:
        bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
        indicators['bb_upper'] = bb.bollinger_hband().fillna(method='bfill').tolist()
        indicators['bb_lower'] = bb.bollinger_lband().fillna(method='bfill').tolist()
        indicators['bb_middle'] = bb.bollinger_mavg().fillna(method='bfill').tolist()
    
    return indicators


def _detect_pattern_markers(df: pd.DataFrame, strategy: str, trade_time: datetime) -> Dict[str, Any]:
    """Detect pattern-specific markers for visualization"""
    pattern = {
        'type': strategy,
        'markers': []
    }
    
    if strategy in ['divergence', 'bearish_divergence']:
        # Find divergence low/high points
        if strategy == 'divergence':
            lows = find_local_minima(df['low'], window=7)
            if len(lows) >= 2:
                pattern['price_lows'] = lows[-2:].tolist() if hasattr(lows, 'tolist') else list(lows[-2:])
                pattern['markers'].append({
                    'type': 'divergence',
                    'indices': pattern['price_lows']
                })
        else:
            highs = find_local_maxima(df['high'], window=7)
            if len(highs) >= 2:
                pattern['price_highs'] = highs[-2:].tolist() if hasattr(highs, 'tolist') else list(highs[-2:])
                pattern['markers'].append({
                    'type': 'bearish_divergence',
                    'indices': pattern['price_highs']
                })
    
    elif strategy in ['morning', 'evening_star']:
        # Mark 3-candle pattern
        pattern['markers'].append({
            'type': 'three_candle',
            'indices': [-3, -2, -1]  # Last 3 candles
        })
    
    elif strategy == 'squirrel':
        # Find reference candle (large bullish)
        df['body_percent'] = abs(df['close'] - df['open']) / df['open']
        df['avg_volume'] = df['volume'].rolling(window=20).mean()
        for i in range(len(df) - 10, len(df)):
            if i < 0:
                continue
            row = df.iloc[i]
            if row['close'] > row['open'] and row['body_percent'] >= 0.05:
                if row['volume'] >= row['avg_volume'] * 2:
                    pattern['reference_candle_idx'] = i
                    pattern['markers'].append({
                        'type': 'reference_candle',
                        'index': i
                    })
                    break
    
    return pattern


@router.get("/trade/{trade_id}")
async def get_trade_chart_data(
    trade_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get chart data for a specific trade with strategy indicators.
    Uses stored snapshot if available, otherwise fetches live data.
    """
    try:
        # Get trade log
        trade = db.query(TradeLog).filter(
            TradeLog.id == trade_id,
            TradeLog.user_id == current_user.id
        ).first()
        
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        
        exchange = trade.exchange or 'upbit'
        coin = trade.coin
        timeframe = trade.timeframe or '4H'
        strategy = trade.strategy
        
        # Check for stored snapshot first
        snapshot = db.query(CandleSnapshot).filter(
            CandleSnapshot.trade_log_id == trade_id
        ).first()
        
        if snapshot:
            # Use stored snapshot data
            candles = json.loads(snapshot.candles_json)
            indicators = json.loads(snapshot.indicators_json) if snapshot.indicators_json else {}
            logger.info(f"Using stored snapshot for trade {trade_id}")
        else:
            # Fallback to live API
            logger.info(f"No snapshot found for trade {trade_id}, fetching live data")
            if exchange == 'upbit':
                if timeframe == '1D':
                    interval = 'day'
                elif timeframe == '4H':
                    interval = 'minute240'
                else:
                    interval = 'minute240'
                
                df = UpbitClient.get_ohlcv(coin, interval=interval, count=60)
            else:
                if timeframe == '1D':
                    interval = 'D'
                elif timeframe == '4H':
                    interval = '240'
                else:
                    interval = '240'
                
                df = BybitClient.get_ohlcv(coin, interval=interval, limit=60)
            
            if df is None or len(df) < 20:
                raise HTTPException(status_code=400, detail="Insufficient candle data")
            
            # Convert DataFrame to JSON format
            candles = []
            for idx, row in df.iterrows():
                # Try index first (Upbit uses DatetimeIndex)
                if hasattr(idx, 'timestamp'):
                    timestamp = idx.timestamp()
                # Then try 'timestamp' column (Bybit uses this)
                elif 'timestamp' in row and hasattr(row['timestamp'], 'timestamp'):
                    timestamp = row['timestamp'].timestamp()
                elif 'timestamp' in row:
                    # Bybit timestamp is already a datetime-like object
                    ts = row['timestamp']
                    if isinstance(ts, (int, float)):
                        timestamp = ts / 1000 if ts > 1e12 else ts  # Handle milliseconds
                    else:
                        timestamp = pd.Timestamp(ts).timestamp()
                else:
                    timestamp = datetime.now().timestamp()
                candles.append({
                    'time': int(timestamp),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume'])
                })
            
            # Calculate indicators
            indicators = _calculate_indicators(df.copy(), strategy)
        
        # Get levels from trade (always from DB)
        levels = {
            'entry': float(trade.price) if trade.price else None,
            'stop_loss': float(trade.stop_loss) if trade.stop_loss else None,
            'take_profit': float(trade.take_profit) if trade.take_profit else None,
            'take_profit_2': float(trade.take_profit_2) if hasattr(trade, 'take_profit_2') and trade.take_profit_2 else None  # ★ Phase 9
        }
        
        # Trade info
        trade_info = {
            'id': trade.id,
            'coin': trade.coin,
            'strategy': trade.strategy,
            'timeframe': trade.timeframe,
            'side': trade.side,
            'price': float(trade.price) if trade.price else None,
            'quantity': float(trade.quantity) if trade.quantity else None,
            'pnl': float(trade.pnl) if trade.pnl else None,
            'pnl_percent': float(trade.pnl_percent) if trade.pnl_percent else None,
            'reason': trade.reason,
            'confidence': float(trade.confidence) if trade.confidence else None,
            'created_at': trade.created_at.isoformat() if trade.created_at else None,
            'exchange': exchange,
            'has_snapshot': snapshot is not None
        }
        
        # Pattern markers (only for live data, skip for snapshots)
        pattern = {'type': strategy, 'markers': []}
        
        return {
            'candles': candles,
            'indicators': indicators,
            'levels': levels,
            'pattern': pattern,
            'trade': trade_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trade chart data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/position/{position_id}")
async def get_position_chart_data(
    position_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get chart data for an open position with current strategy indicators
    """
    try:
        # Get position
        position = db.query(Position).filter(
            Position.id == position_id,
            Position.user_id == current_user.id
        ).first()
        
        if not position:
            raise HTTPException(status_code=404, detail="Position not found")
        
        exchange = position.exchange or 'upbit'
        coin = position.coin
        timeframe = position.timeframe or '4H'
        strategy = position.strategy
        
        # Get candle data
        if exchange == 'upbit':
            interval = 'day' if timeframe == '1D' else 'minute240'
            df = UpbitClient.get_ohlcv(coin, interval=interval, count=60)
        else:
            interval = 'D' if timeframe == '1D' else '240'
            df = BybitClient.get_ohlcv(coin, interval=interval, limit=60)
        
        if df is None or len(df) < 20:
            raise HTTPException(status_code=400, detail="Insufficient candle data")
        
        # Convert to candles
        candles = []
        for idx, row in df.iterrows():
            # Try index first (Upbit uses DatetimeIndex)
            if hasattr(idx, 'timestamp'):
                timestamp = idx.timestamp()
            # Then try 'timestamp' column (Bybit uses this)
            elif 'timestamp' in row and hasattr(row['timestamp'], 'timestamp'):
                timestamp = row['timestamp'].timestamp()
            elif 'timestamp' in row:
                # Bybit timestamp is already a datetime-like object
                ts = row['timestamp']
                if isinstance(ts, (int, float)):
                    timestamp = ts / 1000 if ts > 1e12 else ts  # Handle milliseconds
                else:
                    timestamp = pd.Timestamp(ts).timestamp()
            else:
                timestamp = datetime.now().timestamp()
            candles.append({
                'time': int(timestamp),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume'])
            })
        
        # Calculate indicators
        indicators = _calculate_indicators(df.copy(), strategy)
        
        # Get levels from position
        levels = {
            'entry': float(position.entry_price) if position.entry_price else None,
            'stop_loss': float(position.stop_loss) if position.stop_loss else float(position.reference_candle_low) if position.reference_candle_low else None,
            'take_profit': float(position.take_profit) if position.take_profit else float(position.reference_candle_high) if position.reference_candle_high else None,
            'take_profit_2': float(position.take_profit_2) if position.take_profit_2 else None  # ★ Phase 9
        }
        
        # Current price
        current_price = df['close'].iloc[-1]
        pnl_percent = ((current_price - position.entry_price) / position.entry_price) * 100 if position.entry_price else 0
        
        # Position info
        position_info = {
            'id': position.id,
            'coin': position.coin,
            'strategy': position.strategy,
            'timeframe': position.timeframe,
            'direction': position.direction or 'long',
            'entry_price': float(position.entry_price) if position.entry_price else None,
            'quantity': float(position.quantity) if position.quantity else None,
            'current_price': float(current_price),
            'pnl_percent': float(pnl_percent),
            'created_at': position.created_at.isoformat() if position.created_at else None,
            'exchange': exchange
        }
        
        pattern = _detect_pattern_markers(df.copy(), strategy, position.created_at)
        
        return {
            'candles': candles,
            'indicators': indicators,
            'levels': levels,
            'pattern': pattern,
            'position': position_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting position chart data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/position-history/{position_id}")
async def get_position_history_chart(
    position_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get complete position history with all events (entry, stop loss, take profit).
    Returns candle data from entry event + all event markers for chart visualization.
    """
    try:
        # Get all events for this position
        events = db.query(PositionHistory).filter(
            PositionHistory.position_id == position_id,
            PositionHistory.user_id == current_user.id
        ).order_by(PositionHistory.created_at).all()
        
        if not events:
            raise HTTPException(status_code=404, detail="Position history not found")
        
        # Use entry event's candle data as base
        entry_event = next((e for e in events if e.event_type == 'entry'), events[0])
        
        # Get candle data from entry event snapshot
        if entry_event.candles_json:
            candles = json.loads(entry_event.candles_json)
            indicators = json.loads(entry_event.indicators_json) if entry_event.indicators_json else {}
        else:
            # Fallback to live data
            coin = entry_event.coin
            timeframe = entry_event.timeframe or '4H'
            exchange = entry_event.exchange or 'upbit'
            
            if exchange == 'upbit':
                interval = 'day' if timeframe == '1D' else 'minute240'
                df = UpbitClient.get_ohlcv(coin, interval=interval, count=60)
            else:
                interval = 'D' if timeframe == '1D' else '240'
                df = BybitClient.get_ohlcv(coin, interval=interval, limit=60)
            
            if df is None or len(df) < 20:
                raise HTTPException(status_code=400, detail="Insufficient candle data")
            
            candles = []
            for idx, row in df.iterrows():
                # Try index first (Upbit uses DatetimeIndex)
                if hasattr(idx, 'timestamp'):
                    timestamp = idx.timestamp()
                # Then try 'timestamp' column (Bybit uses this)
                elif 'timestamp' in row and hasattr(row['timestamp'], 'timestamp'):
                    timestamp = row['timestamp'].timestamp()
                elif 'timestamp' in row:
                    # Bybit timestamp is already a datetime-like object
                    ts = row['timestamp']
                    if isinstance(ts, (int, float)):
                        timestamp = ts / 1000 if ts > 1e12 else ts  # Handle milliseconds
                    else:
                        timestamp = pd.Timestamp(ts).timestamp()
                else:
                    timestamp = datetime.now().timestamp()
                candles.append({
                    'time': int(timestamp),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume'])
                })
            
            indicators = _calculate_indicators(df.copy(), entry_event.strategy)
        
        # Get levels from entry event
        levels = {
            'entry': float(entry_event.entry_price) if entry_event.entry_price else None,
            'stop_loss': float(entry_event.stop_loss_price) if entry_event.stop_loss_price else None,
            'take_profit': float(entry_event.take_profit_price) if entry_event.take_profit_price else None,
            'take_profit_2': float(entry_event.take_profit_2_price) if hasattr(entry_event, 'take_profit_2_price') and entry_event.take_profit_2_price else None  # ★ Phase 9
        }
        
        # Build event markers
        event_markers = []
        for event in events:
            event_markers.append({
                'id': event.id,
                'type': event.event_type,
                'price': float(event.event_price) if event.event_price else None,
                'quantity': float(event.event_quantity) if event.event_quantity else None,
                'reason': event.event_reason,
                'pnl_percent': float(event.pnl_percent) if event.pnl_percent else None,
                'timestamp': event.created_at.isoformat() if event.created_at else None,
                'has_snapshot': event.candles_json is not None
            })
        
        # Position info
        position_info = {
            'position_id': position_id,
            'coin': entry_event.coin,
            'strategy': entry_event.strategy,
            'timeframe': entry_event.timeframe,
            'exchange': entry_event.exchange,
            'mode': entry_event.mode,
            'total_events': len(events)
        }
        
        return {
            'candles': candles,
            'indicators': indicators,
            'levels': levels,
            'events': event_markers,
            'position': position_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting position history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

