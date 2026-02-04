"""
Bearish Divergence Strategy (하락 다이버전스 전략) - SHORT

가격은 고점을 높이는데(Higher High), RSI 지표는 고점을 낮추는(Lower High) 현상을 감지.
강력한 하락 반전 신호로 해석됨.
"""
import pandas as pd
from typing import Tuple, Optional, Dict
from utils.pattern_utils import (
    find_local_maxima,
    calculate_rsi,
    calculate_macd
)
from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_short, ensure_min_rr_short

logger = setup_logger(__name__)


class BearishDivergenceStrategy:
    """하락 다이버전스 전략 (숏 진입)"""
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "하락 다이버전스"
    
    def analyze(
        self,
        df: pd.DataFrame,
        market: str = ""
    ) -> Tuple[bool, float, Optional[Dict]]:
        """
        하락 다이버전스 분석 (Bearish Divergence) - 숏
        
        조건 (모두 AND):
        1. 가격 HH: Current_High > Previous_Peak_High
        2. RSI LH: Current_RSI < Previous_Peak_RSI
        3. 과매수: Previous_RSI >= 70
        4. 트리거: RSI 하락 or 음봉 마감
        
        SL: Current_High + ATR×1.0
        TP: Fib 0.5 + ATR×0.2
        """
        try:
            if len(df) < 30:
                return False, 0.0, {}
            
            # 마감된 캔들만 분석
            df = df.iloc[:-1].copy()
            
            # RSI 계산
            rsi = calculate_rsi(df)
            df = df.copy()
            df['rsi'] = rsi
            
            # 최근 30캔들 감시
            lookback = min(30, len(df))
            df_recent = df.tail(lookback).copy()
            
            # 고점 찾기 (window=7)
            price_highs = find_local_maxima(df_recent['high'], window=7)
            
            if len(price_highs) < 2:
                return False, 0.0, {}
            
            # 최근 2개 고점
            recent_highs = price_highs[-2:]
            
            # ========== ① 가격 Higher High ==========
            price_high1 = df_recent['high'].iloc[recent_highs[0]]
            price_high2 = df_recent['high'].iloc[recent_highs[1]]
            
            if price_high2 <= price_high1:
                return False, 0.0, {}
            
            # ========== ② RSI Lower High ==========
            rsi_high1 = df_recent['rsi'].iloc[recent_highs[0]]
            rsi_high2 = df_recent['rsi'].iloc[recent_highs[1]]
            
            if rsi_high2 >= rsi_high1:
                return False, 0.0, {}
            
            # ========== ③ 과매수 필터 (이전 RSI >= 65) ==========
            if rsi_high1 < 65:
                return False, 0.0, {}
            
            # ========== ④ 트리거: RSI 하락 or 음봉 마감 ==========
            last_candle = df.iloc[-1]
            current_rsi = rsi.iloc[-1]
            prev_rsi = rsi.iloc[-2] if len(rsi) > 1 else current_rsi
            
            is_bearish = last_candle['close'] < last_candle['open']
            rsi_falling = current_rsi < prev_rsi
            
            if not (is_bearish or rsi_falling):
                return False, 0.0, {}
            
            # ========== 모든 조건 충족! SL/TP 계산 ==========
            import ta
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            # 현재 고점 & 30캔들 최저점
            current_high = price_high2
            recent_low = df_recent['low'].min()
            entry_price = last_candle['close']
            
            # SL 기준: Current High
            sl_base = current_high
            
            # TP 기준: Fib 0.5 되돌림
            pattern_tp = recent_low + ((current_high - recent_low) * 0.5)
            
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
                'price_high1': price_high1,
                'price_high2': price_high2,
                'rsi_high1': rsi_high1,
                'rsi_high2': rsi_high2,
                'divergence_high': price_high2,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk,
                'reason': f"하락다이버전스: 가격HH, RSI LH({rsi_high1:.0f}→{rsi_high2:.0f})"
            }
            
            logger.info(f"[하락다이버전스] 감지! 가격 {price_high1:.0f}→{price_high2:.0f}, RSI {rsi_high1:.0f}→{rsi_high2:.0f}")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[하락다이버전스] 분석 오류: {e}")
            return False, 0.0, {}
    
    def _calculate_confidence(
        self,
        df: pd.DataFrame,
        current_rsi: float,
        high_idx: int
    ) -> float:
        """신뢰도 계산"""
        confidence = 0.20  # 기본 20%
        
        # 1. RSI 과매수 깊이 (최대 +30%)
        if current_rsi > 70:
            confidence += 0.30
        elif current_rsi > 60:
            confidence += 0.15
        
        # 2. MACD 동시 다이버전스 확인 (최대 +25%)
        try:
            macd, macd_signal, _ = calculate_macd(df)
            macd_highs = find_local_maxima(macd, window=3)
            
            if len(macd_highs) >= 2:
                macd_high1 = macd.iloc[macd_highs[-2]]
                macd_high2 = macd.iloc[macd_highs[-1]]
                
                if macd_high2 < macd_high1:  # MACD도 Lower High
                    confidence += 0.25
        except Exception:
            pass
        
        # 3. 거래량 증가 (최대 +20%)
        try:
            volume_ma20 = df['volume'].rolling(20).mean().iloc[-1]
            current_volume = df['volume'].iloc[-1]
            
            if current_volume > volume_ma20:
                vol_ratio = min((current_volume / volume_ma20 - 1), 1.0)
                confidence += 0.20 * vol_ratio
        except Exception:
            pass
        
        # 4. 저항선 근접 (최대 +25%)
        try:
            ma50 = df['close'].rolling(50).mean().iloc[-1]
            current_price = df['close'].iloc[-1]
            
            # 볼린저 밴드 상단
            ma20 = df['close'].rolling(20).mean().iloc[-1]
            std20 = df['close'].rolling(20).std().iloc[-1]
            bb_upper = ma20 + 2 * std20
            
            if current_price > ma50 * 0.98 or current_price > bb_upper * 0.98:
                confidence += 0.25
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
        """단순화된 청산 조건 체크 (Phase 4: 분할청산 제거) - SHORT"""
        try:
            profit_pct = (entry_price - current_price) / entry_price
            
            if current_price >= stop_loss:
                return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", "stop_loss"
            
            if current_price <= take_profit:
                return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", "take_profit"
            
            return False, f"보유중 ({profit_pct*100:+.1f}%)", None
            
        except Exception as e:
            logger.error(f"[하락다이버전스] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
bearish_divergence_strategy = BearishDivergenceStrategy()
