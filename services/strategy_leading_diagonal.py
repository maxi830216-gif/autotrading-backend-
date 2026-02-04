"""
Leading Diagonal Strategy (리딩 다이아고날 전략)

엘리어트 파동 이론의 1파/A파 시작에서 나타나는 쐐기형 패턴.
하락 추세의 끝에서 하락 쐐기(Falling Wedge) 패턴을 감지하고
상단 돌파 시 매수.
"""
import pandas as pd
from typing import Tuple, Optional, Dict
from utils.pattern_utils import (
    detect_falling_wedge,
    detect_breakout,
    calculate_rsi,
    calculate_macd
)
from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_long, ensure_min_rr_long

logger = setup_logger(__name__)


class LeadingDiagonalStrategy:
    """리딩 다이아고날 전략"""
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "리딩 다이아고날"
    
    def analyze(
        self,
        df: pd.DataFrame,
        market: str = ""
    ) -> Tuple[bool, float, Optional[Dict]]:
        """
        리딩 다이아고날 (폴링 웻지) 패턴 분석
        
        진입조건:
        1. 고점/저점 수렴 (폴링 웻지)
        2. 상단 저항선 양봉 돌파
        """
        try:
            if len(df) < 30:
                return False, 0.0, {}
            
            # 마감된 캔들만 분석
            df = df.iloc[:-1].copy()
            
            # ========== 필수조건 1: 하락 쐐기 패턴 ==========
            is_wedge, wedge_info = detect_falling_wedge(df, lookback=20)
            
            if not is_wedge:
                return False, 0.0, {}
            
            # ========== 필수조건 2: 상단 돌파 ==========
            resistance = wedge_info['resistance']
            is_breakout = detect_breakout(df, resistance, lookback=3)
            
            if not is_breakout:
                return False, 0.0, {}
            
            # ========== 필수조건 3: 양봉 돌파 ==========
            last_candle = df.iloc[-1]
            is_bullish = last_candle['close'] > last_candle['open']
            
            if not is_bullish:
                return False, 0.0, {}
            
            # ========== 모든 조건 충족! SL/TP 계산 ==========
            import ta
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            entry_price = last_candle['close']
            wedge_height = wedge_info.get('wedge_width', resistance - wedge_info['support'])
            
            # ★ wedge_height <= 0 검증
            if wedge_height <= 0:
                return False, 0.0, {}
            
            # SL/TP 기준
            sl_base = wedge_info['support']  # 하단라인
            pattern_tp = entry_price + wedge_height  # 패턴 기반 TP: 웻지 입구 크기
            
            # ★ 버퍼 적용 (롱)
            stop_loss = sl_base - (current_atr * 1.0)
            tp_base = pattern_tp - (current_atr * 0.2)
            
            # ★ R:R 1:1.5 보장 (패턴 TP와 비교하여 더 좋은 값 선택)
            take_profit = ensure_min_rr_long(entry_price, stop_loss, tp_base)
            
            # ★ LONG 불변조건 가드
            is_valid, risk, error_msg = validate_and_calculate_long(
                entry_price, stop_loss, take_profit, self.name
            )
            if not is_valid:
                return False, 0.0, {}
            
            info = {
                'resistance': resistance,
                'support': wedge_info['support'],
                'wedge_width': wedge_height,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk
            }
            
            logger.info(f"[리딩다이아] 폴링웻지 돌파! 저항{resistance:.0f}, 지지{wedge_info['support']:.0f}")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[리딩다이아] 분석 오류: {e}")
            return False, 0.0, None
            
        except Exception as e:
            logger.error(f"[리딩다이아] 분석 오류: {e}")
            return False, 0.0, None
    
    def _calculate_confidence(self, df: pd.DataFrame, wedge_info: dict) -> float:
        """신뢰도 계산"""
        confidence = 0.20  # 기본 20%
        
        # 1. RSI 과매도 탈출 (최대 +30%)
        try:
            rsi = calculate_rsi(df)
            rsi_prev = rsi.iloc[-2]
            rsi_now = rsi.iloc[-1]
            
            if rsi_prev < 40 and rsi_now > rsi_prev:
                gain = min((rsi_now - rsi_prev) / 20, 1.0)
                confidence += 0.30 * gain
        except Exception:
            pass
        
        # 2. 거래량 증가 (최대 +25%)
        try:
            volume_ma20 = df['volume'].rolling(20).mean().iloc[-1]
            current_volume = df['volume'].iloc[-1]
            
            if current_volume > volume_ma20 * 1.5:
                confidence += 0.25
            elif current_volume > volume_ma20:
                confidence += 0.15
        except Exception:
            pass
        
        # 3. MA20 하단에서 반등 (최대 +25%)
        try:
            ma20 = df['close'].rolling(20).mean().iloc[-1]
            price_prev = df['close'].iloc[-2]
            price_now = df['close'].iloc[-1]
            
            if price_prev < ma20 and price_now > price_prev:
                confidence += 0.25
        except Exception:
            pass
        
        # 4. MACD 골든크로스 (최대 +20%)
        try:
            macd, signal, _ = calculate_macd(df)
            macd_now = macd.iloc[-1]
            macd_prev = macd.iloc[-2]
            signal_now = signal.iloc[-1]
            signal_prev = signal.iloc[-2]
            
            if macd_now > signal_now and macd_prev < signal_prev:
                confidence += 0.20
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
            logger.error(f"[리딩다이아] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
leading_diagonal_strategy = LeadingDiagonalStrategy()
