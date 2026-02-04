"""
Bearish Engulfing Strategy (하락장악형) - SHORT
양봉을 완전히 감싸는 큰 음봉 패턴으로 하락 반전 신호
"""
from typing import Optional, Tuple, Dict
import pandas as pd
import ta

from utils.logger import setup_logger
from services.strategy_utils import validate_and_calculate_short, ensure_min_rr_short

logger = setup_logger(__name__)


class BearishEngulfingStrategy:
    """
    하락장악형 전략 (SHORT)
    이전 양봉을 완전히 감싸는 큰 음봉 패턴
    """
    
    def __init__(self, timeframe: str = "day"):
        self.timeframe = timeframe
        self.name = "하락장악형"
    
    def analyze(self, df: pd.DataFrame, market: str = "") -> Tuple[bool, float, Optional[Dict]]:
        """
        하락장악형 패턴 분석
        
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
            
            # Check for Bearish Engulfing pattern
            pattern_result = self._detect_bearish_engulfing(df)
            
            if pattern_result['confidence'] == 0:
                return False, 0.0, {}
            
            # ATR 계산 for SL/TP buffer
            atr_indicator = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
            current_atr = atr_indicator.average_true_range().iloc[-1]
            
            current_price = df['close'].iloc[-1]
            pattern_high = pattern_result.get('pattern_high', current_price)
            
            # 직전 상승 파동 찾기 (피보나치 되돌림용)
            recent_low = df['low'].tail(20).min()
            
            # TP 기준: 피보나치 0.618 되돌림 지점
            pattern_tp = pattern_high - ((pattern_high - recent_low) * 0.618)
            
            # SL 기준: 현재 음봉(N)의 High
            sl_base = pattern_high
            entry_price = current_price
            
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
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'atr': current_atr,
                'risk': risk,
                'reason': pattern_result['reason']
            }
            
            logger.info(f"[하락장악형] 감지!")
            
            return True, 1.0, info
            
        except Exception as e:
            logger.error(f"[하락장악형] 분석 오류: {e}")
            return False, 0.0, None
    
    def _detect_bearish_engulfing(self, df: pd.DataFrame) -> dict:
        """
        하락장악형 패턴 감지 (Bearish Engulfing) - 숏
        
        진입조건 (모두 필수):
        1. Prev=양봉, Curr=음봉
        2. 장악: Curr.Open >= Prev.Close AND Curr.Close < Prev.Open
        3. 추세: SMA20 위 OR RSI >= 60
        4. 거래량: Curr.Vol > Prev.Vol
        """
        if len(df) < 20:
            return {'confidence': 0, 'reason': "데이터 부족"}
        
        try:
            # 마지막 2개 캔들 분석
            prev = df.iloc[-2]
            curr = df.iloc[-1]
            
            # ========== ① 캔들 색상 조건 ==========
            # Prev: 양봉
            if prev['close'] <= prev['open']:
                return {'confidence': 0, 'reason': "Prev가 양봉 아님"}
            
            # Curr: 음봉
            if curr['close'] >= curr['open']:
                return {'confidence': 0, 'reason': "Curr이 음봉 아님"}
            
            # ========== ② 장악 조건 ==========
            # Curr.Open >= Prev.Close (코인은 갭 없으므로 같거나 높음)
            # Curr.Close < Prev.Open (직전 양봉 시가 아래로 마감)
            if curr['open'] < prev['close']:
                return {'confidence': 0, 'reason': f"장악 실패: Curr.Open({curr['open']:.0f}) < Prev.Close({prev['close']:.0f})"}
            
            if curr['close'] >= prev['open']:
                return {'confidence': 0, 'reason': f"장악 실패: Curr.Close({curr['close']:.0f}) >= Prev.Open({prev['open']:.0f})"}
            
            # ========== ③ 추세 조건 (SMA20 위 OR RSI >= 60) ==========
            sma20 = df['close'].rolling(20).mean().iloc[-1]
            rsi = df['rsi'].iloc[-1] if 'rsi' in df.columns else 50
            
            above_sma20 = curr['close'] > sma20
            rsi_overbought = rsi >= 60
            
            if not (above_sma20 or rsi_overbought):
                return {'confidence': 0, 'reason': f"추세 조건 미충족 (SMA20 하단, RSI {rsi:.0f} < 60)"}
            
            # ========== ④ 거래량 조건 ==========
            if curr['volume'] <= prev['volume']:
                return {'confidence': 0, 'reason': f"거래량 미증가 ({curr['volume']:.0f} <= {prev['volume']:.0f})"}
            
            # ========== 모든 조건 충족! ==========
            pattern_high = max(prev['high'], curr['high'])
            vol_ratio = curr['volume'] / prev['volume']
            
            logger.info(f"[하락장악형] 패턴 감지! 거래량 {vol_ratio:.1f}x")
            
            return {
                'confidence': 1.0,
                'pattern_high': pattern_high,
                'reason': f"하락장악형: 거래량{vol_ratio:.1f}x, {'SMA20↑' if above_sma20 else ''}{' RSI' + str(int(rsi)) if rsi_overbought else ''}"
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
            logger.error(f"[하락장악형] check_exit 오류: {e}")
            return False, f"오류: {e}", None


# 싱글톤 인스턴스
bearish_engulfing_strategy = BearishEngulfingStrategy(timeframe="day")
