"""
Evening Star Strategy (석양형) - SHORT
Targets technical reversals at overbought zones during uptrends
"""
from typing import Optional, Tuple, Dict
from dataclasses import dataclass
import pandas as pd
import ta

from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_short, check_exit_short

logger = setup_logger(__name__)


@dataclass
class EveningStarSignal:
    """Signal data for Evening Star strategy"""
    market: str
    signal_type: str  # 'short' or 'none'
    confidence: float
    pattern_high: Optional[float] = None  # 패턴 최고점
    rsi: float = 0.0
    current_price: float = 0.0
    reason: str = ""
    # ATR-based SL/TP
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    atr: float = 0.0


class EveningStarStrategy:
    """
    석양형 전략 (SHORT)
    3캔들 반전 패턴: 긴 양봉(N-2) → 도지(N-1) → 긴 음봉(N)
    """
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "석양형"
    
    def analyze(self, df: pd.DataFrame, market: str = "") -> Tuple[bool, float, Optional[Dict]]:
        """
        Analyze for Evening Star pattern
        
        Returns:
            (is_signal, confidence, info_dict)
        """
        try:
            if len(df) < 20:
                return False, 0.0, {}
            
            # Calculate indicators
            df = df.copy()
            df['body'] = abs(df['close'] - df['open'])
            df['body_pct'] = df['body'] / df['open'] * 100
            df['is_bullish'] = df['close'] > df['open']
            
            # Check for Evening Star pattern
            pattern_result = self._detect_evening_star(df)
            
            if pattern_result['confidence'] == 0:
                return False, 0.0, {}
            
            # ATR 계산 for SL/TP buffer
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            current_price = df['close'].iloc[-1]
            pattern_high = pattern_result.get('pattern_high', current_price)
            doji_high = pattern_result.get('doji_high', pattern_high)  # 중간 캔들(N-1)의 High
            
            # SL/TP 기준 (SHORT)
            entry_price = current_price
            sl_base = doji_high  # SL 기준: N-1 High
            sl_distance = sl_base - entry_price  # 손절폭 (버퍼 적용 전)
            tp_base = entry_price - (sl_distance * 2)  # TP 기준: 1:2 RR
            
            # ★ 버퍼 적용 (숏)
            stop_loss = sl_base + (current_atr * 1.0)  # SL: 기준 + ATR×1.0
            take_profit = tp_base + (current_atr * 0.2)  # TP: 기준 + ATR×0.2
            
            # ★ SHORT 불변조건 가드 (strategy_utils 사용)
            is_valid, risk, error_msg = validate_and_calculate_short(
                entry_price, stop_loss, take_profit, self.name
            )
            if not is_valid:
                return False, 0.0, {}
            
            info = {
                'pattern_high': pattern_high,
                'doji_high': doji_high,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk,
                'reason': pattern_result['reason']
            }
            
            logger.info(f"[석양형] 감지!")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[석양형] 분석 오류: {e}")
            return False, 0.0, {}
    
    def _detect_evening_star(self, df: pd.DataFrame) -> dict:
        """
        석양형 패턴 감지 (Evening Star)
        
        진입조건 (모두 필수):
        1. N-2: 긴 양봉
        2. N-1: 작은 몸통
        3. N: 긴 음봉 + N-2 양봉 50% 이상 하락
        
        SL: N-1 High
        TP: 진입가 - 손절폭×2 (1:2 RR)
        """
        if len(df) < 5:
            return {'confidence': 0, 'reason': "데이터 부족"}
        
        try:
            # 마지막 3개 캔들 (N-2, N-1, N)
            c1 = df.iloc[-3]  # N-2 (양봉)
            c2 = df.iloc[-2]  # N-1 (작은 몸통)
            c3 = df.iloc[-1]  # N (음봉)
            
            # ========== 필수조건 1: N-2는 긴 양봉 ==========
            c1_is_bullish = c1['close'] > c1['open']
            if not c1_is_bullish:
                return {'confidence': 0, 'reason': "N-2가 양봉 아님"}
            
            c1_body = abs(c1['close'] - c1['open'])
            c1_body_percent = c1_body / c1['open'] if c1['open'] > 0 else 0
            
            if c1_body_percent < 0.01:  # 1% 이상이어야 "긴" 양봉
                return {'confidence': 0, 'reason': f"N-2 양봉이 너무 작음 ({c1_body_percent*100:.1f}%)"}
            
            # ========== 필수조건 2: N-1은 작은 몸통 ==========
            c2_body = abs(c2['close'] - c2['open'])
            c2_body_percent = c2_body / c2['open'] if c2['open'] > 0 else 0
            
            if c2_body_percent > 0.01:  # 1% 이하여야 작은 몸통
                return {'confidence': 0, 'reason': f"N-1 몸통이 너무 큼 ({c2_body_percent*100:.1f}%)"}
            
            # ========== 필수조건 3: N은 음봉 ==========
            c3_is_bearish = c3['close'] < c3['open']
            if not c3_is_bearish:
                return {'confidence': 0, 'reason': "N이 음봉 아님"}
            
            # ========== 필수조건 4: N이 N-2 양봉 50% 이상 하락 ==========
            c3_body = abs(c3['open'] - c3['close'])
            recovery_ratio = c3_body / c1_body if c1_body > 0 else 0
            
            if recovery_ratio < 0.5:
                return {'confidence': 0, 'reason': f"50% 하락 미달 ({recovery_ratio*100:.0f}%)"}
            
            # ========== 모든 조건 충족! ==========
            logger.info(f"[석양형] 패턴 감지! 양봉{c1_body_percent*100:.1f}%, 도지{c2_body_percent*100:.2f}%, 하락{recovery_ratio*100:.0f}%")
            
            return {
                'confidence': 1.0,
                'pattern_high': max(c1['high'], c2['high'], c3['high']),
                'doji_high': c2['high'],  # SL 기준점
                'reason': f"석양형: 양봉{c1_body_percent*100:.1f}%, 도지{c2_body_percent*100:.2f}%, 하락{recovery_ratio*100:.0f}%"
            }
            
        except Exception as e:
            return {'confidence': 0, 'reason': f"분석 오류: {e}"}
    
    def check_exit(
        self,
        current_price: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        **kwargs
    ) -> Tuple[bool, str, Optional[str]]:
        """단순화된 청산 조건 체크 (Phase 4: 분할청산 제거) - SHORT"""
        try:
            # 숏 수익 계산: (진입가 - 현재가) / 진입가
            profit_pct = (entry_price - current_price) / entry_price
            
            # 손절 체크 (SHORT: 현재가 >= 손절가)
            if current_price >= stop_loss:
                return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", "stop_loss"
            
            # 익절 체크 (SHORT: 현재가 <= 익절가)
            if current_price <= take_profit:
                return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", "take_profit"
            
            return False, f"보유중 ({profit_pct*100:+.1f}%)", None
            
        except Exception as e:
            logger.error(f"[석양형] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
evening_star_strategy = EveningStarStrategy(timeframe="day")
