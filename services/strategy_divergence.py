"""
Bullish Divergence Strategy (상승 다이버전스 전략)

가격은 저점을 낮추는데(Lower Low), RSI/MACD 지표는 저점을 높이는(Higher Low) 현상을 감지.
강력한 상승 반전 신호로 해석됨.
"""
import pandas as pd
from typing import Tuple, Optional, Dict
from utils.pattern_utils import (
    find_local_minima,
    calculate_rsi,
    calculate_macd
)
from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_long

logger = setup_logger(__name__)


class BullishDivergenceStrategy:
    """상승 다이버전스 전략"""
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "상승 다이버전스"
    
    def analyze(
        self,
        df: pd.DataFrame,
        market: str = ""
    ) -> Tuple[bool, float, Optional[Dict]]:
        """
        상승 다이버전스 분석 (Bullish Divergence) - 롱
        
        조건 (모두 AND):
        1. 가격 LL: Current_Low < Previous_Low
        2. RSI HL: Current_RSI > Previous_RSI
        3. 과매도: RSI <= 30
        4. 트리거: RSI 상승 or 양봉 마감
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
            
            # 저점 찾기 (window=7)
            price_lows = find_local_minima(df_recent['low'], window=7)
            
            if len(price_lows) < 2:
                return False, 0.0, {}
            
            # 최근 2개 저점
            recent_lows = price_lows[-2:]
            
            # ========== ① 가격 Lower Low ==========
            price_low1 = df_recent['low'].iloc[recent_lows[0]]
            price_low2 = df_recent['low'].iloc[recent_lows[1]]
            
            if price_low2 >= price_low1:
                return False, 0.0, {}
            
            # ========== ② RSI Higher Low ==========
            rsi_low1 = df_recent['rsi'].iloc[recent_lows[0]]
            rsi_low2 = df_recent['rsi'].iloc[recent_lows[1]]
            
            if rsi_low2 <= rsi_low1:
                return False, 0.0, {}
            
            # ========== ③ 현재가 > 저점 ==========
            current_price = df['close'].iloc[-1]
            if current_price <= price_low2:
                return False, 0.0, {}
            
            # ========== ④ 확인 캔들: 양봉 ==========
            last_candle = df.iloc[-1]
            is_bullish = last_candle['close'] > last_candle['open']
            if not is_bullish:
                return False, 0.0, {}
            
            # ========== ⑤ RSI 반등 확인 ==========
            current_rsi = rsi.iloc[-1]
            prev_rsi = rsi.iloc[-2] if len(rsi) > 1 else current_rsi
            if current_rsi <= prev_rsi:
                return False, 0.0, {}
            
            # ========== 모든 조건 충족! SL/TP 계산 ==========
            import ta
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            entry_price = current_price
            sl_base = price_low2
            sl_distance = entry_price - sl_base
            tp_base = entry_price + (sl_distance * 2)
            
            # ★ 버퍼 적용 (롱)
            stop_loss = sl_base - (current_atr * 1.0)  # SL: 기준 - ATR×1.0
            take_profit = tp_base - (current_atr * 0.2)  # TP: 기준 - ATR×0.2
            
            # ★ LONG 불변조건 가드
            is_valid, risk, error_msg = validate_and_calculate_long(
                entry_price, stop_loss, take_profit, self.name
            )
            if not is_valid:
                return False, 0.0, {}
            
            info = {
                'price_low1': price_low1,
                'price_low2': price_low2,
                'rsi_low1': rsi_low1,
                'rsi_low2': rsi_low2,
                'divergence_low': price_low2,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk
            }
            
            logger.info(f"[다이버전스] 감지! 가격 {price_low1:.0f}→{price_low2:.0f}, RSI {rsi_low1:.1f}→{rsi_low2:.1f}")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[다이버전스] 분석 오류: {e}")
            return False, 0.0, {}
    
    def _calculate_confidence(
        self,
        df: pd.DataFrame,
        current_rsi: float,
        low_idx: int
    ) -> float:
        """신뢰도 계산"""
        confidence = 0.20  # 기본 20% (필수 조건 충족)
        
        # 1. RSI 과매도 깊이 (최대 +30%)
        if current_rsi < 30:
            confidence += 0.30
        elif current_rsi < 40:
            confidence += 0.15
        
        # 2. MACD 동시 다이버전스 확인 (최대 +25%)
        try:
            macd, macd_signal, _ = calculate_macd(df)
            macd_lows = find_local_minima(macd, window=3)
            
            if len(macd_lows) >= 2:
                macd_low1 = macd.iloc[macd_lows[-2]]
                macd_low2 = macd.iloc[macd_lows[-1]]
                
                if macd_low2 > macd_low1:  # MACD도 Higher Low
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
        
        # 4. 지지선 근접 (최대 +25%)
        try:
            ma50 = df['close'].rolling(50).mean().iloc[-1]
            current_price = df['close'].iloc[-1]
            
            # 볼린저 밴드 하단
            ma20 = df['close'].rolling(20).mean().iloc[-1]
            std20 = df['close'].rolling(20).std().iloc[-1]
            bb_lower = ma20 - 2 * std20
            
            if current_price < ma50 * 1.02 or current_price < bb_lower * 1.02:
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
        """
        단순화된 청산 조건 체크 (Phase 4: 분할청산 제거)
        
        Args:
            current_price: 현재가
            entry_price: 진입가
            stop_loss: 손절가
            take_profit: 익절가
            
        Returns:
            (청산여부, 사유, exit_type)
        """
        try:
            profit_pct = (current_price - entry_price) / entry_price
            
            # 손절 체크 (LONG: 현재가 <= 손절가)
            if current_price <= stop_loss:
                return True, f"손절: SL 도달 ({profit_pct*100:+.1f}%)", "stop_loss"
            
            # 익절 체크 (LONG: 현재가 >= 익절가)
            if current_price >= take_profit:
                return True, f"익절: TP 도달 ({profit_pct*100:+.1f}%)", "take_profit"
            
            return False, f"보유중 ({profit_pct*100:+.1f}%)", None
            
        except Exception as e:
            logger.error(f"[다이버전스] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
divergence_strategy = BullishDivergenceStrategy()
