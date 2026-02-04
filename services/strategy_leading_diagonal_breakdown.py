"""
Leading Diagonal Breakdown Strategy (리딩다이아 하단이탈) - SHORT
하락 쐐기 패턴의 하단 지지선 이탈 시 숏 진입
"""
from typing import Optional, Tuple, Dict
import pandas as pd
import numpy as np
import ta

from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_short, ensure_min_rr_short

logger = setup_logger(__name__)


class LeadingDiagonalBreakdownStrategy:
    """
    리딩다이아 하단이탈 전략 (SHORT)
    상승 쐐기 패턴의 하단 지지선 이탈 시 숏 진입
    """
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "리딩다이아 하단이탈"
    
    def analyze(self, df: pd.DataFrame, market: str = "") -> Tuple[bool, float, Optional[Dict]]:
        """
        리딩다이아 하단이탈 분석 (Rising Wedge Breakdown) - 숏
        
        조건:
        1. 패턴: 상승 쐐기 (고점↑ 저점↑ 수렴)
        2. 트리거: Close < 지지 추세선
        3. 거래량: 이탈 캔들 거래량 > 평균
        
        SL: Recent High + ATR×1.0
        TP: Fib 0.5 + ATR×0.2
        """
        try:
            if df is None or len(df) < 30:
                return False, 0.0, {}
            
            # 마감된 캔들만 분석
            df = df.iloc[:-1].copy()
            
            # Calculate indicators
            df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
            
            # 쐐기 패턴 감지
            pattern_result = self._detect_wedge_breakdown(df)
            
            if pattern_result['confidence'] == 0:
                return False, 0.0, {}
            
            # ATR 계산
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            # 패턴 정보
            peak = pattern_result.get('peak', df['high'].max())  # 패턴 최고점
            start = pattern_result.get('start', df['low'].min())  # 패턴 시작점
            wedge_range = peak - start
            
            # ★ wedge_range <= 0 검증
            if wedge_range <= 0:
                return False, 0.0, {}
            
            entry_price = df['close'].iloc[-1]
            
            # SL 기준: Recent High
            swing_high = df['high'].tail(10).max()
            sl_base = swing_high
            
            # TP 기준: Fib 0.5 되돌림 = Start + (Range × 0.5)
            pattern_tp = start + (wedge_range * 0.5)
            
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
                'support': pattern_result.get('support'),
                'resistance': pattern_result.get('resistance'),
                'peak': peak,
                'start': start,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk,
                'reason': pattern_result['reason']
            }
            
            logger.info(f"[리딩다이아 하단이탈] 감지!")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[리딩다이아 하단이탈] 분석 오류: {e}")
            return False, 0.0, {}
    
    def _detect_wedge_breakdown(self, df: pd.DataFrame) -> dict:
        """
        상승 쐐기 하향 이탈 패턴 감지
        
        조건 (모두 필수):
        1. 상승 쐐기: 고점↑ 저점↑ 수렴
        2. 트리거: Close < 지지 추세선
        3. 거래량: 이탈 캔들 > 평균
        """
        if len(df) < 20:
            return {'confidence': 0, 'reason': "데이터 부족"}
        
        try:
            # 최근 20일 데이터 분석
            recent = df.tail(20).copy()
            
            # 고점과 저점의 추세선 계산
            highs = recent['high'].values
            lows = recent['low'].values
            x = np.arange(len(highs))
            
            # 선형 회귀로 추세선 계산
            high_slope, high_intercept = np.polyfit(x, highs, 1)
            low_slope, low_intercept = np.polyfit(x, lows, 1)
            
            # ========== ① 상승 쐐기 패턴 ==========
            # 고점 상승 + 저점 상승 + 수렴 (저점 기울기 > 고점 기울기*0.8)
            if high_slope <= 0:
                return {'confidence': 0, 'reason': "고점 상승 아님"}
            if low_slope <= 0:
                return {'confidence': 0, 'reason': "저점 상승 아님"}
            if low_slope <= high_slope * 0.8:
                return {'confidence': 0, 'reason': "수렴 아님"}
            
            # 현재 지지선/저항선 레벨
            current_support = low_slope * (len(x) - 1) + low_intercept
            current_resistance = high_slope * (len(x) - 1) + high_intercept
            current_price = df['close'].iloc[-1]
            
            # ========== ② 지지선 하향 이탈 ==========
            if current_price >= current_support:
                return {'confidence': 0, 'reason': "지지선 이탈 안함"}
            
            # ========== ③ 거래량 증가 ==========
            vol_ma = df['volume'].tail(10).mean()
            curr_vol = df['volume'].iloc[-1]
            
            if curr_vol <= vol_ma:
                return {'confidence': 0, 'reason': f"거래량 미증가 ({curr_vol:.0f} <= {vol_ma:.0f})"}
            
            # ========== 모든 조건 충족! ==========
            peak = recent['high'].max()  # 패턴 최고점
            start = recent['low'].iloc[0]  # 패턴 시작점
            vol_ratio = curr_vol / vol_ma
            
            logger.info(f"[리딩다이아 하단이탈] 패턴 감지! 거래량 {vol_ratio:.1f}x")
            
            return {
                'confidence': 1.0,
                'support': current_support,
                'resistance': current_resistance,
                'peak': peak,
                'start': start,
                'reason': f"리딩다이아 하단이탈: 거래량{vol_ratio:.1f}x"
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
            logger.error(f"[리딩다이아 하단이탈] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
leading_diagonal_breakdown_strategy = LeadingDiagonalBreakdownStrategy(timeframe="day")
