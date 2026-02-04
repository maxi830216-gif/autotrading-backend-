"""
Local Testing Configuration
테스트 환경 설정 및 상수 정의
"""
from pathlib import Path

# 테스트 디렉토리 경로
LOCAL_TESTING_DIR = Path(__file__).parent
TEST_DB_PATH = LOCAL_TESTING_DIR / "test.db"
RESULTS_DIR = LOCAL_TESTING_DIR / "results"

# 기본 설정
DEFAULT_UPBIT_BALANCE = 1_000_000  # 100만원
DEFAULT_BYBIT_BALANCE = 10_000     # 10,000 USDT

# 수수료
UPBIT_FEE_RATE = 0.0005   # 0.05%
BYBIT_FEE_RATE = 0.001    # 0.1%

# 최소 캔들 히스토리 (지표 계산용)
MIN_CANDLE_HISTORY = 20

# 타임프레임 설정
TIMEFRAMES = {
    "upbit": {
        "day": "1D",
        "minute240": "4H",
    },
    "bybit": {
        "D": "1D",
        "240": "4H",
    }
}

# 전략 목록
UPBIT_STRATEGIES = [
    "morning_star",
    "divergence", 
    "harmonic",
    "squirrel",
    "inverted_hammer",
    "leading_diagonal",
]

BYBIT_STRATEGIES = [
    # Long strategies
    "morning_star",
    "divergence",
    "harmonic", 
    "squirrel",
    "inverted_hammer",
    "leading_diagonal",
    # Short strategies
    "shooting_star",
    "bearish_divergence",
    "evening_star",
    "bearish_engulfing",
    "leading_diagonal_breakdown",
]
