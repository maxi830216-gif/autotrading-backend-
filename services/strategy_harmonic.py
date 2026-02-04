"""
Harmonic Pattern Strategy (하모닉 패턴 전략)

피보나치 비율을 기반으로 한 기하학적 가격 패턴.
가틀리(Gartley)와 배트(Bat) 패턴을 감지.
"""
import pandas as pd
import numpy as np
from typing import Tuple, Optional, List, Dict
from utils.pattern_utils import (
    find_local_minima,
    find_local_maxima,
    calculate_rsi
)
from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_long

logger = setup_logger(__name__)


class HarmonicPatternStrategy:
    """하모닉 패턴 전략"""
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "하모닉 패턴"
        
        # 가틀리 패턴 피보나치 비율
        self.gartley = {
            'AB_XA': (0.382, 0.618),
            'BC_AB': (0.382, 0.886),
            'CD_BC': (1.272, 1.618),
            'XD_XA': (0.786, 0.786),  # D는 XA의 78.6% 되돌림
        }
        
        # 배트 패턴 피보나치 비율
        self.bat = {
            'AB_XA': (0.382, 0.50),
            'BC_AB': (0.382, 0.886),
            'CD_BC': (1.618, 2.618),
            'XD_XA': (0.886, 0.886),  # D는 XA의 88.6% 되돌림
        }
    
    def analyze(
        self,
        df: pd.DataFrame,
        market: str = ""
    ) -> Tuple[bool, float, Optional[Dict]]:
        """
        하모닉 패턴 분석 (가틀리/배트)
        """
        try:
            if len(df) < 50:
                return False, 0.0, {}
            
            # 마감된 캔들만 분석
            df = df.iloc[:-1].copy()
            
            # XABCD 포인트 찾기
            points = self._find_xabcd_points(df)
            
            if not points:
                return False, 0.0, {}
            
            X, A, B, C, D = points
            
            # 가틀리 패턴 확인
            gartley_score = self._check_pattern(X, A, B, C, D, self.gartley, 'Gartley')
            
            # 배트 패턴 확인
            bat_score = self._check_pattern(X, A, B, C, D, self.bat, 'Bat')
            
            # 더 높은 점수 선택
            if gartley_score > bat_score:
                pattern_name = 'Gartley'
                fib_score = gartley_score
            else:
                pattern_name = 'Bat'
                fib_score = bat_score
            
            if fib_score < 0.8:  # 최소 피보나치 정확도 80%
                return False, 0.0, {}
            
            # ★ 필수조건: D점에서 양봉 반전 확인
            last_candle = df.iloc[-1]
            is_bullish = last_candle['close'] > last_candle['open']
            
            if not is_bullish:
                return False, 0.0, {}
            
            # ========== 모든 조건 충족! SL/TP 계산 ==========
            import ta
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            entry_price = last_candle['close']
            
            # ★ XA 비율 계산 개선 (0 나눗셈 방지)
            xa_distance = A - X  # XA 레그 거리
            if xa_distance == 0:
                return False, 0.0, {}
            
            # SL 기준: X점 이탈 또는 XA의 1.13 확장 레벨
            x_113_extension = X - (abs(xa_distance) * 0.13)
            sl_base = min(X, x_113_extension)
            
            # TP 기준: 진입가에서 SL 거리의 2배 (1:2 Risk-Reward)
            sl_distance = entry_price - sl_base
            tp_base = entry_price + (sl_distance * 2)
            
            # ★ 버퍼 적용 (롱)
            stop_loss = sl_base - (current_atr * 1.0)
            take_profit = tp_base - (current_atr * 0.2)
            
            # ★ LONG 불변조건 가드
            is_valid, risk, error_msg = validate_and_calculate_long(
                entry_price, stop_loss, take_profit, self.name
            )
            if not is_valid:
                return False, 0.0, {}
            
            info = {
                'pattern': pattern_name,
                'X': X, 'A': A, 'B': B, 'C': C, 'D': D,
                'fib_accuracy': fib_score,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk
            }
            
            logger.info(f"[하모닉] {pattern_name} 감지! D={D:.0f}, X={X:.0f}, A={A:.0f}")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[하모닉] 분석 오류: {e}")
            return False, 0.0, {}
    
    def _find_xabcd_points(self, df: pd.DataFrame) -> Optional[List[float]]:
        """XABCD 5개 포인트 찾기 (Bullish 패턴용)"""
        try:
            # 저점 및 고점 찾기
            lows_idx = find_local_minima(df['low'], window=5)
            highs_idx = find_local_maxima(df['high'], window=5)
            
            if len(lows_idx) < 3 or len(highs_idx) < 2:
                return None
            
            # Bullish 패턴: X(저점) -> A(고점) -> B(저점) -> C(고점) -> D(저점)
            # 최근 데이터에서 D점 찾기
            
            # 가장 최근 저점을 D로
            D_idx = lows_idx[-1]
            D = df['low'].iloc[D_idx]
            
            # D 이전의 고점을 C로
            c_candidates = [i for i in highs_idx if i < D_idx]
            if not c_candidates:
                return None
            C_idx = c_candidates[-1]
            C = df['high'].iloc[C_idx]
            
            # C 이전의 저점을 B로
            b_candidates = [i for i in lows_idx if i < C_idx]
            if not b_candidates:
                return None
            B_idx = b_candidates[-1]
            B = df['low'].iloc[B_idx]
            
            # B 이전의 고점을 A로
            a_candidates = [i for i in highs_idx if i < B_idx]
            if not a_candidates:
                return None
            A_idx = a_candidates[-1]
            A = df['high'].iloc[A_idx]
            
            # A 이전의 저점을 X로
            x_candidates = [i for i in lows_idx if i < A_idx]
            if not x_candidates:
                return None
            X_idx = x_candidates[-1]
            X = df['low'].iloc[X_idx]
            
            # ★ 시간 간격 검증: 각 점 사이에 최소 3개 캔들 이상 필요
            MIN_CANDLES_BETWEEN = 3
            if (A_idx - X_idx) < MIN_CANDLES_BETWEEN:
                return None  # X→A 구간이 너무 짧음
            if (B_idx - A_idx) < MIN_CANDLES_BETWEEN:
                return None  # A→B 구간이 너무 짧음
            if (C_idx - B_idx) < MIN_CANDLES_BETWEEN:
                return None  # B→C 구간이 너무 짧음
            if (D_idx - C_idx) < MIN_CANDLES_BETWEEN:
                return None  # C→D 구간이 너무 짧음
            
            return [X, A, B, C, D]
            
        except Exception:
            return None
    
    def _check_pattern(
        self,
        X: float, A: float, B: float, C: float, D: float,
        ratios: dict,
        pattern_name: str
    ) -> float:
        """패턴 피보나치 비율 확인"""
        try:
            XA = A - X
            AB = A - B
            BC = C - B
            CD = C - D
            XD = A - D
            
            scores = []
            
            # AB / XA 비율
            ab_xa = AB / XA if XA != 0 else 0
            min_r, max_r = ratios['AB_XA']
            if min_r <= ab_xa <= max_r:
                scores.append(1.0)
            elif abs(ab_xa - min_r) < 0.1 or abs(ab_xa - max_r) < 0.1:
                scores.append(0.5)
            else:
                scores.append(0.0)
            
            # BC / AB 비율
            bc_ab = BC / AB if AB != 0 else 0
            min_r, max_r = ratios['BC_AB']
            if min_r <= bc_ab <= max_r:
                scores.append(1.0)
            elif abs(bc_ab - min_r) < 0.1 or abs(bc_ab - max_r) < 0.1:
                scores.append(0.5)
            else:
                scores.append(0.0)
            
            # XD / XA 비율 (가장 중요!)
            xd_xa = XD / XA if XA != 0 else 0
            target = ratios['XD_XA'][0]
            if abs(xd_xa - target) <= 0.05:  # 5% 오차
                scores.append(1.0)
            elif abs(xd_xa - target) <= 0.08:  # 8% 오차
                scores.append(0.7)
            else:
                scores.append(0.0)
            
            return np.mean(scores) if scores else 0.0
            
        except Exception:
            return 0.0
    
    def _calculate_confidence(self, df: pd.DataFrame, fib_score: float) -> float:
        """신뢰도 계산"""
        confidence = 0.20  # 기본 20%
        
        # 1. 피보나치 정확도 (최대 +40%)
        confidence += fib_score * 0.40
        
        # 2. RSI 과매도 (최대 +25%)
        try:
            rsi = calculate_rsi(df)
            current_rsi = rsi.iloc[-1]
            if current_rsi < 40:
                confidence += 0.25 * (40 - current_rsi) / 20
        except Exception:
            pass
        
        # 3. 거래량 (최대 +20%)
        try:
            volume_ma20 = df['volume'].rolling(20).mean().iloc[-1]
            current_volume = df['volume'].iloc[-1]
            if current_volume > volume_ma20:
                confidence += 0.20
        except Exception:
            pass
        
        # 4. 반전 캔들 확인 (최대 +15%)
        try:
            last_candle = df.iloc[-1]
            body = abs(last_candle['close'] - last_candle['open'])
            lower_wick = min(last_candle['open'], last_candle['close']) - last_candle['low']
            
            # 해머형 캔들
            if lower_wick > body * 2 and last_candle['close'] > last_candle['open']:
                confidence += 0.15
        except Exception:
            pass
        
        return min(confidence, 1.0)
    
    def check_exit(
        self,
        current_price: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        **kwargs
    ) -> Tuple[bool, str, Optional[str]]:
        """단순화된 청산 조건 체크 (Phase 4: 분할청산 제거)"""
        try:
            profit_pct = (current_price - entry_price) / entry_price
            
            if current_price <= stop_loss:
                return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", "stop_loss"
            
            if current_price >= take_profit:
                return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", "take_profit"
            
            return False, f"보유중 ({profit_pct*100:+.1f}%)", None
            
        except Exception as e:
            logger.error(f"[하모닉] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
harmonic_strategy = HarmonicPatternStrategy()
