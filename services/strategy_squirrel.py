"""
Rising Squirrel Strategy (상승 다람쥐)
Targets healthy pullbacks during strong uptrends
Timeframe: Daily (1D)
"""
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
import pandas as pd
import numpy as np
import ta

from services.upbit_client import UpbitClient
from services.strategy_utils import validate_and_calculate_long
from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class SquirrelSignal:
    """Signal data for Squirrel strategy"""
    market: str
    signal_type: str  # 'buy' or 'sell' or 'none'
    confidence: float  # 0.2 ~ 1.0
    reference_candle_date: Optional[str] = None
    reference_candle_open: Optional[float] = None
    reference_candle_high: Optional[float] = None  # For partial take profit
    reference_candle_median: Optional[float] = None
    current_price: float = 0.0
    ma5: float = 0.0
    reason: str = ""
    # ATR-based SL/TP (Jan 2026 Redesign)
    stop_loss: Optional[float] = None  # 꼬리 최저점 - ATR×1.0
    take_profit: Optional[float] = None  # Range High - ATR×0.2
    atr: float = 0.0


class SquirrelStrategy:
    """
    상승 다람쥐 전략 (Pin Bar / 꼬리 전략)
    
    이미지 기준 진입조건:
    1. 캔들의 몸통(Body)이 상단에 위치
    2. 아래 꼬리(Lower Wick)가 몸통보다 2배 이상 길어야 함
    3. 꼬리가 긴 캔들이 완성(Close)된 후, 다음 캔들 시가(Open)에 진입
    
    SL: Low_signal - (ATR × 0.2)
    TP: 패턴 캔들 고점 + ATR
    """
    
    MIN_WICK_RATIO = 2.0  # 아래꼬리가 몸통의 2배 이상
    
    def __init__(self):
        pass
    
    def analyze(self, market: str) -> SquirrelSignal:
        """
        Pin Bar 패턴 분석 (이미지 기준)
        
        Args:
            market: Market ticker (e.g., 'KRW-BTC')
            
        Returns:
            SquirrelSignal with analysis results
        """
        try:
            # Get daily candles
            df = UpbitClient.get_ohlcv(market, interval="day", count=100)
            if df is None or len(df) < 20:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    reason="Insufficient data"
                )
            
            # 마감된 캔들만 분석 (진행중인 마지막 캔들 제외)
            df = df.iloc[:-1].copy()
            
            # Calculate indicators
            df['ma5'] = df['close'].rolling(window=5).mean()
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            
            # ATR for SL/TP buffer calculation
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            df['atr'] = atr_indicator.average_true_range()
            current_atr = df['atr'].iloc[-1]
            
            # 패턴 캔들 (마지막에서 두번째 = 완성된 캔들)
            pattern_candle = df.iloc[-2]
            # 확인 캔들 (마지막 = 다음 캔들)
            confirm_candle = df.iloc[-1]
            
            current_price = confirm_candle['close']
            ma5 = confirm_candle['ma5']
            
            # ★ Pin Bar 조건 체크
            body = abs(pattern_candle['close'] - pattern_candle['open'])
            lower_wick = min(pattern_candle['close'], pattern_candle['open']) - pattern_candle['low']
            upper_wick = pattern_candle['high'] - max(pattern_candle['close'], pattern_candle['open'])
            
            # 몸통이 0이면 도지 - 패스
            if body < pattern_candle['open'] * 0.001:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    current_price=current_price,
                    ma5=ma5,
                    reason="Body too small (doji)"
                )
            
            # ★ 필수조건 1: 아래꼬리가 몸통의 2배 이상
            wick_ratio = lower_wick / body if body > 0 else 0
            if wick_ratio < self.MIN_WICK_RATIO:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    current_price=current_price,
                    ma5=ma5,
                    reason=f"Lower wick too short ({wick_ratio:.1f}x < 2x)"
                )
            
            # ★ 필수조건 2: 몸통이 상단에 위치 (윗꼬리 < 아래꼬리)
            if upper_wick >= lower_wick:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    current_price=current_price,
                    ma5=ma5,
                    reason="Body not at top (upper wick >= lower wick)"
                )
            
            # ★ 필수조건 3: 확인캔들이 패턴 캔들의 종가 위에서 종가
            if confirm_candle['close'] <= pattern_candle['close']:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    current_price=current_price,
                    ma5=ma5,
                    reason="Confirmation failed (close below pattern close)"
                )
            
            # ★ 필수조건 4: RSI < 50 (과매수 상태가 아니어야 함)
            rsi = pattern_candle['rsi']
            if rsi >= 50:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    current_price=current_price,
                    ma5=ma5,
                    reason=f"RSI too high ({rsi:.0f} >= 50)"
                )
            
            # ★ 필수조건 5: 조정 구간 (패턴 캔들 Low < MA20)
            ma20 = pattern_candle['ma20']
            if pattern_candle['low'] >= ma20:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    current_price=current_price,
                    ma5=ma5,
                    reason=f"Not in pullback zone (Low >= MA20)"
                )
            
            # 신뢰도 제거 - 모든 조건 충족 시 1.0
            
            pattern_date = pattern_candle.name
            if hasattr(pattern_date, 'strftime'):
                pattern_date = pattern_date.strftime('%Y-%m-%d')
            else:
                pattern_date = str(pattern_date)
            
            # ★ SL 기준: 꼬리 최저점 (꼬리가 너무 길면 50% 지점)
            wick_low = pattern_candle['low']
            candle_range = pattern_candle['high'] - pattern_candle['low']
            
            # 꼬리가 캔들 전체의 70% 이상이면 50% 지점 사용
            if lower_wick > candle_range * 0.7:
                sl_base = pattern_candle['low'] + (lower_wick * 0.5)  # 꼬리의 50%
            else:
                sl_base = pattern_candle['low']  # 꼬리 최저점
            
            # ★ TP 기준: 직전 단기 고점 (Range High)
            range_high = df['high'].tail(10).max()
            tp_base = range_high
            
            # ★ 버퍼 적용 (롱)
            stop_loss = sl_base - (current_atr * 1.0)  # SL: 기준 - ATR×1.0
            take_profit = tp_base - (current_atr * 0.2)  # TP: 기준 - ATR×0.2
            
            # ★ LONG 불변조건 가드
            entry_price = current_price
            is_valid, risk, error_msg = validate_and_calculate_long(
                entry_price, stop_loss, take_profit, "다람쥐꼬리"
            )
            if not is_valid:
                return SquirrelSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    current_price=current_price,
                    ma5=ma5,
                    reason=f"LONG 불변조건 실패: {error_msg}"
                )
            
            return SquirrelSignal(
                market=market,
                signal_type="buy",
                confidence=1.0,
                reference_candle_date=pattern_date,
                reference_candle_open=pattern_candle['open'],
                reference_candle_high=pattern_candle['high'],
                reference_candle_median=(pattern_candle['open'] + pattern_candle['close']) / 2,
                current_price=current_price,
                ma5=ma5,
                reason=f"다람쥐꼬리: 아래꼬리 {wick_ratio:.1f}x",
                stop_loss=stop_loss,
                take_profit=take_profit,
                atr=current_atr
            )
            
        except Exception as e:
            logger.error(f"Error analyzing {market} with Squirrel strategy: {e}")
            return SquirrelSignal(
                market=market,
                signal_type="none",
                confidence=0.0,
                reason=f"Analysis error: {e}"
            )
    
    def analyze_df(self, df: pd.DataFrame, symbol: str = "") -> dict:
        """
        DataFrame을 직접 받아 분석 (Bybit용)
        
        Args:
            df: OHLCV DataFrame (columns: open, high, low, close, volume)
            symbol: Symbol name for logging
            
        Returns:
            dict with action, confidence, reason, reference_data
        """
        try:
            if df is None or len(df) < 20:
                return None
            
            # 마감된 캔들만 분석
            df = df.iloc[:-1].copy()
            
            # Calculate indicators
            df['ma5'] = df['close'].rolling(window=5).mean()
            df['ma20'] = df['close'].rolling(window=20).mean()
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            
            # ATR
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            df['atr'] = atr_indicator.average_true_range()
            current_atr = df['atr'].iloc[-1]
            
            # 패턴 캔들, 확인 캔들
            pattern_candle = df.iloc[-2]
            confirm_candle = df.iloc[-1]
            current_price = confirm_candle['close']
            
            # Pin Bar 조건
            body = abs(pattern_candle['close'] - pattern_candle['open'])
            lower_wick = min(pattern_candle['close'], pattern_candle['open']) - pattern_candle['low']
            upper_wick = pattern_candle['high'] - max(pattern_candle['close'], pattern_candle['open'])
            
            if body < pattern_candle['open'] * 0.001:
                return None  # Doji
            
            wick_ratio = lower_wick / body if body > 0 else 0
            if wick_ratio < self.MIN_WICK_RATIO:
                return None
            
            if upper_wick >= lower_wick:
                return None
            
            if confirm_candle['close'] <= pattern_candle['close']:
                return None
            
            rsi = pattern_candle['rsi']
            if rsi >= 50:
                return None
            
            ma20 = pattern_candle['ma20']
            if pattern_candle['low'] >= ma20:
                return None
            
            # SL/TP 계산
            candle_range = pattern_candle['high'] - pattern_candle['low']
            if lower_wick > candle_range * 0.7:
                sl_base = pattern_candle['low'] + (lower_wick * 0.5)
            else:
                sl_base = pattern_candle['low']
            
            range_high = df['high'].tail(10).max()
            tp_base = range_high
            
            stop_loss = sl_base - (current_atr * 1.0)
            take_profit = tp_base - (current_atr * 0.2)
            
            # LONG 불변조건
            is_valid, risk, error_msg = validate_and_calculate_long(
                current_price, stop_loss, take_profit, "다람쥐꼬리"
            )
            if not is_valid:
                return None
            
            return {
                "action": "BUY",
                "confidence": 1.0,
                "reason": f"다람쥐꼬리: 아래꼬리 {wick_ratio:.1f}x",
                "reference_data": {
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern_low": pattern_candle['low'],
                    "pattern_high": pattern_candle['high'],
                    "atr": current_atr
                }
            }
            
        except Exception as e:
            logger.error(f"[Squirrel] analyze_df error: {e}")
            return None
    
    def _calculate_pin_bar_confidence(
        self,
        pattern_candle: pd.Series,
        confirm_candle: pd.Series,
        wick_ratio: float
    ) -> float:
        """Pin Bar 신뢰도 계산"""
        confidence = 0.20  # 기본 20%
        
        # 1. 꼬리 길이 (최대 +35%)
        if wick_ratio >= 4.0:
            confidence += 0.35
        elif wick_ratio >= 3.0:
            confidence += 0.25
        elif wick_ratio >= 2.0:
            confidence += 0.15
        
        # 2. 패턴 캔들이 양봉이면 추가 (최대 +20%)
        if pattern_candle['close'] > pattern_candle['open']:
            confidence += 0.20
        
        # 3. 확인캔들 상승폭 (최대 +25%)
        confirm_gain = (confirm_candle['close'] - pattern_candle['close']) / pattern_candle['close']
        if confirm_gain >= 0.02:  # 2% 이상 상승
            confidence += 0.25
        elif confirm_gain >= 0.01:
            confidence += 0.15
        elif confirm_gain > 0:
            confidence += 0.10
        
        # 4. RSI 과매도 (최대 +20%)
        rsi = pattern_candle.get('rsi', 50)
        if rsi and rsi <= 30:
            confidence += 0.20
        elif rsi and rsi <= 40:
            confidence += 0.10
        
        return min(confidence, 1.0)
    
    def _find_reference_candle(self, df: pd.DataFrame) -> Optional[Tuple[Any, pd.Series]]:
        """
        Find reference candle (장대양봉) in last 10 days
        
        Criteria:
        - Body >= 5% of open price
        - Volume >= 2x of 20-day average volume
        - Bullish (close > open)
        """
        recent = df.iloc[-self.LOOKBACK_DAYS:]
        
        for idx in range(len(recent) - 1, -1, -1):
            row = recent.iloc[idx]
            
            # Check if bullish
            if row['close'] <= row['open']:
                continue
            
            # Check body size
            if row['body_percent'] < self.MIN_BODY_PERCENT:
                continue
            
            # Check volume
            if row['volume'] < row['avg_volume'] * self.MIN_VOLUME_RATIO:
                continue
            
            return (recent.index[idx], row)
        
        return None
    
    def _calculate_confidence(
        self,
        current_price: float,
        ref_median: float,
        ref_open: float,
        ref_close: float,
        current_volume: float,
        ref_volume: float
    ) -> float:
        """
        Calculate confidence score (0.2 ~ 1.0)
        
        Factors:
        - Distance from median (closer to median = higher confidence)
        - Volume contraction level (more contraction = higher confidence)
        """
        # Price position score (0.0 ~ 0.5)
        price_range = ref_close - ref_median
        if price_range > 0:
            price_position = (current_price - ref_median) / price_range
            price_score = max(0, min(0.5, 0.5 - price_position * 0.3))
        else:
            price_score = 0.25
        
        # Volume contraction score (0.0 ~ 0.5)
        volume_ratio = current_volume / ref_volume
        volume_score = max(0, min(0.5, 0.5 - volume_ratio))
        
        confidence = 0.2 + price_score + volume_score
        return round(min(1.0, max(0.2, confidence)), 2)
    
    def check_exit(
        self,
        market: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float
    ) -> Tuple[bool, str, Optional[float]]:
        """
        단순화된 청산 조건 체크 (Phase 4: 분할청산 제거)
        
        Args:
            market: Market ticker
            entry_price: Entry price of position
            stop_loss: Stop loss price
            take_profit: Take profit price
            
        Returns:
            Tuple of (should_exit, reason, current_price)
        """
        try:
            df = UpbitClient.get_ohlcv(market, interval="day", count=20)
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


# Global instance
squirrel_strategy = SquirrelStrategy()

