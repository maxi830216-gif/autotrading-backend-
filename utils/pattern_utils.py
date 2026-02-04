"""
Pattern Detection Utilities
파보나치 계산, 저점/고점 탐지, 쐐기 패턴 감지 등
"""
import numpy as np
import pandas as pd
from typing import List, Tuple, Optional


def find_local_minima(series: pd.Series, window: int = 5) -> List[int]:
    """
    로컬 최저점(저점) 인덱스 목록 반환
    window 크기만큼 양쪽을 비교해서 가장 낮은 점을 찾음
    """
    minima = []
    values = series.values
    
    for i in range(window, len(values) - window):
        if values[i] == min(values[i - window:i + window + 1]):
            minima.append(i)
    
    return minima


def find_local_maxima(series: pd.Series, window: int = 5) -> List[int]:
    """
    로컬 최고점(고점) 인덱스 목록 반환
    """
    maxima = []
    values = series.values
    
    for i in range(window, len(values) - window):
        if values[i] == max(values[i - window:i + window + 1]):
            maxima.append(i)
    
    return maxima


def fibonacci_retracement(high: float, low: float, level: float) -> float:
    """
    피보나치 되돌림 가격 계산
    level: 0.382, 0.5, 0.618, 0.786, 0.886 등
    """
    return low + (high - low) * (1 - level)


def fibonacci_extension(start: float, end: float, retracement: float, level: float) -> float:
    """
    피보나치 확장 가격 계산
    level: 1.272, 1.618, 2.618 등
    """
    move = end - start
    return retracement + move * level


def calculate_fibonacci_accuracy(actual: float, target: float, tolerance: float = 0.03) -> float:
    """
    실제 값이 목표 피보나치 레벨에 얼마나 가까운지 계산
    Returns: 0.0 ~ 1.0 (1.0 = 정확히 일치)
    """
    if target == 0:
        return 0.0
    
    error = abs(actual - target) / abs(target)
    
    if error <= tolerance:
        return 1.0 - (error / tolerance)
    else:
        return 0.0


def detect_falling_wedge(
    df: pd.DataFrame,
    lookback: int = 20,
    min_touches: int = 2
) -> Tuple[bool, Optional[dict]]:
    """
    하락 쐐기(Falling Wedge) 패턴 감지
    
    Returns:
        (패턴감지여부, 패턴정보 dict)
    """
    if len(df) < lookback:
        return False, None
    
    recent = df.tail(lookback)
    highs = recent['high'].values
    lows = recent['low'].values
    
    # 고점과 저점의 추세선 기울기 계산
    x = np.arange(len(highs))
    
    try:
        # 고점 추세선 (저항선)
        high_slope, high_intercept = np.polyfit(x, highs, 1)
        # 저점 추세선 (지지선)
        low_slope, low_intercept = np.polyfit(x, lows, 1)
    except Exception:
        return False, None
    
    # 하락 쐐기 조건:
    # 1. 두 추세선 모두 하락 (기울기 < 0)
    # 2. 저점 추세선 기울기가 고점 추세선보다 가파름 (수렴)
    # 3. 현재 가격이 저항선 근처 (상단 돌파 가능성)
    
    if high_slope >= 0 or low_slope >= 0:
        return False, None
    
    # 수렴 확인: 저점 하락이 고점 하락보다 완만해야 함 (절대값 기준)
    if abs(low_slope) >= abs(high_slope):
        return False, None
    
    # 현재가와 현재 시점의 추세선 값 계산
    current_price = df['close'].iloc[-1]
    # 현재 시점(마지막 인덱스)에서의 추세선 값
    current_idx = len(recent) - 1
    resistance_at_current = high_slope * current_idx + high_intercept
    support_at_current = low_slope * current_idx + low_intercept
    
    # 쐐기 폭 (시작점 기준으로 더 넓은 폭 사용)
    resistance_at_start = high_intercept  # x=0 시점
    support_at_start = low_intercept
    wedge_width = resistance_at_start - support_at_start
    
    # 현재가가 저항선의 95% 이상이면 돌파 임박
    if current_price >= resistance_at_current * 0.95:
        # 목표가: 현재가 + 쐐기 폭 (또는 현재가의 5% 중 큰 값)
        # 쐐기 돌파 후 쐐기 폭만큼 상승하는 것이 기술적 목표
        target_price = max(current_price * 1.05, current_price + wedge_width * 0.5)
        
        return True, {
            'resistance': resistance_at_current,  # 실제 저항선 (돌파 확인용)
            'target_price': target_price,  # 목표가
            'support': support_at_current,  # 현재 시점 지지선
            'high_slope': high_slope,
            'low_slope': low_slope,
            'wedge_width': wedge_width,
            'breakout_price': current_price  # 돌파 시점 가격
        }
    
    return False, None


def detect_breakout(
    df: pd.DataFrame,
    resistance: float,
    lookback: int = 3
) -> bool:
    """
    저항선 상향 돌파 확인
    """
    if len(df) < lookback:
        return False
    
    recent = df.tail(lookback)
    
    # 최근 종가가 저항선 위로 올라왔으면 돌파
    if recent['close'].iloc[-1] > resistance:
        return True
    
    return False


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    RSI 계산 (이미 df에 있으면 그냥 반환)
    """
    if 'rsi' in df.columns:
        return df['rsi']
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD, Signal, Histogram 계산
    """
    if 'macd' in df.columns and 'macd_signal' in df.columns:
        return df['macd'], df['macd_signal'], df.get('macd_hist', df['macd'] - df['macd_signal'])
    
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    
    return macd, macd_signal, macd_hist
