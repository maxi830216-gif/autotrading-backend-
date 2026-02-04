"""
Inverted Hammer Strategy (하락 브레이크 윗꼬리 양봉)
Targets reversals at oversold zones with false break confirmation
Timeframe: Daily (1D) ONLY - 4H has too much noise
"""
from typing import Optional, Tuple
from dataclasses import dataclass
import pandas as pd
import numpy as np
import ta

from services.upbit_client import UpbitClient
from services.strategy_utils import validate_and_calculate_long
from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class InvertedHammerSignal:
    """Signal data for Inverted Hammer strategy"""
    market: str
    signal_type: str  # 'buy' or 'sell' or 'none'
    confidence: float  # 0.2 ~ 1.0
    pattern_candle_date: Optional[str] = None
    pattern_high: float = 0.0  # 패턴 캔들 High
    pattern_low: float = 0.0   # 패턴 캔들 Low
    ma20: float = 0.0          # 20일 이동평균
    current_price: float = 0.0
    rsi: float = 0.0
    reason: str = ""
    # ATR-based SL/TP (Jan 2026 Redesign)
    stop_loss: Optional[float] = None  # pattern_low - ATR×1.0
    take_profit: Optional[float] = None  # 진입가 + 윗꼬리 - ATR×0.2
    atr: float = 0.0


class InvertedHammerStrategy:
    """
    하락 브레이크 윗꼬리 양봉 전략 (Inverted Hammer / False Break)
    
    Entry Conditions:
    1. Downtrend: Close < MA20 and RSI(14) <= 40
    2. Pattern shape: 
       - Bullish candle (Close > Open)
       - Upper wick >= 2x body
       - Lower wick < 50% of body
    3. False break: Low < lowest low of last 10 candles
    4. Volume spike: Volume > 1.5x of 20-day average
    5. Confirmation: Next candle closes above pattern's close (conservative)
    
    Exit Conditions:
    - Take Profit 1: Pattern candle's High
    - Take Profit 2: MA20 touch
    - Stop Loss: Pattern candle's Low breach
    """
    
    LOOKBACK_CANDLES = 10
    UPPER_WICK_MIN_RATIO = 2.0   # Upper wick >= 2x body
    LOWER_WICK_MAX_RATIO = 0.5   # Lower wick < 50% of body
    RSI_OVERSOLD = 40
    MA_PERIOD = 20
    VOLUME_SPIKE_RATIO = 1.5    # Volume >= 1.5x of 20-day average
    
    def __init__(self, timeframe: str = "day"):
        """
        Initialize strategy with configurable timeframe
        
        Args:
            timeframe: "day" for daily or "minute240" for 4-hour
        """
        self.timeframe = timeframe
    
    def analyze(self, market: str, timeframe: str = None) -> InvertedHammerSignal:
        """
        Analyze a market for Inverted Hammer pattern
        
        Args:
            market: Market ticker (e.g., 'KRW-BTC')
            
        Returns:
            InvertedHammerSignal with analysis results
        """
        try:
            # Use provided timeframe or default to instance timeframe
            tf = timeframe or self.timeframe
            # Get candles
            df = UpbitClient.get_ohlcv(market, interval=tf, count=100)
            if df is None or len(df) < self.MA_PERIOD + self.LOOKBACK_CANDLES:
                return InvertedHammerSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    reason="Insufficient data"
                )
            
            # 마감된 캔들만 분석 (진행중인 마지막 캔들 제외)
            df = df.iloc[:-1].copy()
            
            # Calculate indicators
            df['ma20'] = df['close'].rolling(window=self.MA_PERIOD).mean()
            df['rsi'] = self._calculate_rsi(df['close'], 14)
            df['avg_volume'] = df['volume'].rolling(window=self.MA_PERIOD).mean()
            
            # ATR for SL/TP buffer calculation
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            df['atr'] = atr_indicator.average_true_range()
            current_atr = df['atr'].iloc[-1]
            
            # Calculate body and wick sizes
            df['body'] = abs(df['close'] - df['open'])
            df['upper_wick'] = df.apply(
                lambda x: x['high'] - max(x['close'], x['open']), axis=1
            )
            df['lower_wick'] = df.apply(
                lambda x: min(x['close'], x['open']) - x['low'], axis=1
            )
            
            # Calculate support level (lowest low of last N candles, excluding current)
            support_lookback = df.iloc[-(self.LOOKBACK_CANDLES + 1):-1]
            support_level = support_lookback['low'].min()
            
            # Get the most recent completed candle (pattern candidate)
            pattern_candle = df.iloc[-2]
            confirmation_candle = df.iloc[-1]
            
            pattern_date = pattern_candle.name
            if hasattr(pattern_date, 'strftime'):
                pattern_date = pattern_date.strftime('%Y-%m-%d %H:%M')
            else:
                pattern_date = str(pattern_date)
            
            current_price = confirmation_candle['close']
            ma20 = confirmation_candle['ma20']
            rsi = pattern_candle['rsi']
            
            # Calculate weighted confidence
            confidence_result = self._calculate_weighted_confidence(
                pattern_candle=pattern_candle,
                confirmation_candle=confirmation_candle,
                support_level=support_level,
                ma20=pattern_candle['ma20']
            )
            
            confidence = confidence_result['confidence']
            reason = confidence_result['reason']
            
            # Return buy signal if confidence > 0 (meets required conditions)
            if confidence > 0:
                # SL/TP 기준
                entry_price = current_price
                upper_wick = pattern_candle['upper_wick']
                
                sl_base = pattern_candle['low']  # 역망치 Low
                pattern_tp = entry_price + upper_wick  # 1:1 RR
                
                # ★ 버퍼 적용 (롱)
                stop_loss = sl_base - (current_atr * 1.0)  # SL: 기준 - ATR×1.0
                tp_base = pattern_tp - (current_atr * 0.2)  # TP: 기준 - ATR×0.2
                
                # ★ R:R 1:1.5 보장
                from services.strategy_utils import ensure_min_rr_long
                take_profit = ensure_min_rr_long(entry_price, stop_loss, tp_base)
                
                # ★ LONG 불변조건 가드
                is_valid, risk, error_msg = validate_and_calculate_long(
                    current_price, stop_loss, take_profit, "윗꼬리양봉"
                )
                if not is_valid:
                    return InvertedHammerSignal(
                        market=market,
                        signal_type="none",
                        confidence=0.0,
                        pattern_candle_date=pattern_date,
                        pattern_high=pattern_candle['high'],
                        pattern_low=pattern_candle['low'],
                        ma20=ma20,
                        current_price=current_price,
                        rsi=rsi,
                        reason=f"LONG 불변조건 실패: {error_msg}"
                    )
                
                return InvertedHammerSignal(
                    market=market,
                    signal_type="buy",
                    confidence=confidence,
                    pattern_candle_date=pattern_date,
                    pattern_high=pattern_candle['high'],
                    pattern_low=pattern_candle['low'],
                    ma20=ma20,
                    current_price=current_price,
                    rsi=rsi,
                    reason=reason,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    atr=current_atr
                )
            
            return InvertedHammerSignal(
                market=market,
                signal_type="none",
                confidence=0.0,
                pattern_candle_date=pattern_date,
                pattern_high=pattern_candle['high'],
                pattern_low=pattern_candle['low'],
                ma20=ma20,
                current_price=current_price,
                rsi=rsi,
                reason=reason
            )
            
        except Exception as e:
            logger.error(f"Error analyzing {market} with Inverted Hammer strategy: {e}")
            return InvertedHammerSignal(
                market=market,
                signal_type="none",
                confidence=0.0,
                reason=f"Analysis error: {e}"
            )
    
    def _calculate_weighted_confidence(
        self,
        pattern_candle: pd.Series,
        confirmation_candle: pd.Series,
        support_level: float,
        ma20: float
    ) -> dict:
        """
        역망치형 패턴 감지 (Inverted Hammer)
        
        진입조건 (모두 필수):
        1. 하락 추세 (Close < MA20)
        2. 윗꼬리 ≥ 몸통×2
        3. 아래꼬리 없거나 매우 짧음 (< 몸통×0.5)
        4. 확인: 다음캔들 양봉 or 역망치 고점 돌파
        
        SL: 역망치 Low
        TP: 진입가 + 윗꼬리길이 (1:1)
        """
        # ========== 필수조건 1: 하락 추세 (Close < MA20) ==========
        if pattern_candle['close'] >= ma20:
            return {'confidence': 0.0, 'reason': "하락추세 아님 (Close >= MA20)"}
        
        # ========== 캔들 구조 계산 ==========
        body = abs(pattern_candle['close'] - pattern_candle['open'])
        upper_wick = pattern_candle['high'] - max(pattern_candle['close'], pattern_candle['open'])
        lower_wick = min(pattern_candle['close'], pattern_candle['open']) - pattern_candle['low']
        
        # Body가 너무 작으면 doji (패턴 불성립)
        if body < pattern_candle['open'] * 0.001:
            return {'confidence': 0.0, 'reason': "몸통이 너무 작음 (도지)"}
        
        # ========== 필수조건 2: 윗꼬리 ≥ 몸통×2 ==========
        wick_ratio = upper_wick / body if body > 0 else 0
        if wick_ratio < 2.0:
            return {'confidence': 0.0, 'reason': f"윗꼬리 부족 ({wick_ratio:.1f}x < 2x)"}
        
        # ========== 필수조건 3: 아래꼬리 없거나 짧음 (< 몸통×0.5) ==========
        lower_wick_ratio = lower_wick / body if body > 0 else 0
        if lower_wick_ratio > 0.5:
            return {'confidence': 0.0, 'reason': f"아래꼬리 너무 김 ({lower_wick_ratio:.1f}x > 0.5x)"}
        
        # ========== 필수조건 4: 확인 (다음캔들 양봉 or 고점 돌파) ==========
        confirm_is_bullish = confirmation_candle['close'] > confirmation_candle['open']
        confirm_breaks_high = confirmation_candle['close'] > pattern_candle['high']
        
        if not (confirm_is_bullish or confirm_breaks_high):
            return {'confidence': 0.0, 'reason': "확인 실패 (양봉도 아니고 고점돌파도 아님)"}
        
        # ========== 모든 조건 충족! ==========
        logger.info(f"[역망치] 패턴 감지! 윗꼬리{wick_ratio:.1f}x, 아래꼬리{lower_wick_ratio:.1f}x")
        
        return {
            'confidence': 1.0,
            'reason': f"역망치: 윗꼬리{wick_ratio:.1f}x, 아래꼬리{lower_wick_ratio:.1f}x, {'양봉확인' if confirm_is_bullish else '고점돌파'}"
        }
    
    def analyze_df(self, df: pd.DataFrame, symbol: str = "") -> dict:
        """
        DataFrame을 직접 받아 분석 (Bybit용)
        
        Returns:
            dict with action, confidence, reason, reference_data or None
        """
        try:
            if df is None or len(df) < 20:
                return None
            
            # 마감된 캔들만 분석
            df = df.iloc[:-1].copy()
            
            # Calculate indicators
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            df['atr'] = atr_indicator.average_true_range()
            current_atr = df['atr'].iloc[-1]
            
            # 패턴 캔들, 확인 캔들
            pattern_candle = df.iloc[-2]
            confirmation_candle = df.iloc[-1]
            ma20 = pattern_candle['ma20']
            current_price = confirmation_candle['close']
            
            # 필수조건 1: 하락 추세
            if pattern_candle['close'] >= ma20:
                return None
            
            # 캔들 구조 계산
            body = abs(pattern_candle['close'] - pattern_candle['open'])
            upper_wick = pattern_candle['high'] - max(pattern_candle['close'], pattern_candle['open'])
            lower_wick = min(pattern_candle['close'], pattern_candle['open']) - pattern_candle['low']
            
            if body < pattern_candle['open'] * 0.001:
                return None  # Doji
            
            # 필수조건 2: 윗꼬리 >= 몸통×2
            wick_ratio = upper_wick / body if body > 0 else 0
            if wick_ratio < 2.0:
                return None
            
            # 필수조건 3: 아래꼬리 < 몸통×0.5
            lower_wick_ratio = lower_wick / body if body > 0 else 0
            if lower_wick_ratio > 0.5:
                return None
            
            # 필수조건 4: 확인
            confirm_is_bullish = confirmation_candle['close'] > confirmation_candle['open']
            confirm_breaks_high = confirmation_candle['close'] > pattern_candle['high']
            if not (confirm_is_bullish or confirm_breaks_high):
                return None
            
            # SL/TP 계산
            sl_base = pattern_candle['low']
            pattern_tp = current_price + upper_wick  # 1:1 (윗꼬리 길이만큼 TP)
            
            stop_loss = sl_base - (current_atr * 1.0)
            tp_base = pattern_tp - (current_atr * 0.2)
            
            # ★ R:R 1:1.5 보장
            from services.strategy_utils import validate_and_calculate_long, ensure_min_rr_long
            take_profit = ensure_min_rr_long(current_price, stop_loss, tp_base)
            
            # LONG 불변조건
            is_valid, risk, error_msg = validate_and_calculate_long(
                current_price, stop_loss, take_profit, "윗꼬리양봉"
            )
            if not is_valid:
                return None
            
            return {
                "action": "BUY",
                "confidence": 1.0,
                "reason": f"역망치: 윗꼬리{wick_ratio:.1f}x, {'양봉확인' if confirm_is_bullish else '고점돌파'}",
                "reference_data": {
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern_low": pattern_candle['low'],
                    "pattern_high": pattern_candle['high'],
                    "atr": current_atr
                }
            }
            
        except Exception as e:
            logger.error(f"[InvertedHammer] analyze_df error: {e}")
            return None
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI indicator"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def check_exit(
        self,
        market: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        timeframe: str = None
    ) -> Tuple[bool, str, Optional[float]]:
        """
        단순화된 청산 조건 체크 (Phase 4: 분할청산 제거)
        
        Args:
            market: Market ticker
            entry_price: Entry price of position
            stop_loss: Stop loss price
            take_profit: Take profit price
            timeframe: Optional timeframe override
            
        Returns:
            Tuple of (should_exit, reason, current_price)
        """
        try:
            tf = timeframe or self.timeframe
            df = UpbitClient.get_ohlcv(market, interval=tf, count=20)
            if df is None or len(df) < 5:
                return False, "데이터 부족", None
            
            current_price = df['close'].iloc[-1]
            profit_pct = (current_price - entry_price) / entry_price
            
            # 손절 체크 (LONG: 현재가 <= 손절가)
            if current_price <= stop_loss:
                return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", current_price
            
            # 익절 체크 (LONG: 현재가 >= 익절가)
            if current_price >= take_profit:
                return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", current_price
            
            return False, f"보유중 ({profit_pct*100:+.1f}%)", current_price
            
        except Exception as e:
            logger.error(f"Error checking exit for {market}: {e}")
            return False, f"오류: {e}", None

# Global instances - Daily and 4-hour
inverted_hammer_strategy = InvertedHammerStrategy(timeframe="day")
inverted_hammer_strategy_4h = InvertedHammerStrategy(timeframe="minute240")
