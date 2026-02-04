"""
전략별 테스트 시나리오 정의
각 거래소/전략별 테스트 케이스를 생성합니다.
"""
from typing import Dict, List
from local_testing.mock_data_generator import CandlePatternGenerator, CandleConfig
from local_testing.config import DEFAULT_UPBIT_BALANCE, DEFAULT_BYBIT_BALANCE


def build_upbit_scenario(strategy: str, market: str, pattern_data: Dict) -> Dict:
    """Upbit 시나리오 빌더"""
    candles = pattern_data['candles']
    
    return {
        'name': pattern_data['name'],
        'exchange': 'upbit',
        'market': market,
        'timeframe': '1D',
        'initial_balance': DEFAULT_UPBIT_BALANCE,
        'candles': {
            market: {
                'day': candles
            }
        },
        'expected': {
            'entry_idx': pattern_data.get('entry_idx'),
            'entry_price': pattern_data.get('entry_price'),
            'exit_reason': pattern_data.get('expected_exit'),
        }
    }


def build_bybit_scenario(strategy: str, symbol: str, pattern_data: Dict) -> Dict:
    """Bybit 시나리오 빌더"""
    candles = pattern_data['candles']
    
    return {
        'name': pattern_data['name'],
        'exchange': 'bybit',
        'symbol': symbol,
        'timeframe': '1D',
        'direction': pattern_data.get('direction', 'long'),
        'initial_balance': DEFAULT_BYBIT_BALANCE,
        'candles': {
            symbol: {
                'D': candles
            }
        },
        'expected': {
            'entry_idx': pattern_data.get('entry_idx'),
            'entry_price': pattern_data.get('entry_price'),
            'exit_reason': pattern_data.get('expected_exit'),
            'direction': pattern_data.get('direction', 'long'),
        }
    }


# ==========================================
# Upbit 시나리오 (Long Only)
# ==========================================

UPBIT_SCENARIOS = {
    'morning_star': {
        'take_profit': build_upbit_scenario(
            'morning_star', 'KRW-BTC',
            CandlePatternGenerator.morning_star(exit_type='take_profit')
        ),
        'stop_loss': build_upbit_scenario(
            'morning_star', 'KRW-BTC',
            CandlePatternGenerator.morning_star(exit_type='stop_loss')
        ),
        'no_signal': build_upbit_scenario(
            'morning_star', 'KRW-BTC',
            CandlePatternGenerator.no_signal()
        ),
    },
    'divergence': {
        'take_profit': build_upbit_scenario(
            'divergence', 'KRW-BTC',
            CandlePatternGenerator.divergence(exit_type='take_profit')
        ),
        'stop_loss': build_upbit_scenario(
            'divergence', 'KRW-BTC',
            CandlePatternGenerator.divergence(exit_type='stop_loss')
        ),
        'no_signal': build_upbit_scenario(
            'divergence', 'KRW-BTC',
            CandlePatternGenerator.no_signal()
        ),
    },
    'harmonic': {
        'take_profit': build_upbit_scenario(
            'harmonic', 'KRW-BTC',
            CandlePatternGenerator.harmonic_gartley(exit_type='take_profit')
        ),
        'stop_loss': build_upbit_scenario(
            'harmonic', 'KRW-BTC',
            CandlePatternGenerator.harmonic_gartley(exit_type='stop_loss')
        ),
        'no_signal': build_upbit_scenario(
            'harmonic', 'KRW-BTC',
            CandlePatternGenerator.no_signal()
        ),
    },
    'squirrel': {
        'take_profit': build_upbit_scenario(
            'squirrel', 'KRW-BTC',
            CandlePatternGenerator.squirrel(exit_type='take_profit')
        ),
        'stop_loss': build_upbit_scenario(
            'squirrel', 'KRW-BTC',
            CandlePatternGenerator.squirrel(exit_type='stop_loss')
        ),
        'no_signal': build_upbit_scenario(
            'squirrel', 'KRW-BTC',
            CandlePatternGenerator.no_signal()
        ),
    },
    'inverted_hammer': {
        'take_profit': build_upbit_scenario(
            'inverted_hammer', 'KRW-BTC',
            CandlePatternGenerator.inverted_hammer(exit_type='take_profit')
        ),
        'stop_loss': build_upbit_scenario(
            'inverted_hammer', 'KRW-BTC',
            CandlePatternGenerator.inverted_hammer(exit_type='stop_loss')
        ),
        'no_signal': build_upbit_scenario(
            'inverted_hammer', 'KRW-BTC',
            CandlePatternGenerator.no_signal()
        ),
    },
    'leading_diagonal': {
        'take_profit': build_upbit_scenario(
            'leading_diagonal', 'KRW-BTC',
            CandlePatternGenerator.leading_diagonal(exit_type='take_profit')
        ),
        'stop_loss': build_upbit_scenario(
            'leading_diagonal', 'KRW-BTC',
            CandlePatternGenerator.leading_diagonal(exit_type='stop_loss')
        ),
        'no_signal': build_upbit_scenario(
            'leading_diagonal', 'KRW-BTC',
            CandlePatternGenerator.no_signal()
        ),
    },
}

# ==========================================
# Bybit 시나리오 (Long + Short)
# ==========================================

BYBIT_SCENARIOS = {
    # Long 전략
    'morning_star': {
        'take_profit': build_bybit_scenario(
            'morning_star', 'BTCUSDT',
            CandlePatternGenerator.morning_star(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'morning_star', 'BTCUSDT',
            CandlePatternGenerator.morning_star(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'morning_star', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'divergence': {
        'take_profit': build_bybit_scenario(
            'divergence', 'BTCUSDT',
            CandlePatternGenerator.divergence(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'divergence', 'BTCUSDT',
            CandlePatternGenerator.divergence(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'divergence', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'harmonic': {
        'take_profit': build_bybit_scenario(
            'harmonic', 'BTCUSDT',
            CandlePatternGenerator.harmonic_gartley(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'harmonic', 'BTCUSDT',
            CandlePatternGenerator.harmonic_gartley(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'harmonic', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'squirrel': {
        'take_profit': build_bybit_scenario(
            'squirrel', 'BTCUSDT',
            CandlePatternGenerator.squirrel(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'squirrel', 'BTCUSDT',
            CandlePatternGenerator.squirrel(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'squirrel', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'inverted_hammer': {
        'take_profit': build_bybit_scenario(
            'inverted_hammer', 'BTCUSDT',
            CandlePatternGenerator.inverted_hammer(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'inverted_hammer', 'BTCUSDT',
            CandlePatternGenerator.inverted_hammer(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'inverted_hammer', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'leading_diagonal': {
        'take_profit': build_bybit_scenario(
            'leading_diagonal', 'BTCUSDT',
            CandlePatternGenerator.leading_diagonal(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'leading_diagonal', 'BTCUSDT',
            CandlePatternGenerator.leading_diagonal(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'leading_diagonal', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    
    # Short 전략
    'shooting_star': {
        'take_profit': build_bybit_scenario(
            'shooting_star', 'BTCUSDT',
            CandlePatternGenerator.shooting_star(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'shooting_star', 'BTCUSDT',
            CandlePatternGenerator.shooting_star(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'shooting_star', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'bearish_divergence': {
        'take_profit': build_bybit_scenario(
            'bearish_divergence', 'BTCUSDT',
            CandlePatternGenerator.bearish_divergence(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'bearish_divergence', 'BTCUSDT',
            CandlePatternGenerator.bearish_divergence(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'bearish_divergence', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'evening_star': {
        'take_profit': build_bybit_scenario(
            'evening_star', 'BTCUSDT',
            CandlePatternGenerator.evening_star(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'evening_star', 'BTCUSDT',
            CandlePatternGenerator.evening_star(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'evening_star', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'bearish_engulfing': {
        'take_profit': build_bybit_scenario(
            'bearish_engulfing', 'BTCUSDT',
            CandlePatternGenerator.bearish_engulfing(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'bearish_engulfing', 'BTCUSDT',
            CandlePatternGenerator.bearish_engulfing(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'bearish_engulfing', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
    'leading_diagonal_breakdown': {
        'take_profit': build_bybit_scenario(
            'leading_diagonal_breakdown', 'BTCUSDT',
            CandlePatternGenerator.leading_diagonal_breakdown(exit_type='take_profit')
        ),
        'stop_loss': build_bybit_scenario(
            'leading_diagonal_breakdown', 'BTCUSDT',
            CandlePatternGenerator.leading_diagonal_breakdown(exit_type='stop_loss')
        ),
        'no_signal': build_bybit_scenario(
            'leading_diagonal_breakdown', 'BTCUSDT',
            CandlePatternGenerator.no_signal()
        ),
    },
}


def get_all_upbit_scenarios() -> List[Dict]:
    """모든 Upbit 시나리오 목록 반환"""
    scenarios = []
    for strategy, cases in UPBIT_SCENARIOS.items():
        for case_name, scenario in cases.items():
            scenario['strategy'] = strategy
            scenario['case'] = case_name
            scenarios.append(scenario)
    return scenarios


def get_all_bybit_scenarios() -> List[Dict]:
    """모든 Bybit 시나리오 목록 반환"""
    scenarios = []
    for strategy, cases in BYBIT_SCENARIOS.items():
        for case_name, scenario in cases.items():
            scenario['strategy'] = strategy
            scenario['case'] = case_name
            scenarios.append(scenario)
    return scenarios


def get_scenario(exchange: str, strategy: str, case: str) -> Dict:
    """특정 시나리오 반환"""
    if exchange == 'upbit':
        return UPBIT_SCENARIOS.get(strategy, {}).get(case)
    else:
        return BYBIT_SCENARIOS.get(strategy, {}).get(case)
