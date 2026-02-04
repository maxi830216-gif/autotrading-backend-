import pandas as pd
import numpy as np
import pytest
from services.strategy_divergence import BullishDivergenceStrategy
from utils.pattern_utils import calculate_rsi

@pytest.fixture
def divergence_strategy():
    return BullishDivergenceStrategy()

def test_rsi_calculation():
    # 간단한 RSI 계산 테스트
    prices = [100, 102, 104, 103, 101, 100, 99, 98, 100, 102, 104, 103, 101, 100, 98]
    df = pd.DataFrame({'close': prices})
    rsi = calculate_rsi(df)
    assert len(rsi) == len(prices)
    assert not rsi.isna().all()

def test_divergence_detection(divergence_strategy):
    # V자 형태 데이터 생성 (평탄한 구간 방지)
    lows = np.array([120.0] * 35)
    # 주변을 높게 설정
    for i in range(35):
        lows[i] = 130 + i * 0.1  # 기본적으로 130 이상, 미세하게 증가
    
    # 10번째 인덱스: 첫 번째 저점 (100)
    lows[8:13] = [115, 110, 100, 110, 115]
    
    # 20번째 인덱스: 두 번째 저점 (90)
    lows[18:23] = [115, 110, 90, 110, 115]
    
    data = {
        'high': lows + 10,
        'low': lows,
        'close': lows + 5,
        'volume': [1000] * 35
    }
    df = pd.DataFrame(data)
    
    df['rsi'] = 50.0
    df.loc[10, 'rsi'] = 30.0  # 과매도
    df.loc[20, 'rsi'] = 35.0  # 저점 상승 (다이버전스)
    
    # analyze 호출
    is_signal, confidence, info = divergence_strategy.analyze(df)
    
    # 신호가 떠야 함
    # 조건: Low2(90) < Low1(100) AND RSI2(35) > RSI1(30)
    assert is_signal is True
    assert confidence > 0
    assert info['divergence_low'] == 90

def test_no_divergence(divergence_strategy):
    # 다이버전스 없는 경우
    data = {
        'high': [110] * 30,
        'low': [100] * 30,
        'close': [105] * 30,
        'volume': [1000] * 30
    }
    df = pd.DataFrame(data)
    df['rsi'] = 50.0
    
    is_signal, confidence, info = divergence_strategy.analyze(df)
    
    assert is_signal is False
    assert confidence == 0
