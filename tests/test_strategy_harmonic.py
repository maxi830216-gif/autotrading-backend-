import pandas as pd
import pytest
from services.strategy_harmonic import HarmonicPatternStrategy

@pytest.fixture
def harmonic_strategy():
    return HarmonicPatternStrategy()

def test_gartley_pattern(harmonic_strategy):
    # 가틀리 패턴 데이터 생성 (X-A-B-C-D)
    # X: 0, A: 10, B: 3.82 (61.8%), C: ...
    # 실제로는 복잡하므로 간단한 구조만 테스트
    
    # analyze 메서드가 정상적으로 실행되고 예외가 없는지 확인
    data = {
        'high': [100] * 50,
        'low': [90] * 50,
        'close': [95] * 50,
        'volume': [1000] * 50
    }
    df = pd.DataFrame(data)
    
    is_signal, confidence, info = harmonic_strategy.analyze(df)
    
    # 랜덤 데이터라 신호는 False여야 함
    assert is_signal is False

def test_check_exit_logic(harmonic_strategy):
    # 청산 로직 테스트
    # D점(진입) = 100, A점(TP1) = 110, C점(TP2) = 115
    # 현재가 111 -> TP1 달성, 50% 매도
    
    entry_price = 100
    current_price = 111
    A_point = 110
    C_point = 115
    stop_loss = 90
    
    should_exit, reason, exit_type, sell_ratio = harmonic_strategy.check_exit(
        df=pd.DataFrame(), # 안씀
        entry_price=entry_price,
        current_price=current_price,
        partial_exit_stage=0,
        A_point=A_point,
        C_point=C_point,
        stop_loss=stop_loss
    )
    
    assert should_exit is True
    assert sell_ratio == 0.5
    assert "1차익절" in reason
