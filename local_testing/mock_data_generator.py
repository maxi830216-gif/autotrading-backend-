"""
Mock Data Generator
전략별 테스트 캔들 패턴 생성기
"""
from typing import List, Dict, Optional
import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class CandleConfig:
    """캔들 생성 설정"""
    base_price: float = 50000
    volatility: float = 0.002  # ★ 0.2% 변동성 (패턴 감지 + R:R 충족 균형)
    volume_base: int = 100


class CandlePatternGenerator:
    """
    전략별 캔들 패턴 생성기
    
    각 전략의 진입 조건을 충족하는 캔들 데이터를 생성합니다.
    """
    
    @staticmethod
    def _generate_base_history(config: CandleConfig, count: int = 60) -> List[Dict]:
        """
        기본 히스토리 캔들 생성 (지표 계산용)
        
        약간의 랜덤 변동을 가진 횡보 캔들 생성
        """
        candles = []
        price = config.base_price
        
        for i in range(count):
            # 작은 랜덤 변동
            change = np.random.uniform(-config.volatility/2, config.volatility/2)
            
            open_price = price
            close_price = price * (1 + change)
            # ★ ATR을 작게 유지하기 위해 high-low 범위 최소화
            high_price = max(open_price, close_price) * 1.0001  # 0.01% 위
            low_price = min(open_price, close_price) * 0.9999   # 0.01% 아래
            
            candles.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + np.random.randint(-20, 50)
            })
            
            price = close_price
        
        return candles
    
    @classmethod
    def _ensure_min_candles(cls, candles: List[Dict], min_count: int = 100, config: CandleConfig = None) -> List[Dict]:
        """패턴 앞에 히스토리를 추가하여 최소 캔들 수 보장"""
        config = config or CandleConfig()
        current_count = len(candles)
        
        if current_count >= min_count:
            return candles
        
        # 필요한 만큼 히스토리 추가
        needed = min_count - current_count
        # 보수적으로 더 많이 추가
        history = cls._generate_base_history(config, count=needed + 10)
        
        return history + candles
    
    @staticmethod
    def _generate_upward_candles(start_price: float, count: int, 
                                  step_percent: float = 0.02, config: CandleConfig = None) -> List[Dict]:
        """상승 캔들 시퀀스 생성"""
        config = config or CandleConfig()
        candles = []
        price = start_price
        
        for i in range(count):
            open_price = price
            close_price = price * (1 + step_percent)
            high_price = close_price * 1.005
            low_price = open_price * 0.995
            
            candles.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 50
            })
            
            price = close_price
        
        return candles
    
    @staticmethod
    def _generate_downward_candles(start_price: float, count: int,
                                    step_percent: float = 0.02, config: CandleConfig = None) -> List[Dict]:
        """하락 캔들 시퀀스 생성"""
        config = config or CandleConfig()
        candles = []
        price = start_price
        
        for i in range(count):
            open_price = price
            close_price = price * (1 - step_percent)
            high_price = open_price * 1.005
            low_price = close_price * 0.995
            
            candles.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 50
            })
            
            price = close_price
        
        return candles
    
    @staticmethod
    def _generate_short_exit_candles(start_price: float, count: int,
                                      step_percent: float = 0.02, config: CandleConfig = None,
                                      for_tp: bool = True) -> List[Dict]:
        """
        Short 포지션 청산용 캔들 시퀀스
        - for_tp=True: 하락 캔들 (TP 도달), high를 낮게 유지
        - for_tp=False: 상승 캔들 (SL 도달), low를 높게 유지
        """
        config = config or CandleConfig()
        candles = []
        price = start_price
        
        for i in range(count):
            open_price = price
            
            if for_tp:
                # Short TP: 가격 하락, high는 open과 거의 같게 (SL 회피)
                close_price = price * (1 - step_percent)
                high_price = open_price * 1.001  # 매우 작은 위꼬리
                low_price = close_price * 0.995
            else:
                # Short SL: 가격 상승
                close_price = price * (1 + step_percent)
                high_price = close_price * 1.005
                low_price = open_price * 0.999  # 매우 작은 아래꼬리
            
            candles.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 50
            })
            
            price = close_price
        
        return candles

    # ==========================================
    # 전략별 패턴 생성
    # ==========================================
    
    @classmethod
    def morning_star(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Morning Star 패턴 생성
        
        패턴 구조:
        - c1: 큰 음봉 (하락)
        - c2: 도지/스피닝 탑 (작은 몸통)
        - c3: 큰 양봉 (c1의 50% 이상 회복) → 진입 트리거
        
        Args:
            config: 캔들 설정
            exit_type: "take_profit" or "stop_loss"
        """
        config = config or CandleConfig()
        
        # 1. 히스토리 캔들 (RSI, BB 계산용)
        history = cls._generate_base_history(config, 20)
        last_price = history[-1]['close']
        
        # 2. 하락 추세 (RSI를 낮추기 위해)
        downtrend = cls._generate_downward_candles(last_price, 3, 0.03, config)
        
        # 3. Morning Star 패턴 (3개 캔들)
        c1_open = downtrend[-1]['close']
        c1_close = c1_open * 0.95  # 5% 하락 큰 음봉
        c1 = {
            'open': round(c1_open, 2),
            'high': round(c1_open * 1.005, 2),
            'low': round(c1_close * 0.99, 2),
            'close': round(c1_close, 2),
            'volume': config.volume_base * 2
        }
        
        # c2: 도지 (작은 몸통, 하지만 저점은 낮게 - SL 거리 확보)
        c2_open = c1_close * 1.001
        c2_close = c2_open * 1.002  # 거의 변동 없음
        c2_low = c1_close * 0.92  # ★ 저점을 8% 더 낮게 (SL 거리 확보)
        c2 = {
            'open': round(c2_open, 2),
            'high': round(max(c2_open, c2_close) * 1.003, 2),
            'low': round(c2_low, 2),  # ★ 낮은 저점
            'close': round(c2_close, 2),
            'volume': int(config.volume_base * 0.5)
        }
        
        # c3: 큰 양봉 (c1의 50% 이상 회복)
        c3_open = c2_close
        recovery_target = c1_open  # c1 시가까지 회복
        c3_close = c3_open + (recovery_target - c3_open) * 0.6  # 60% 회복
        c3 = {
            'open': round(c3_open, 2),
            'high': round(c3_close * 1.01, 2),
            'low': round(c3_open * 0.995, 2),
            'close': round(c3_close, 2),
            'volume': config.volume_base * 2
        }
        
        pattern = [c1, c2, c3]
        
        # 4. 이후 캔들 (청산 시나리오에 따라)
        entry_price = c3_close
        
        if exit_type == "take_profit":
            # 상승하여 익절 도달
            exit_candles = cls._generate_upward_candles(entry_price, 3, 0.025, config)
        else:  # stop_loss
            # 하락하여 손절 도달
            exit_candles = cls._generate_downward_candles(entry_price, 2, 0.03, config)
        
        all_candles = history + downtrend + pattern + exit_candles
        all_candles = cls._ensure_min_candles(all_candles, 100, config)
        
        return {
            'name': f"morning_star_{exit_type}",
            'candles': all_candles,
            'pattern_start_idx': len(history) + len(downtrend),
            'entry_idx': len(history) + len(downtrend) + 2,  # c3 다음
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def divergence(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        RSI Divergence 패턴 생성
        
        전략 조건:
        1. df.tail(30)에서 find_local_minima(window=7) → 2개 저점
        2. 가격 LL: price_low2 < price_low1
        3. RSI HL: rsi_low2 > rsi_low1
        4. current_price > price_low2
        5. 양봉 확인 + RSI 반등
        
        핵심: window=7이면 인덱스 7~22 범위에서만 저점 감지됨
        """
        config = config or CandleConfig()
        
        # 전체 캔들: 사전 히스토리 + 30개 분석 캔들 + exit 캔들
        # 30개 분석 캔들 내에서 저점1은 인덱스 10, 저점2는 인덱스 18에 배치
        
        candles = []
        price = config.base_price
        
        # === 사전 히스토리 (20캔들) - RSI 초기화용 ===
        for i in range(20):
            change = np.random.uniform(-0.002, 0.002)
            candles.append({
                'open': round(price, 2),
                'high': round(price * 1.003, 2),
                'low': round(price * 0.997, 2),
                'close': round(price * (1 + change), 2),
                'volume': config.volume_base
            })
            price = candles[-1]['close']
        
        # === 분석 대상 30캔들 시작 ===
        # 저점1은 인덱스 10에, 저점2는 인덱스 18에 위치해야 함
        base_for_30 = price
        
        # 인덱스 0-9: 하락 (저점1 형성 준비)
        low1_price = base_for_30 * 0.85  # 15% 하락
        for i in range(10):
            step = (base_for_30 - low1_price) / 10
            open_price = price
            close_price = price - step
            # low는 close보다 높게 (저점1 캔들까지 유지)
            candles.append({
                'open': round(open_price, 2),
                'high': round(open_price * 1.002, 2),
                'low': round(close_price * 0.998, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 40
            })
            price = close_price
        
        # 인덱스 10: 저점1 (가장 낮은 점 - window=7 내에서)
        low1_candle = {
            'open': round(price, 2),
            'high': round(price * 1.002, 2),
            'low': round(low1_price, 2),  # 명확한 저점
            'close': round(low1_price * 1.003, 2),
            'volume': config.volume_base + 80
        }
        candles.append(low1_candle)
        low1_idx_in_30 = 10
        price = low1_candle['close']
        
        # 인덱스 11-17: 반등 (저점1보다 높은 가격 유지)
        bounce_target = base_for_30 * 0.95
        for i in range(7):
            step = (bounce_target - price) / 7
            open_price = price
            close_price = price + step
            # low는 저점1보다 항상 높게
            low_val = max(open_price * 0.998, low1_price * 1.03)
            candles.append({
                'open': round(open_price, 2),
                'high': round(close_price * 1.003, 2),
                'low': round(low_val, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 30
            })
            price = close_price
        
        # 인덱스 18: 저점2 (저점1보다 낮은 가격 - 가격 LL)
        low2_price = low1_price * 0.97  # 저점1보다 3% 낮음
        low2_candle = {
            'open': round(price, 2),
            'high': round(price * 1.002, 2),
            'low': round(low2_price, 2),  # 저점1보다 낮음
            'close': round(low2_price * 1.003, 2),
            'volume': config.volume_base + 70
        }
        candles.append(low2_candle)
        low2_idx_in_30 = 18
        price = low2_candle['close']
        
        # 인덱스 19-27: 반등 (저점2보다 높게, RSI 상승)
        for i in range(9):
            step = price * 0.012
            open_price = price
            close_price = price + step
            low_val = max(open_price * 0.998, low2_price * 1.02)
            candles.append({
                'open': round(open_price, 2),
                'high': round(close_price * 1.003, 2),
                'low': round(low_val, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 40
            })
            price = close_price
        
        # 인덱스 28: 양봉 확인 캔들 (마지막 마감 캔들)
        confirm_open = price
        confirm_close = price * 1.015  # 양봉
        confirm_candle = {
            'open': round(confirm_open, 2),
            'high': round(confirm_close * 1.005, 2),
            'low': round(confirm_open * 0.998, 2),
            'close': round(confirm_close, 2),
            'volume': config.volume_base + 60
        }
        candles.append(confirm_candle)
        price = confirm_close
        
        # 인덱스 29: 현재 캔들 (미마감 - 전략에서 제외됨)
        current_candle = {
            'open': round(price, 2),
            'high': round(price * 1.01, 2),
            'low': round(price * 0.995, 2),
            'close': round(price * 1.005, 2),
            'volume': config.volume_base
        }
        candles.append(current_candle)
        
        entry_price = confirm_close  # 확인 캔들 종가
        
        # === Exit 캔들 ===
        if exit_type == "take_profit":
            exit_candles = cls._generate_upward_candles(entry_price, 10, 0.025, config)
        else:
            exit_candles = cls._generate_downward_candles(entry_price, 8, 0.03, config)
        
        all_candles = candles + exit_candles
        all_candles = cls._ensure_min_candles(all_candles, 100, config)
        
        return {
            'name': f"divergence_{exit_type}",
            'candles': all_candles,
            'low1_idx': 20 + low1_idx_in_30,  # 전체 인덱스
            'low2_idx': 20 + low2_idx_in_30,
            'entry_idx': len(candles) - 2,  # 확인 캔들
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def harmonic_gartley(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Harmonic Gartley 패턴 생성
        
        전략 조건:
        1. 50개 이상 캔들
        2. find_local_minima/maxima(window=5)로 XABCD 5포인트
        3. 각 점 사이 최소 3캔들 간격
        4. 피보나치 비율 (Gartley)
        5. D점에서 양봉 반전
        
        핵심: 각 포인트가 ±5 범위에서 유일한 최저/최고점이 되어야 함
        """
        config = config or CandleConfig()
        candles = []
        price = config.base_price
        
        # 기준 가격 설정 (R:R 1:1.5 충족을 위해 XA 거리 확보)
        base = config.base_price
        x_price = base * 0.90   # ★ X점 (저점1) - 더 낮게
        xa_move = base * 0.25   # ★ XA 거리 확대 (25%)
        a_price = x_price + xa_move  # A점 (고점1)
        b_price = a_price - (xa_move * 0.618)  # B점 (저점2) - 61.8% 되돌림
        bc_move = (a_price - b_price) * 0.786
        c_price = b_price + bc_move  # C점 (고점2)
        # D점: XD/XA = 0.786이 되어야 함
        # XD = A - D, XA = A - X
        # D = A - (XA * 0.786)
        d_price = a_price - (xa_move * 0.786)  # D점 (저점3)
        
        # === Phase 1: 초기 히스토리 (50캔들) - 100개 이상 충족 ===
        # X점이 local min이 되려면 앞의 5캔들 low가 x_price보다 높아야 함
        for i in range(50):
            candle_price = base * (1 + np.random.uniform(-0.01, 0.01))
            candles.append({
                'open': round(candle_price, 2),
                'high': round(candle_price * 1.01, 2),
                'low': round(x_price * 1.05, 2),  # X점보다 높은 low
                'close': round(candle_price, 2),
                'volume': config.volume_base
            })
        
        # === X점 (저점1) - idx 5 ===
        candles.append({
            'open': round(base, 2),
            'high': round(base * 1.005, 2),
            'low': round(x_price, 2),  # 가장 낮은 저점
            'close': round(x_price * 1.01, 2),
            'volume': config.volume_base + 50
        })
        x_idx = len(candles) - 1
        price = x_price * 1.01
        
        # === X→A 상승 (5캔들) - A점이 local max가 되도록 ===
        for i in range(5):
            step = (a_price - price) / 5
            open_price = price
            close_price = price + step
            # low는 X점보다 높게, high는 A점보다 낮게
            candles.append({
                'open': round(open_price, 2),
                'high': round(a_price * 0.97, 2),  # A점보다 낮은 high
                'low': round(x_price * 1.03, 2),   # X점보다 높은 low
                'close': round(close_price, 2),
                'volume': config.volume_base + 30
            })
            price = close_price
        
        # === A점 (고점1) - idx 11 ===
        candles.append({
            'open': round(price, 2),
            'high': round(a_price, 2),  # 가장 높은 고점
            'low': round(price * 0.995, 2),
            'close': round(a_price * 0.99, 2),
            'volume': config.volume_base + 40
        })
        a_idx = len(candles) - 1
        price = a_price * 0.99
        
        # === A→B 하락 (4캔들) ===
        for i in range(4):
            step = (price - b_price) / 4
            open_price = price
            close_price = price - step
            candles.append({
                'open': round(open_price, 2),
                'high': round(a_price * 0.97, 2),  # A점보다 낮게
                'low': round(b_price * 1.02, 2),   # B점보다 높게
                'close': round(close_price, 2),
                'volume': config.volume_base + 25
            })
            price = close_price
        
        # === B점 (저점2) - idx 16 ===
        candles.append({
            'open': round(price, 2),
            'high': round(price * 1.005, 2),
            'low': round(b_price, 2),  # 두 번째 저점
            'close': round(b_price * 1.01, 2),
            'volume': config.volume_base + 35
        })
        b_idx = len(candles) - 1
        price = b_price * 1.01
        
        # === B→C 상승 (4캔들) ===
        for i in range(4):
            step = (c_price - price) / 4
            open_price = price
            close_price = price + step
            candles.append({
                'open': round(open_price, 2),
                'high': round(c_price * 0.98, 2),  # C점보다 낮게
                'low': round(b_price * 1.02, 2),   # B점보다 높게
                'close': round(close_price, 2),
                'volume': config.volume_base + 20
            })
            price = close_price
        
        # === C점 (고점2) - idx 21 ===
        candles.append({
            'open': round(price, 2),
            'high': round(c_price, 2),  # 두 번째 고점
            'low': round(price * 0.995, 2),
            'close': round(c_price * 0.99, 2),
            'volume': config.volume_base + 30
        })
        c_idx = len(candles) - 1
        price = c_price * 0.99
        
        # === C→D 하락 (5캔들) ===
        for i in range(5):
            step = (price - d_price) / 5
            open_price = price
            close_price = price - step
            candles.append({
                'open': round(open_price, 2),
                'high': round(c_price * 0.98, 2),  # C점보다 낮게
                'low': round(d_price * 1.02, 2),   # D점보다 높게
                'close': round(close_price, 2),
                'volume': config.volume_base + 35
            })
            price = close_price
        
        # === D점 (저점3) - idx 27 ===
        candles.append({
            'open': round(price, 2),
            'high': round(price * 1.005, 2),
            'low': round(d_price, 2),  # 세 번째 저점
            'close': round(d_price * 1.01, 2),
            'volume': config.volume_base + 50
        })
        d_idx = len(candles) - 1
        price = d_price * 1.01
        
        # === D점 다음: 양봉 반전 + 5캔들 (local min 확보) ===
        for i in range(5):
            open_price = price
            close_price = price * 1.015  # 양봉
            candles.append({
                'open': round(open_price, 2),
                'high': round(close_price * 1.005, 2),
                'low': round(d_price * 1.02, 2),  # D점보다 높게
                'close': round(close_price, 2),
                'volume': config.volume_base + 40
            })
            price = close_price
        
        entry_price = price
        
        # === Exit 캔들 ===
        if exit_type == "take_profit":
            exit_candles = cls._generate_upward_candles(entry_price, 10, 0.025, config)
        else:
            exit_candles = cls._generate_downward_candles(entry_price, 8, 0.03, config)
        
        all_candles = candles + exit_candles
        # 주의: _ensure_min_candles 사용 안 함 - exit 캔들 보존
        
        return {
            'name': f"harmonic_{exit_type}",
            'candles': all_candles,
            'x_price': x_price,
            'a_price': a_price,
            'b_price': b_price,
            'c_price': c_price,
            'd_price': d_price,
            'entry_idx': len(candles) - 1,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def squirrel(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        다람쥐꼬리 (Pin Bar) 패턴 생성
        
        실제 전략 조건:
        1. body >= open * 0.001 (도지 아님)
        2. lower_wick / body >= 2.0 (MIN_WICK_RATIO)
        3. upper_wick < lower_wick
        4. confirm_close > pattern_close
        5. RSI < 50
        6. pattern_low < MA20
        """
        config = config or CandleConfig()
        
        # 1. 하락 추세로 히스토리 생성 (RSI를 낮추기 위해)
        # MA20보다 낮은 가격대로 하락
        candles = []
        price = config.base_price
        
        # 처음 10개: 완만한 횡보/상승 (MA20 기준점 설정)
        for i in range(10):
            change = np.random.uniform(-0.005, 0.01)
            open_price = price
            close_price = price * (1 + change)
            high_price = max(open_price, close_price) * 1.005
            low_price = min(open_price, close_price) * 0.995
            candles.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + np.random.randint(-10, 30)
            })
            price = close_price
        
        ma20_reference = price  # 대략적인 MA20 기준
        
        # 다음 10개: 강한 하락 (RSI를 30 근처로, MA20 아래로)
        for i in range(10):
            open_price = price
            close_price = price * (1 - 0.025)  # 2.5% 연속 하락 → RSI 급락
            high_price = open_price * 1.002
            low_price = close_price * 0.998
            candles.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 50
            })
            price = close_price
        
        # 2. Pin Bar 패턴 캔들 (N-1 위치)
        # body는 작고, 아래꼬리가 매우 길어야 함
        pattern_open = price
        body_size = pattern_open * 0.003  # 0.3% 작은 몸통
        pattern_close = pattern_open + body_size  # 작은 양봉
        
        lower_wick = body_size * 3  # wick_ratio = 3.0 (>= 2.0 조건 충족)
        pattern_low = pattern_open - lower_wick  # 긴 아래꼬리
        
        upper_wick = body_size * 0.3  # 짧은 윗꼬리 (< lower_wick)
        pattern_high = pattern_close + upper_wick
        
        pin_bar = {
            'open': round(pattern_open, 2),
            'high': round(pattern_high, 2),
            'low': round(pattern_low, 2),  # MA20보다 낮음
            'close': round(pattern_close, 2),
            'volume': config.volume_base
        }
        candles.append(pin_bar)
        
        # 3. 확인 캔들 (N 위치) - 양봉, pattern_close보다 높은 종가
        confirm_open = pattern_close
        confirm_close = confirm_open * 1.015  # 1.5% 상승
        confirm_high = confirm_close * 1.005
        confirm_low = confirm_open * 0.997
        
        confirmation = {
            'open': round(confirm_open, 2),
            'high': round(confirm_high, 2),
            'low': round(confirm_low, 2),
            'close': round(confirm_close, 2),
            'volume': int(config.volume_base * 1.5)
        }
        candles.append(confirmation)
        
        entry_price = confirm_close
        
        # 4. 청산 캔들
        if exit_type == "take_profit":
            exit_candles = cls._generate_upward_candles(entry_price, 8, 0.03, config)  # 더 많은 상승
        else:
            exit_candles = cls._generate_downward_candles(entry_price, 2, 0.04, config)
        
        all_candles = candles + exit_candles
        all_candles = cls._ensure_min_candles(all_candles, 100, config)
        
        return {
            'name': f"squirrel_{exit_type}",
            'candles': all_candles,
            'entry_idx': len(candles) - 1,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def inverted_hammer(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        역망치형(Inverted Hammer) 패턴 생성
        
        조건:
        - 하락 추세 중 출현
        - 작은 몸통, 긴 윗꼬리, 짧거나 없는 아래꼬리
        """
        config = config or CandleConfig()
        
        history = cls._generate_base_history(config, 20)
        last_price = history[-1]['close']
        
        # 하락 추세
        downtrend = cls._generate_downward_candles(last_price, 4, 0.025, config)
        
        # 역망치형 캔들 (조건: 윗꼬리≥몸통×2, 아래꼬리<몸통×0.5)
        ih_open = downtrend[-1]['close']
        ih_close = ih_open * 1.005  # 작은 양봉 몸통 (0.5%)
        ih_high = ih_open * 1.025   # 긴 윗꼬리 (2.5% = 몸통의 5배)
        ih_low = ih_open * 0.9995   # 아래꼬리 거의 없음 (0.05%)
        
        inverted_hammer = {
            'open': round(ih_open, 2),
            'high': round(ih_high, 2),
            'low': round(ih_low, 2),
            'close': round(ih_close, 2),
            'volume': config.volume_base
        }
        
        # 확인 캔들 (양봉)
        confirm_open = ih_close
        confirm_close = confirm_open * 1.02
        confirmation = {
            'open': round(confirm_open, 2),
            'high': round(confirm_close * 1.005, 2),
            'low': round(confirm_open * 0.995, 2),
            'close': round(confirm_close, 2),
            'volume': config.volume_base * 1.3
        }
        
        entry_price = confirm_close
        
        # 청산 캔들
        if exit_type == "take_profit":
            exit_candles = cls._generate_upward_candles(entry_price, 3, 0.02, config)
        else:
            exit_candles = cls._generate_downward_candles(entry_price, 2, 0.03, config)
        
        pattern_candles = history + downtrend + [inverted_hammer, confirmation] + exit_candles
        # entry_idx should be the tick where strategy can see pattern+confirmation
        # Strategy does iloc[:-1], then looks at iloc[-2] (pattern) and iloc[-1] (confirmation)
        # So tick needs to be at first exit candle (confirmation + 1)
        original_entry_idx = len(history) + len(downtrend) + 2  # first exit candle index
        
        all_candles = cls._ensure_min_candles(pattern_candles, 100, config)
        
        # _ensure_min_candles adds history at the FRONT, so entry_idx shifts
        padding_added = len(all_candles) - len(pattern_candles)
        adjusted_entry_idx = original_entry_idx + padding_added
        
        return {
            'name': f"inverted_hammer_{exit_type}",
            'candles': all_candles,
            'entry_idx': adjusted_entry_idx,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def leading_diagonal(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Leading Diagonal (Falling Wedge) 패턴 생성
        
        전략 조건:
        1. df >= 30 캔들
        2. detect_falling_wedge(lookback=20):
           - tail(20)의 high_slope < 0
           - tail(20)의 low_slope < 0
           - abs(low_slope) < abs(high_slope) (수렴)
        3. 양봉 돌파
        
        핵심: 100캔들을 직접 생성, _ensure_min_candles 사용 안 함
        """
        config = config or CandleConfig()
        candles = []
        price = config.base_price * 1.3  # 높게 시작 (하락할 것이므로)
        
        # === Phase 1: 70캔들 - 하락 추세 히스토리 ===
        for i in range(70):
            decline = price * 0.003
            open_price = price
            close_price = price - decline
            high_price = open_price * 1.003
            low_price = close_price * 0.998
            
            candles.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base
            })
            price = close_price
        
        # === Phase 2: 25캔들 - Falling Wedge ===
        # high_slope: 급하게 하락 (-0.004)
        # low_slope: 완만하게 하락 (-0.002) → 수렴
        
        wedge_start = price
        high_start = wedge_start * 1.05   # ★ 웨지 시작점을 더 넓게
        low_start = wedge_start * 0.90    # ★ 웨지 시작점을 더 넓게 (15% 폭)
        
        high_decline = config.base_price * 0.006  # ★ 캔들당 고점 하락 (더 급하게)
        low_decline = config.base_price * 0.001   # ★ 캔들당 저점 하락 (매우 완만 → 넓은 웨지)
        
        wedge_len = 24  # 마지막은 돌파용
        for i in range(wedge_len):
            current_high = high_start - (high_decline * i)
            current_low = low_start - (low_decline * i)
            
            mid = (current_high + current_low) / 2
            candle_open = mid * (1 + np.random.uniform(-0.002, 0.002))
            candle_close = mid * (1 + np.random.uniform(-0.002, 0.002))
            
            candles.append({
                'open': round(candle_open, 2),
                'high': round(current_high, 2),
                'low': round(current_low, 2),
                'close': round(candle_close, 2),
                'volume': config.volume_base + np.random.randint(-10, 20)
            })
            price = candle_close
        
        # === Phase 3: 돌파 캔들 ===
        final_resistance = high_start - (high_decline * wedge_len)
        breakout_open = price
        breakout_close = final_resistance * 1.02  # 저항선 2% 위
        
        candles.append({
            'open': round(breakout_open, 2),
            'high': round(breakout_close * 1.005, 2),
            'low': round(breakout_open * 0.995, 2),
            'close': round(breakout_close, 2),
            'volume': config.volume_base * 2
        })
        
        entry_price = breakout_close
        
        # === Phase 4: 청산 캔들 ===
        if exit_type == "take_profit":
            exit_candles = cls._generate_upward_candles(entry_price, 6, 0.025, config)
        else:
            exit_candles = cls._generate_downward_candles(entry_price, 3, 0.04, config)
        
        all_candles = candles + exit_candles
        # 주의: _ensure_min_candles 사용 안 함
        
        return {
            'name': f"leading_diagonal_{exit_type}",
            'candles': all_candles,
            'entry_idx': len(candles) - 1,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    # ==========================================
    # Short 전략 패턴 (Bybit용)
    # ==========================================
    
    @classmethod
    def shooting_star(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Shooting Star (유성형) 패턴 - Short 진입
        
        조건:
        - 상승 추세 중 출현
        - 작은 몸통, 긴 윗꼬리, 짧거나 없는 아래꼬리
        """
        config = config or CandleConfig()
        
        history = cls._generate_base_history(config, 20)
        last_price = history[-1]['close']
        
        # 상승 추세
        uptrend = cls._generate_upward_candles(last_price, 4, 0.025, config)
        
        # Shooting Star (R:R은 ensure_min_rr_short가 보장)
        ss_open = uptrend[-1]['close']
        ss_high = ss_open * 1.04  # 긴 윗꼬리
        ss_close = ss_open * 0.995  # 작은 음봉 몸통
        ss_low = ss_close * 0.998  # 아래꼬리 짧게 (패턴 조건 충족)
        
        shooting_star = {
            'open': round(ss_open, 2),
            'high': round(ss_high, 2),
            'low': round(ss_low, 2),
            'close': round(ss_close, 2),
            'volume': config.volume_base
        }
        
        # 확인 캔들 (음봉)
        confirm_open = ss_close
        confirm_close = confirm_open * 0.98
        confirmation = {
            'open': round(confirm_open, 2),
            'high': round(confirm_open * 1.005, 2),
            'low': round(confirm_close * 0.995, 2),
            'close': round(confirm_close, 2),
            'volume': config.volume_base * 1.3
        }
        
        entry_price = confirm_close
        
        # 청산 캔들 (Short용 - high 낮게 유지)
        if exit_type == "take_profit":
            exit_candles = cls._generate_short_exit_candles(entry_price, 6, 0.04, config, for_tp=True)
        else:
            exit_candles = cls._generate_short_exit_candles(entry_price, 3, 0.05, config, for_tp=False)
        
        all_candles = history + uptrend + [shooting_star, confirmation] + exit_candles
        all_candles = cls._ensure_min_candles(all_candles, 100, config)
        
        return {
            'name': f"shooting_star_{exit_type}",
            'candles': all_candles,
            'direction': 'short',
            'entry_idx': len(history) + len(uptrend) + 1,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def bearish_divergence(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Bearish RSI Divergence - Short 진입
        
        조건:
        1. find_local_maxima(window=7) → 2개 고점 in tail(30)
        2. 가격 Higher High: price_high2 > price_high1
        3. RSI Lower High: rsi_high2 < rsi_high1
        4. rsi_high1 >= 70 (과매수)
        5. 음봉 또는 RSI 하락
        
        핵심: 100캔들 직접 생성, _ensure_min_candles 사용 안 함
        """
        config = config or CandleConfig()
        candles = []
        price = config.base_price * 0.8  # 낮게 시작 (상승할 것)
        
        # === Phase 1: 70캔들 - 상승 추세 히스토리 ===
        for i in range(70):
            rise = price * 0.004
            open_price = price
            close_price = price + rise
            candles.append({
                'open': round(open_price, 2),
                'high': round(close_price * 1.003, 2),
                'low': round(open_price * 0.998, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base
            })
            price = close_price
        
        # === Phase 2: 30캔들 - Bearish Divergence 형성 ===
        # tail(30)에서 고점1은 인덱스 10, 고점2는 인덱스 18에 위치
        
        base_for_30 = price
        high1_price = base_for_30 * 1.15  # ★ 첫 번째 고점 (더 높게)
        high2_price = high1_price * 1.10   # ★ 두 번째 고점 (더 높음 - Price HH, SL 거리 확보)
        
        # 인덱스 0-9: 상승
        for i in range(10):
            step = (high1_price - price) / 10
            open_price = price
            close_price = price + step
            # high는 고점1보다 낮게
            candles.append({
                'open': round(open_price, 2),
                'high': round(high1_price * 0.98, 2),
                'low': round(open_price * 0.998, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 30
            })
            price = close_price
        
        # 인덱스 10: 고점1 (local max)
        candles.append({
            'open': round(price, 2),
            'high': round(high1_price, 2),  # 명확한 고점
            'close': round(high1_price * 0.995, 2),
            'low': round(price * 0.998, 2),
            'volume': config.volume_base + 80
        })
        price = high1_price * 0.995
        
        # 인덱스 11-17: 하락 후 반등 (고점1보다 낮은 high)
        drop_target = base_for_30 * 1.02
        for i in range(7):
            step = (price - drop_target) / 7 if i < 4 else (high2_price - price) / 3
            open_price = price
            close_price = price - step if i < 4 else price + step
            candles.append({
                'open': round(open_price, 2),
                'high': round(high1_price * 0.97, 2),  # 고점1보다 낮게
                'low': round(min(open_price, close_price) * 0.998, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 20
            })
            price = close_price
        
        # 인덱스 18: 고점2 (local max, 고점1보다 높음)
        candles.append({
            'open': round(price, 2),
            'high': round(high2_price, 2),  # 고점1보다 높음
            'close': round(high2_price * 0.995, 2),
            'low': round(price * 0.998, 2),
            'volume': config.volume_base + 70
        })
        price = high2_price * 0.995
        
        # 인덱스 19-27: 하락 (고점2보다 낮은 high)
        for i in range(9):
            step = price * 0.008
            open_price = price
            close_price = price - step
            candles.append({
                'open': round(open_price, 2),
                'high': round(high2_price * 0.97, 2),
                'low': round(close_price * 0.998, 2),
                'close': round(close_price, 2),
                'volume': config.volume_base + 40
            })
            price = close_price
        
        # 인덱스 28: 음봉 확인 캔들
        confirm_open = price
        confirm_close = price * 0.985  # 음봉
        candles.append({
            'open': round(confirm_open, 2),
            'high': round(confirm_open * 1.002, 2),
            'low': round(confirm_close * 0.998, 2),
            'close': round(confirm_close, 2),
            'volume': config.volume_base + 60
        })
        
        # 인덱스 29: 현재 캔들 (미마감)
        candles.append({
            'open': round(confirm_close, 2),
            'high': round(confirm_close * 1.005, 2),
            'low': round(confirm_close * 0.995, 2),
            'close': round(confirm_close * 0.998, 2),
            'volume': config.volume_base
        })
        
        entry_price = confirm_close
        
        # === Phase 3: 청산 캔들 ===
        if exit_type == "take_profit":
            exit_candles = cls._generate_short_exit_candles(entry_price, 10, 0.025, config, for_tp=True)
        else:
            exit_candles = cls._generate_short_exit_candles(entry_price, 10, 0.05, config, for_tp=False)
        
        all_candles = candles + exit_candles
        # 주의: _ensure_min_candles 사용 안 함
        
        return {
            'name': f"bearish_divergence_{exit_type}",
            'candles': all_candles,
            'direction': 'short',
            'high1_idx': 70 + 10,  # 전체 인덱스
            'high2_idx': 70 + 18,
            'entry_idx': len(candles) - 2,  # 확인 캔들
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def evening_star(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Evening Star 패턴 - Short 진입
        Morning Star의 반대 패턴
        """
        config = config or CandleConfig()
        
        history = cls._generate_base_history(config, 20)
        last_price = history[-1]['close']
        
        # 상승 추세
        uptrend = cls._generate_upward_candles(last_price, 3, 0.03, config)
        
        # Evening Star 패턴
        c1_open = uptrend[-1]['close']
        c1_close = c1_open * 1.05  # 큰 양봉
        c1 = {
            'open': round(c1_open, 2),
            'high': round(c1_close * 1.005, 2),
            'low': round(c1_open * 0.995, 2),
            'close': round(c1_close, 2),
            'volume': config.volume_base * 2
        }
        
        # c2: 도지 (고점을 높게 - SL 거리 확보)
        c2_open = c1_close * 1.001
        c2_close = c2_open * 0.998
        c2_high = c2_open * 1.08  # ★ 고점을 8% 높게 (SL 거리 확보)
        c2 = {
            'open': round(c2_open, 2),
            'high': round(c2_high, 2),  # ★ 높은 고점
            'low': round(min(c2_open, c2_close) * 0.997, 2),
            'close': round(c2_close, 2),
            'volume': int(config.volume_base * 0.5)
        }
        
        # c3: 큰 음봉
        c3_open = c2_close
        c3_close = c3_open * 0.94  # 큰 하락
        c3 = {
            'open': round(c3_open, 2),
            'high': round(c3_open * 1.005, 2),
            'low': round(c3_close * 0.99, 2),
            'close': round(c3_close, 2),
            'volume': config.volume_base * 2
        }
        
        pattern = [c1, c2, c3]
        entry_price = c3_close
        
        # 청산 캔들 (Short용 - high 낮게 유지)
        if exit_type == "take_profit":
            exit_candles = cls._generate_short_exit_candles(entry_price, 6, 0.04, config, for_tp=True)
        else:
            exit_candles = cls._generate_short_exit_candles(entry_price, 3, 0.05, config, for_tp=False)
        
        all_candles = history + uptrend + pattern + exit_candles
        all_candles = cls._ensure_min_candles(all_candles, 100, config)
        
        return {
            'name': f"evening_star_{exit_type}",
            'candles': all_candles,
            'direction': 'short',
            'entry_idx': len(history) + len(uptrend) + 2,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def bearish_engulfing(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Bearish Engulfing 패턴 - Short 진입
        """
        config = config or CandleConfig()
        
        history = cls._generate_base_history(config, 20)
        last_price = history[-1]['close']
        
        # 상승 추세
        uptrend = cls._generate_upward_candles(last_price, 4, 0.02, config)
        
        # 작은 양봉
        small_bull_open = uptrend[-1]['close']
        small_bull_close = small_bull_open * 1.01
        small_bull = {
            'open': round(small_bull_open, 2),
            'high': round(small_bull_close * 1.005, 2),
            'low': round(small_bull_open * 0.995, 2),
            'close': round(small_bull_close, 2),
            'volume': config.volume_base
        }
        
        # 큰 음봉 (Engulfing, 고점을 높게 - SL 거리 확보)
        engulf_open = small_bull_close * 1.005
        engulf_high = engulf_open * 1.08  # ★ 고점을 8% 높게 (SL 거리 확보)
        engulf_close = small_bull_open * 0.98
        engulfing = {
            'open': round(engulf_open, 2),
            'high': round(engulf_high, 2),  # ★ 높은 고점
            'low': round(engulf_close * 0.995, 2),
            'close': round(engulf_close, 2),
            'volume': config.volume_base * 2
        }
        
        entry_price = engulf_close
        
        # 청산 캔들 (Short용 - high 낮게 유지)
        if exit_type == "take_profit":
            exit_candles = cls._generate_short_exit_candles(entry_price, 6, 0.04, config, for_tp=True)
        else:
            exit_candles = cls._generate_short_exit_candles(entry_price, 3, 0.05, config, for_tp=False)
        
        all_candles = history + uptrend + [small_bull, engulfing] + exit_candles
        all_candles = cls._ensure_min_candles(all_candles, 100, config)
        
        return {
            'name': f"bearish_engulfing_{exit_type}",
            'candles': all_candles,
            'direction': 'short',
            'entry_idx': len(history) + len(uptrend) + 1,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    @classmethod
    def leading_diagonal_breakdown(cls, config: CandleConfig = None, exit_type: str = "take_profit") -> Dict:
        """
        Leading Diagonal Breakdown - Short 진입
        상승 리딩 다이아고날 이후 하방 이탈
        """
        config = config or CandleConfig()
        
        history = cls._generate_base_history(config, 20)
        last_price = history[-1]['close']
        
        # 5파동 상승 구조 (R:R 1:1.5 충족을 위해 패턴 폭 확대)
        wave1 = cls._generate_upward_candles(last_price, 3, 0.04, config)  # ★ 확대
        wave2 = cls._generate_downward_candles(wave1[-1]['close'], 2, 0.02, config)  # ★ 확대
        wave3 = cls._generate_upward_candles(wave2[-1]['close'], 4, 0.03, config)  # ★ 확대
        wave4 = cls._generate_downward_candles(wave3[-1]['close'], 2, 0.015, config)  # ★ 확대
        wave5 = cls._generate_upward_candles(wave4[-1]['close'], 3, 0.025, config)  # ★ 확대
        
        # 하방 이탈 (트렌드라인 붕괴)
        breakdown = cls._generate_downward_candles(wave5[-1]['close'], 3, 0.05, config)  # ★ 확대
        entry_price = breakdown[-1]['close']
        
        # 청산 캔들
        if exit_type == "take_profit":
            exit_candles = cls._generate_downward_candles(entry_price, 10, 0.03, config)
        else:
            exit_candles = cls._generate_upward_candles(entry_price, 10, 0.05, config)
        
        all_candles = history + wave1 + wave2 + wave3 + wave4 + wave5 + breakdown + exit_candles
        all_candles = cls._ensure_min_candles(all_candles, 100, config)
        
        return {
            'name': f"leading_diagonal_breakdown_{exit_type}",
            'candles': all_candles,
            'direction': 'short',
            'entry_idx': len(all_candles) - len(exit_candles) - 1,
            'entry_price': entry_price,
            'expected_exit': exit_type
        }
    
    # ==========================================
    # 시나리오 없음 (No Signal) 패턴
    # ==========================================
    
    @classmethod
    def no_signal(cls, config: CandleConfig = None) -> Dict:
        """
        신호가 발생하지 않는 횡보 패턴
        """
        config = config or CandleConfig()
        
        # 조용한 횡보 캔들만 생성
        all_candles = cls._generate_base_history(config, 30)
        
        return {
            'name': 'no_signal',
            'candles': all_candles,
            'entry_idx': None,
            'entry_price': None,
            'expected_exit': None
        }
