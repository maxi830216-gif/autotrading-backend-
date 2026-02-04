"""
Shooting Star Strategy (유성형) - SHORT
긴 윗꼬리 + 작은 몸통 패턴으로 상승추세 끝에서 하락 반전 신호
"""
from typing import Optional, Tuple, Dict
from dataclasses import dataclass
import pandas as pd
import ta

from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_short, ensure_min_rr_short

logger = setup_logger(__name__)


@dataclass
class ShootingStarSignal:
    """Signal data for Shooting Star strategy"""
    market: str
    signal_type: str
    confidence: float
    pattern_high: Optional[float] = None
    rsi: float = 0.0
    current_price: float = 0.0
    reason: str = ""


class ShootingStarStrategy:
    """
    유성형 전략 (SHORT)
    윗꼬리 ≥ 몸통×2, 아래꼬리 ≤ 몸통×0.5
    """
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "유성형"
    
    def analyze(self, df: pd.DataFrame, market: str = "") -> Tuple[bool, float, Optional[Dict]]:
        """
        유성형 패턴 분석
        
        Returns:
            (is_signal, confidence, info_dict)
        """
        try:
            if df is None or len(df) < 20:
                return False, 0.0, {}
            
            # 마감된 캔들만 분석
            df = df.iloc[:-1].copy()
            
            # Calculate indicators
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            
            # Check last candle for shooting star pattern
            pattern_result = self._detect_shooting_star(df)
            
            if pattern_result['confidence'] == 0:
                return False, 0.0, {}
            
            # ATR 계산 for SL/TP buffer
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            current_price = df['close'].iloc[-1]
            pattern_high = pattern_result.get('pattern_high', current_price)
            pattern_low = pattern_result.get('pattern_low', current_price * 0.95)
            
            # SL/TP 기준 (SHORT)
            entry_price = current_price
            candle_length = pattern_high - pattern_low  # 캔들 전체 길이
            
            sl_base = pattern_high  # SL 기준: 유성형 High
            pattern_tp = entry_price - candle_length  # 패턴 기반 TP: 캔들 길이만큼 하락
            
            # ★ 버퍼 적용 (숏)
            stop_loss = sl_base + (current_atr * 1.0)  # SL: 기준 + ATR×1.0
            tp_base = pattern_tp + (current_atr * 0.2)  # TP: 기준 + ATR×0.2
            
            # ★ R:R 1:1.5 보장 (패턴 TP와 비교하여 더 좋은 값 선택)
            take_profit = ensure_min_rr_short(entry_price, stop_loss, tp_base)
            
            # ★ SHORT 불변조건 가드
            is_valid, risk, error_msg = validate_and_calculate_short(
                entry_price, stop_loss, take_profit, self.name
            )
            if not is_valid:
                return False, 0.0, {}
            
            info = {
                'pattern_high': pattern_high,
                'pattern_low': pattern_low,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk,
                'reason': pattern_result['reason']
            }
            
            logger.info(f"[유성형] 감지!")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[유성형] 분석 오류: {e}")
            return False, 0.0, None
    
    def _detect_shooting_star(self, df: pd.DataFrame) -> dict:
        """
        유성형 패턴 감지 (Shooting Star) - 숏
        
        진입조건 (모두 필수):
        1. 상승 추세 고점
        2. 긴 윗꼬리 + 아래 몸통
        3. 다음캔들 음봉 or 저점 이탈
        """
        if len(df) < 5:
            return {'confidence': 0, 'reason': "데이터 부족"}
        
        try:
            # 마지막 2개 캔들 분석 (패턴 + 확인캔들)
            pattern_candle = df.iloc[-2]  # 유성형 캔들
            confirm_candle = df.iloc[-1]  # 확인 캔들
            
            # 캔들 구조 계산
            open_price = pattern_candle['open']
            close = pattern_candle['close']
            high = pattern_candle['high']
            low = pattern_candle['low']
            
            body = abs(close - open_price)
            upper_shadow = high - max(open_price, close)
            lower_shadow = min(open_price, close) - low
            
            if body < open_price * 0.001:
                return {'confidence': 0, 'reason': "몸통이 너무 작음"}
            
            # ========== 필수조건 1: 상승 추세 ==========
            ma20 = df['close'].rolling(20).mean().iloc[-1]
            if pattern_candle['close'] <= ma20:
                return {'confidence': 0, 'reason': "상승추세 아님 (Close <= MA20)"}
            
            # ========== 필수조건 2: 윗꼬리 >= 몸통×2 ==========
            upper_ratio = upper_shadow / body if body > 0 else 0
            if upper_ratio < 2.0:
                return {'confidence': 0, 'reason': f"윗꼬리 부족 ({upper_ratio:.1f}x < 2x)"}
            
            # ========== 필수조건 3: 아래꼬리 짧음 (< 몸통×0.5) ==========
            lower_ratio = lower_shadow / body if body > 0 else 0
            if lower_ratio > 0.5:
                return {'confidence': 0, 'reason': f"아래꼬리 너무 김 ({lower_ratio:.1f}x > 0.5x)"}
            
            # ========== 필수조건 4: 확인 (다음캔들 음봉 or 저점 이탈) ==========
            confirm_is_bearish = confirm_candle['close'] < confirm_candle['open']
            confirm_breaks_low = confirm_candle['close'] < low
            
            if not (confirm_is_bearish or confirm_breaks_low):
                return {'confidence': 0, 'reason': "확인 실패 (음봉도 아니고 저점이탈도 아님)"}
            
            # ========== 모든 조건 충족! ==========
            logger.info(f"[유성형] 패턴 감지! 윗꼬리{upper_ratio:.1f}x, 아래꼬리{lower_ratio:.1f}x")
            
            return {
                'confidence': 1.0,
                'pattern_high': high,
                'pattern_low': low,
                'reason': f"유성형: 윗꼬리{upper_ratio:.1f}x, {'음봉확인' if confirm_is_bearish else '저점이탈'}"
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
            profit_pct = (entry_price - current_price) / entry_price
            
            if current_price >= stop_loss:
                return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", "stop_loss"
            
            if current_price <= take_profit:
                return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", "take_profit"
            
            return False, f"보유중 ({profit_pct*100:+.1f}%)", None
            
        except Exception as e:
            logger.error(f"[유성형] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
shooting_star_strategy = ShootingStarStrategy(timeframe="day")
