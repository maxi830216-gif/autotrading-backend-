import pandas as pd
import pytest
from services.strategy_leading_diagonal import LeadingDiagonalStrategy

@pytest.fixture
def leading_strategy():
    return LeadingDiagonalStrategy()

def test_no_signal_flat_data(leading_strategy):
    # 평탄한 데이터에서는 신호 없음
    data = {
        'high': [100] * 30,
        'low': [90] * 30,
        'close': [95] * 30,
        'volume': [1000] * 30
    }
    df = pd.DataFrame(data)
    
    is_signal, confidence, info = leading_strategy.analyze(df)
    
    assert is_signal is False

def test_exit_logic_tp1(leading_strategy):
    # 1차 익절 테스트
    # 진입 100, 저항선 110
    # 현재가 116 (저항선 * 1.03 = 113.3) -> TP1 조건 만족
    
    entry_price = 100
    current_price = 116
    resistance = 110
    support = 90
    
    should_exit, reason, exit_type, sell_ratio = leading_strategy.check_exit(
        df=pd.DataFrame({'close': [current_price]}),
        entry_price=entry_price,
        current_price=current_price,
        partial_exit_stage=0,
        resistance=resistance,
        support=support
    )
    
    # 1.03 * resistance = 113.3
    # 116 > 113.3 -> True
    
    assert should_exit is True
    assert sell_ratio == 0.5
    assert "1차익절" in reason
