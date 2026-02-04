"""
Morning Star Strategy (샛별형)
Targets technical rebounds at oversold zones during downtrends
Timeframe: 4-Hour (240 minutes)
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
class MorningStarSignal:
    """Signal data for Morning Star strategy"""
    market: str
    signal_type: str  # 'buy' or 'sell' or 'none'
    confidence: float  # 0.2 ~ 1.0
    pattern_low: Optional[float] = None  # Doji candle's low (stop loss level)
    pattern_high: Optional[float] = None  # 패턴 최고점 (N-2 시가)
    rsi: float = 0.0
    bollinger_upper: float = 0.0
    bollinger_lower: float = 0.0
    current_price: float = 0.0
    reason: str = ""
    # ATR-based SL/TP (Jan 2026 Redesign)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    atr: float = 0.0


class MorningStarStrategy:
    """
    샛별형 전략 (Morning Star)
    
    Entry Conditions:
    1. Three-candle pattern: Large bearish → Doji/Spinning → Large bullish
    2. RSI(14) < 35 at pattern completion
    
    Exit Conditions:
    - Take Profit: RSI > 70 OR price touches Bollinger Band upper
    - Stop Loss: Close below pattern's lowest point (doji's low)
    """
    
    RSI_OVERSOLD = 35
    RSI_OVERBOUGHT = 70
    LARGE_CANDLE_BODY_PERCENT = 0.02  # 2% body for large candles
    DOJI_BODY_PERCENT = 0.008  # 0.8% max body for doji (relaxed for crypto volatility)
    
    def __init__(self, timeframe: str = "minute240"):
        """
        Initialize strategy with configurable timeframe
        
        Args:
            timeframe: "day" for daily or "minute240" for 4-hour
        """
        self.timeframe = timeframe
    
    def analyze(self, market: str, timeframe: str = None) -> MorningStarSignal:
        """
        Analyze a market for Morning Star pattern
        
        Args:
            market: Market ticker (e.g., 'KRW-BTC')
            timeframe: Optional timeframe override
            
        Returns:
            MorningStarSignal with analysis results
        """
        try:
            # Use provided timeframe or default to instance timeframe
            tf = timeframe or self.timeframe
            # Get candles
            df = UpbitClient.get_ohlcv(market, interval=tf, count=100)
            if df is None or len(df) < 20:
                return MorningStarSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    reason="Insufficient data"
                )
            
            # 마감된 캔들만 분석 (진행중인 마지막 캔들 제외)
            df = df.iloc[:-1].copy()
            
            # Calculate indicators
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            
            # Bollinger Bands
            bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_lower'] = bb.bollinger_lband()
            df['bb_middle'] = bb.bollinger_mavg()
            
            # ATR for SL/TP buffer calculation
            atr = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
            df['atr'] = atr
            current_atr = df['atr'].iloc[-1]
            
            # Body calculations
            df['body'] = abs(df['close'] - df['open'])
            df['body_percent'] = df['body'] / df['open']
            df['is_bullish'] = df['close'] > df['open']
            
            current = df.iloc[-1]
            current_price = current['close']
            rsi = current['rsi']
            bb_upper = current['bb_upper']
            bb_lower = current['bb_lower']
            
            # Check for Morning Star pattern (last 3 completed candles)
            pattern_result = self._detect_morning_star_weighted(df, current_atr, current_price)
            
            if pattern_result['confidence'] == 0:
                return MorningStarSignal(
                    market=market,
                    signal_type="none",
                    confidence=0.0,
                    rsi=rsi,
                    bollinger_upper=bb_upper,
                    bollinger_lower=bb_lower,
                    current_price=current_price,
                    reason=pattern_result['reason']
                )
            
            return MorningStarSignal(
                market=market,
                signal_type="buy",
                confidence=pattern_result['confidence'],
                pattern_low=pattern_result.get('pattern_low'),
                pattern_high=pattern_result.get('pattern_high'),
                rsi=rsi,
                bollinger_upper=bb_upper,
                bollinger_lower=bb_lower,
                current_price=current_price,
                reason=pattern_result['reason'],
                stop_loss=pattern_result.get('stop_loss'),
                take_profit=pattern_result.get('take_profit'),
                atr=pattern_result.get('atr', 0.0)
            )
            
        except Exception as e:
            logger.error(f"Error analyzing {market} with Morning Star strategy: {e}")
            return MorningStarSignal(
                market=market,
                signal_type="none",
                confidence=0.0,
                reason=f"Analysis error: {e}"
            )
    
    def _detect_morning_star_weighted(self, df: pd.DataFrame, current_atr: float, entry_price: float) -> dict:
        """
        샛별형 패턴 감지 (Morning Star)
        
        진입조건 (모두 필수):
        1. N-2: 긴 음봉
        2. N-1: 도지/팽이형 (짧은 몸통)
        3. N: 양봉 + N-2 음봉 50% 이상 회복 + 종가 마감
        
        SL: N-1 Low
        TP: 진입가 + 손절폭×2 (1:2 RR)
        """
        if len(df) < 5:
            return {'confidence': 0, 'reason': "데이터 부족"}
        
        try:
            # 마지막 3개 캔들 (N-2, N-1, N)
            c1 = df.iloc[-3]  # N-2 (첫째 캔들)
            c2 = df.iloc[-2]  # N-1 (중간 캔들 - 도지)
            c3 = df.iloc[-1]  # N (셋째 캔들)
            
            # ========== 필수조건 1: N-2는 긴 음봉 ==========
            c1_is_bearish = c1['close'] < c1['open']
            if not c1_is_bearish:
                return {'confidence': 0, 'reason': "N-2가 음봉 아님"}
            
            c1_body = abs(c1['close'] - c1['open'])
            c1_body_percent = c1_body / c1['open'] if c1['open'] > 0 else 0
            
            if c1_body_percent < 0.01:  # 1% 이상이어야 "긴" 음봉
                return {'confidence': 0, 'reason': f"N-2 음봉이 너무 작음 ({c1_body_percent*100:.1f}%)"}
            
            # ========== 필수조건 2: N-1은 도지/팽이형 (짧은 몸통) ==========
            c2_body = abs(c2['close'] - c2['open'])
            c2_body_percent = c2_body / c2['open'] if c2['open'] > 0 else 0
            
            if c2_body_percent > 0.01:  # 1% 이하여야 도지/팽이
                return {'confidence': 0, 'reason': f"N-1이 도지 아님 ({c2_body_percent*100:.1f}%)"}
            
            # ========== 필수조건 3: N은 양봉 ==========
            c3_is_bullish = c3['close'] > c3['open']
            if not c3_is_bullish:
                return {'confidence': 0, 'reason': "N이 양봉 아님"}
            
            # ========== 필수조건 4: N이 N-2 음봉 50% 이상 회복 ==========
            c3_body = abs(c3['close'] - c3['open'])
            recovery_ratio = c3_body / c1_body if c1_body > 0 else 0
            
            if recovery_ratio < 0.5:
                return {'confidence': 0, 'reason': f"50% 회복 미달 ({recovery_ratio*100:.0f}%)"}
            
            # ========== 필수조건 5: RSI < 40 (과매도 구간) ==========
            rsi = c2['rsi'] if 'rsi' in c2.index else df['rsi'].iloc[-2]
            if rsi >= 40:
                return {'confidence': 0, 'reason': f"RSI 과매도 아님 ({rsi:.0f} >= 40)"}
            
            # ========== 모든 조건 충족! SL/TP 계산 ==========
            entry_price = c3['close']  # 진입가
            sl_base = c2['low']  # SL 기준: N-1 Low
            
            sl_distance = entry_price - sl_base  # 손절폭 (버퍼 적용 전)
            tp_base = entry_price + (sl_distance * 2)  # TP 기준: 1:2 RR
            
            # ★ 버퍼 적용 (롱)
            stop_loss = sl_base - (current_atr * 1.0)  # SL: 기준 - ATR×1.0
            take_profit = tp_base - (current_atr * 0.2)  # TP: 기준 - ATR×0.2
            
            # ★ LONG 불변조건 가드
            is_valid, risk, error_msg = validate_and_calculate_long(
                entry_price, stop_loss, take_profit, "샛별형"
            )
            if not is_valid:
                return {'confidence': 0, 'reason': f"LONG 불변조건 실패: {error_msg}"}
            
            logger.info(f"[샛별형] 패턴 감지! 음봉{c1_body_percent*100:.1f}%, 도지{c2_body_percent*100:.2f}%, 회복{recovery_ratio*100:.0f}%")
            
            return {
                'confidence': 1.0,  # 모든 조건 충족
                'pattern_low': c2['low'],
                'pattern_high': c1['open'],
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk,
                'reason': f"샛별형: 음봉{c1_body_percent*100:.1f}%, 도지{c2_body_percent*100:.2f}%, 회복{recovery_ratio*100:.0f}%"
            }
            
        except Exception as e:
            return {'confidence': 0, 'reason': f"분석 오류: {e}"}
    
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
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
            df['bb_middle'] = bb.bollinger_mavg()
            
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            df['atr'] = atr_indicator.average_true_range()
            current_atr = df['atr'].iloc[-1]
            
            # 마지막 3개 캔들
            c1 = df.iloc[-3]  # N-2 (큰 음봉)
            c2 = df.iloc[-2]  # N-1 (도지/작은 캔들)
            c3 = df.iloc[-1]  # N (큰 양봉)
            
            # 조건 체크
            c1_body = abs(c1['close'] - c1['open'])
            c1_body_percent = c1_body / c1['open'] if c1['open'] > 0 else 0
            
            if c1['close'] >= c1['open'] or c1_body_percent < self.LARGE_CANDLE_BODY_PERCENT:
                return None
            
            c2_body = abs(c2['close'] - c2['open'])
            c2_body_percent = c2_body / c2['open'] if c2['open'] > 0 else 0
            
            if c2_body_percent > self.DOJI_BODY_PERCENT:
                return None
            
            if c3['close'] <= c3['open']:
                return None
            
            c3_body = abs(c3['close'] - c3['open'])
            recovery_ratio = c3_body / c1_body if c1_body > 0 else 0
            if recovery_ratio < 0.5:
                return None
            
            rsi = c2['rsi'] if 'rsi' in c2.index else df['rsi'].iloc[-2]
            if rsi >= 40:
                return None
            
            # SL/TP
            entry_price = c3['close']
            sl_base = c2['low']
            sl_distance = entry_price - sl_base
            tp_base = entry_price + (sl_distance * 2)
            
            stop_loss = sl_base - (current_atr * 1.0)
            take_profit = tp_base - (current_atr * 0.2)
            
            is_valid, risk, error_msg = validate_and_calculate_long(
                entry_price, stop_loss, take_profit, "샛별형"
            )
            if not is_valid:
                return None
            
            return {
                "action": "BUY",
                "confidence": 1.0,
                "reason": f"샛별형: 음봉{c1_body_percent*100:.1f}%, 도지{c2_body_percent*100:.2f}%, 회복{recovery_ratio*100:.0f}%",
                "reference_data": {
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "pattern_low": c2['low'],
                    "pattern_high": c1['open'],
                    "atr": current_atr
                }
            }
            
        except Exception as e:
            logger.error(f"[MorningStar] analyze_df error: {e}")
            return None
    
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


# Global instances - 4-hour and Daily
morning_star_strategy = MorningStarStrategy(timeframe="minute240")
morning_star_strategy_daily = MorningStarStrategy(timeframe="day")
