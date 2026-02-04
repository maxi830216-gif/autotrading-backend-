#!/usr/bin/env python3
"""
Bybit Strategy Diagnostic Tool
현재 감시종목에 대해 모든 전략의 조건을 하나씩 체크합니다.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from pybit.unified_trading import HTTP
import ta

# Import all strategies
from services.strategy_squirrel import squirrel_strategy
from services.strategy_morning import morning_star_strategy
from services.strategy_inverted_hammer import inverted_hammer_strategy
from services.strategy_divergence import divergence_strategy
from services.strategy_harmonic import harmonic_strategy
from services.strategy_leading_diagonal import leading_diagonal_strategy

from services.strategy_bearish_divergence import bearish_divergence_strategy
from services.strategy_evening_star import evening_star_strategy
from services.strategy_shooting_star import shooting_star_strategy
from services.strategy_bearish_engulfing import bearish_engulfing_strategy
from services.strategy_leading_diagonal_breakdown import leading_diagonal_breakdown_strategy

from services.bybit_whitelist import bybit_whitelist_service


def get_bybit_candles(symbol: str, interval: str = "D", limit: int = 100) -> pd.DataFrame:
    """Bybit에서 캔들 데이터 가져오기"""
    client = HTTP()
    response = client.get_kline(
        category="linear",
        symbol=symbol,
        interval=interval,
        limit=limit
    )
    
    if response['retCode'] != 0 or not response['result']['list']:
        return None
    
    data = list(reversed(response['result']['list']))
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df


def check_morning_star(df: pd.DataFrame) -> dict:
    """Morning Star 조건 체크"""
    if len(df) < 5:
        return {"pass": False, "reason": "데이터 부족"}
    
    # 마감된 캔들만 분석
    df = df.iloc[:-1].copy()
    
    c1 = df.iloc[-3]  # N-2
    c2 = df.iloc[-2]  # N-1
    c3 = df.iloc[-1]  # N
    
    conditions = {}
    
    # 조건 1: N-2 긴 음봉
    c1_is_bearish = c1['close'] < c1['open']
    c1_body = abs(c1['close'] - c1['open'])
    c1_body_pct = c1_body / c1['open'] if c1['open'] > 0 else 0
    conditions["① N-2 음봉"] = "✅" if c1_is_bearish else "❌"
    conditions["② N-2 몸통≥1%"] = f"{'✅' if c1_body_pct >= 0.01 else '❌'} ({c1_body_pct*100:.2f}%)"
    
    # 조건 2: N-1 도지
    c2_body = abs(c2['close'] - c2['open'])
    c2_body_pct = c2_body / c2['open'] if c2['open'] > 0 else 0
    conditions["③ N-1 도지≤1%"] = f"{'✅' if c2_body_pct <= 0.01 else '❌'} ({c2_body_pct*100:.2f}%)"
    
    # 조건 3: N 양봉
    c3_is_bullish = c3['close'] > c3['open']
    conditions["④ N 양봉"] = "✅" if c3_is_bullish else "❌"
    
    # 조건 4: 50% 회복
    c3_body = abs(c3['close'] - c3['open'])
    recovery = c3_body / c1_body if c1_body > 0 else 0
    conditions["⑤ 50% 회복"] = f"{'✅' if recovery >= 0.5 else '❌'} ({recovery*100:.0f}%)"
    
    # 조건 5: RSI < 40
    rsi = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    rsi_val = rsi.iloc[-2] if len(rsi) > 1 else 50
    conditions["⑥ RSI<40"] = f"{'✅' if rsi_val < 40 else '❌'} ({rsi_val:.1f})"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def check_squirrel(df: pd.DataFrame) -> dict:
    """Squirrel (핀바) 조건 체크"""
    if len(df) < 20:
        return {"pass": False, "reason": "데이터 부족"}
    
    df = df.iloc[:-1].copy()
    
    # 지표 계산
    df['ma20'] = df['close'].rolling(window=20).mean()
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    
    pattern = df.iloc[-2]
    confirm = df.iloc[-1]
    
    body = abs(pattern['close'] - pattern['open'])
    lower_wick = min(pattern['close'], pattern['open']) - pattern['low']
    upper_wick = pattern['high'] - max(pattern['close'], pattern['open'])
    
    conditions = {}
    
    # 조건 1: 아래꼬리 >= 몸통*2
    wick_ratio = lower_wick / body if body > 0 else 0
    conditions["① 아래꼬리≥몸통×2"] = f"{'✅' if wick_ratio >= 2.0 else '❌'} ({wick_ratio:.1f}x)"
    
    # 조건 2: 몸통 상단 (윗꼬리 < 아래꼬리)
    conditions["② 윗꼬리<아래꼬리"] = "✅" if upper_wick < lower_wick else "❌"
    
    # 조건 3: 확인캔들 상승
    conditions["③ 확인캔들 상승"] = "✅" if confirm['close'] > pattern['close'] else "❌"
    
    # 조건 4: RSI < 50
    rsi_val = pattern['rsi'] if pd.notna(pattern.get('rsi')) else 50
    conditions["④ RSI<50"] = f"{'✅' if rsi_val < 50 else '❌'} ({rsi_val:.1f})"
    
    # 조건 5: 패턴 Low < MA20
    ma20_val = pattern['ma20'] if pd.notna(pattern.get('ma20')) else pattern['close']
    conditions["⑤ Low<MA20"] = f"{'✅' if pattern['low'] < ma20_val else '❌'}"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def check_inverted_hammer(df: pd.DataFrame) -> dict:
    """Inverted Hammer 조건 체크"""
    if len(df) < 20:
        return {"pass": False, "reason": "데이터 부족"}
    
    df = df.iloc[:-1].copy()
    df['ma20'] = df['close'].rolling(window=20).mean()
    
    pattern = df.iloc[-2]
    confirm = df.iloc[-1]
    
    body = abs(pattern['close'] - pattern['open'])
    upper_wick = pattern['high'] - max(pattern['close'], pattern['open'])
    lower_wick = min(pattern['close'], pattern['open']) - pattern['low']
    
    conditions = {}
    
    # 조건 1: 하락 추세
    ma20 = pattern['ma20'] if pd.notna(pattern.get('ma20')) else pattern['close']
    conditions["① Close<MA20"] = f"{'✅' if pattern['close'] < ma20 else '❌'}"
    
    # 조건 2: 윗꼬리 >= 몸통*2
    wick_ratio = upper_wick / body if body > 0 else 0
    conditions["② 윗꼬리≥몸통×2"] = f"{'✅' if wick_ratio >= 2.0 else '❌'} ({wick_ratio:.1f}x)"
    
    # 조건 3: 아래꼬리 < 몸통*0.5
    lower_ratio = lower_wick / body if body > 0 else 0
    conditions["③ 아래꼬리<몸통×0.5"] = f"{'✅' if lower_ratio < 0.5 else '❌'} ({lower_ratio:.1f}x)"
    
    # 조건 4: 확인 (양봉 or 고점돌파)
    confirm_bullish = confirm['close'] > confirm['open']
    confirm_break = confirm['close'] > pattern['high']
    conditions["④ 확인(양봉/고점돌파)"] = "✅" if (confirm_bullish or confirm_break) else "❌"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def check_divergence(df: pd.DataFrame) -> dict:
    """Bullish Divergence 조건 체크"""
    from utils.pattern_utils import find_local_minima, calculate_rsi
    
    if len(df) < 30:
        return {"pass": False, "reason": "데이터 부족"}
    
    df = df.iloc[:-1].copy()
    rsi = calculate_rsi(df)
    df['rsi'] = rsi
    
    df_recent = df.tail(30).copy()
    price_lows = find_local_minima(df_recent['low'], window=7)
    
    conditions = {}
    
    # 조건 1: 2개 이상 저점
    conditions["① 2개 이상 저점"] = f"{'✅' if len(price_lows) >= 2 else '❌'} ({len(price_lows)}개)"
    
    if len(price_lows) < 2:
        return {"pass": False, "conditions": conditions}
    
    recent_lows = price_lows[-2:]
    price_low1 = df_recent['low'].iloc[recent_lows[0]]
    price_low2 = df_recent['low'].iloc[recent_lows[1]]
    rsi_low1 = df_recent['rsi'].iloc[recent_lows[0]]
    rsi_low2 = df_recent['rsi'].iloc[recent_lows[1]]
    
    # 조건 2: 가격 LL
    conditions["② 가격 LL"] = f"{'✅' if price_low2 < price_low1 else '❌'} ({price_low2:.0f} vs {price_low1:.0f})"
    
    # 조건 3: RSI HL
    conditions["③ RSI HL"] = f"{'✅' if rsi_low2 > rsi_low1 else '❌'} ({rsi_low2:.1f} vs {rsi_low1:.1f})"
    
    # 조건 4: 현재가 > 저점
    current_price = df['close'].iloc[-1]
    conditions["④ 현재가>저점"] = f"{'✅' if current_price > price_low2 else '❌'}"
    
    # 조건 5: 양봉
    last = df.iloc[-1]
    conditions["⑤ 양봉"] = "✅" if last['close'] > last['open'] else "❌"
    
    # 조건 6: RSI 반등
    curr_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2] if len(rsi) > 1 else curr_rsi
    conditions["⑥ RSI 반등"] = f"{'✅' if curr_rsi > prev_rsi else '❌'} ({curr_rsi:.1f} vs {prev_rsi:.1f})"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def check_shooting_star(df: pd.DataFrame) -> dict:
    """Shooting Star 조건 체크"""
    if len(df) < 20:
        return {"pass": False, "reason": "데이터 부족"}
    
    df = df.iloc[:-1].copy()
    df['ma20'] = df['close'].rolling(window=20).mean()
    
    pattern = df.iloc[-2]
    confirm = df.iloc[-1]
    
    body = abs(pattern['close'] - pattern['open'])
    upper_wick = pattern['high'] - max(pattern['close'], pattern['open'])
    lower_wick = min(pattern['close'], pattern['open']) - pattern['low']
    
    conditions = {}
    
    # 조건 1: 상승 추세
    ma20 = pattern['ma20'] if pd.notna(pattern.get('ma20')) else pattern['close']
    conditions["① Close>MA20"] = f"{'✅' if pattern['close'] > ma20 else '❌'}"
    
    # 조건 2: 윗꼬리 >= 몸통*2
    wick_ratio = upper_wick / body if body > 0 else 0
    conditions["② 윗꼬리≥몸통×2"] = f"{'✅' if wick_ratio >= 2.0 else '❌'} ({wick_ratio:.1f}x)"
    
    # 조건 3: 아래꼬리 < 몸통*0.5
    lower_ratio = lower_wick / body if body > 0 else 0
    conditions["③ 아래꼬리<몸통×0.5"] = f"{'✅' if lower_ratio < 0.5 else '❌'} ({lower_ratio:.1f}x)"
    
    # 조건 4: 확인 (음봉 or 저점이탈)
    confirm_bearish = confirm['close'] < confirm['open']
    confirm_break = confirm['close'] < pattern['low']
    conditions["④ 확인(음봉/저점이탈)"] = "✅" if (confirm_bearish or confirm_break) else "❌"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def check_evening_star(df: pd.DataFrame) -> dict:
    """Evening Star 조건 체크"""
    if len(df) < 5:
        return {"pass": False, "reason": "데이터 부족"}
    
    df = df.iloc[:-1].copy()
    
    c1 = df.iloc[-3]
    c2 = df.iloc[-2]
    c3 = df.iloc[-1]
    
    conditions = {}
    
    # 조건 1: N-2 긴 양봉
    c1_is_bullish = c1['close'] > c1['open']
    c1_body = abs(c1['close'] - c1['open'])
    c1_body_pct = c1_body / c1['open'] if c1['open'] > 0 else 0
    conditions["① N-2 양봉"] = "✅" if c1_is_bullish else "❌"
    conditions["② N-2 몸통≥1%"] = f"{'✅' if c1_body_pct >= 0.01 else '❌'} ({c1_body_pct*100:.2f}%)"
    
    # 조건 2: N-1 도지
    c2_body = abs(c2['close'] - c2['open'])
    c2_body_pct = c2_body / c2['open'] if c2['open'] > 0 else 0
    conditions["③ N-1 도지≤1%"] = f"{'✅' if c2_body_pct <= 0.01 else '❌'} ({c2_body_pct*100:.2f}%)"
    
    # 조건 3: N 음봉
    c3_is_bearish = c3['close'] < c3['open']
    conditions["④ N 음봉"] = "✅" if c3_is_bearish else "❌"
    
    # 조건 4: 50% 하락
    c3_body = abs(c3['close'] - c3['open'])
    recovery = c3_body / c1_body if c1_body > 0 else 0
    conditions["⑤ 50% 하락"] = f"{'✅' if recovery >= 0.5 else '❌'} ({recovery*100:.0f}%)"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def check_bearish_engulfing(df: pd.DataFrame) -> dict:
    """Bearish Engulfing 조건 체크"""
    if len(df) < 20:
        return {"pass": False, "reason": "데이터 부족"}
    
    df = df.iloc[:-1].copy()
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    df['sma20'] = df['close'].rolling(20).mean()
    
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    
    conditions = {}
    
    # 조건 1: Prev=양봉
    conditions["① Prev 양봉"] = "✅" if prev['close'] > prev['open'] else "❌"
    
    # 조건 2: Curr=음봉
    conditions["② Curr 음봉"] = "✅" if curr['close'] < curr['open'] else "❌"
    
    # 조건 3: 장악
    engulf1 = curr['open'] >= prev['close']
    engulf2 = curr['close'] < prev['open']
    conditions["③ 장악(Open≥PrevClose)"] = "✅" if engulf1 else "❌"
    conditions["④ 장악(Close<PrevOpen)"] = "✅" if engulf2 else "❌"
    
    # 조건 4: 추세
    sma20 = curr['sma20'] if pd.notna(curr.get('sma20')) else curr['close']
    rsi = curr['rsi'] if pd.notna(curr.get('rsi')) else 50
    above_sma = curr['close'] > sma20
    rsi_high = rsi >= 60
    conditions["⑤ 추세(SMA20↑ or RSI≥60)"] = f"{'✅' if (above_sma or rsi_high) else '❌'} (RSI={rsi:.0f})"
    
    # 조건 5: 거래량
    conditions["⑥ 거래량증가"] = "✅" if curr['volume'] > prev['volume'] else "❌"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def check_bearish_divergence(df: pd.DataFrame) -> dict:
    """Bearish Divergence 조건 체크"""
    from utils.pattern_utils import find_local_maxima, calculate_rsi
    
    if len(df) < 30:
        return {"pass": False, "reason": "데이터 부족"}
    
    df = df.iloc[:-1].copy()
    rsi = calculate_rsi(df)
    df['rsi'] = rsi
    
    df_recent = df.tail(30).copy()
    price_highs = find_local_maxima(df_recent['high'], window=7)
    
    conditions = {}
    
    # 조건 1: 2개 이상 고점
    conditions["① 2개 이상 고점"] = f"{'✅' if len(price_highs) >= 2 else '❌'} ({len(price_highs)}개)"
    
    if len(price_highs) < 2:
        return {"pass": False, "conditions": conditions}
    
    recent_highs = price_highs[-2:]
    price_high1 = df_recent['high'].iloc[recent_highs[0]]
    price_high2 = df_recent['high'].iloc[recent_highs[1]]
    rsi_high1 = df_recent['rsi'].iloc[recent_highs[0]]
    rsi_high2 = df_recent['rsi'].iloc[recent_highs[1]]
    
    # 조건 2: 가격 HH
    conditions["② 가격 HH"] = f"{'✅' if price_high2 > price_high1 else '❌'} ({price_high2:.0f} vs {price_high1:.0f})"
    
    # 조건 3: RSI LH
    conditions["③ RSI LH"] = f"{'✅' if rsi_high2 < rsi_high1 else '❌'} ({rsi_high2:.1f} vs {rsi_high1:.1f})"
    
    # 조건 4: RSI >= 65 (완화됨)
    conditions["④ RSI≥65"] = f"{'✅' if rsi_high1 >= 65 else '❌'} ({rsi_high1:.1f})"
    
    # 조건 5: 확인 (음봉 or RSI 하락)
    last = df.iloc[-1]
    curr_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2] if len(rsi) > 1 else curr_rsi
    is_bearish = last['close'] < last['open']
    rsi_falling = curr_rsi < prev_rsi
    conditions["⑤ 확인(음봉/RSI하락)"] = "✅" if (is_bearish or rsi_falling) else "❌"
    
    all_pass = all("✅" in v for v in conditions.values())
    return {"pass": all_pass, "conditions": conditions}


def run_diagnosis():
    """전체 진단 실행"""
    print("\n" + "="*70)
    print("🔍 BYBIT 전략 진단 리포트")
    print("="*70)
    
    # 감시종목 가져오기
    try:
        symbols = bybit_whitelist_service.get_whitelist_symbols()
    except:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    
    print(f"\n📊 감시종목: {len(symbols)}개")
    print("-"*70)
    
    # 캔들 윈도우 체크
    from utils.scheduler_common import is_within_candle_close_window
    is_1d, msg_1d = is_within_candle_close_window("1D")
    is_4h, msg_4h = is_within_candle_close_window("4H")
    
    print(f"\n⏰ 캔들 윈도우 상태:")
    print(f"   1D: {'✅ 활성' if is_1d else '❌ 비활성'} - {msg_1d}")
    print(f"   4H: {'✅ 활성' if is_4h else '❌ 비활성'} - {msg_4h}")
    
    if not is_1d and not is_4h:
        print("\n⚠️ 현재 캔들 마감 윈도우가 아닙니다. 매수가 발생하지 않습니다.")
        print("   (1D: 08:50~09:10 KST, 4H: 매 4시간±10분)")
    
    # 전략 체커 맵
    checkers = {
        "Morning Star": check_morning_star,
        "Squirrel (핀바)": check_squirrel,
        "Inverted Hammer": check_inverted_hammer,
        "Divergence": check_divergence,
        "Shooting Star": check_shooting_star,
        "Evening Star": check_evening_star,
        "Bearish Engulfing": check_bearish_engulfing,
        "Bearish Divergence": check_bearish_divergence,
    }
    
    # 상위 5개 종목만 분석
    test_symbols = symbols[:5]
    
    for symbol in test_symbols:
        print(f"\n{'='*70}")
        print(f"📈 {symbol}")
        print("="*70)
        
        try:
            df = get_bybit_candles(symbol, "D", 100)
            if df is None or len(df) < 20:
                print("   ❌ 데이터 가져오기 실패")
                continue
            
            current_price = df['close'].iloc[-1]
            print(f"   현재가: ${current_price:,.2f}")
            print("-"*70)
            
            for strategy_name, checker in checkers.items():
                result = checker(df.copy())
                
                status = "🟢 PASS" if result.get("pass") else "🔴 FAIL"
                print(f"\n   [{status}] {strategy_name}")
                
                if "conditions" in result:
                    for cond, val in result["conditions"].items():
                        print(f"       {cond}: {val}")
                elif "reason" in result:
                    print(f"       Reason: {result['reason']}")
                    
        except Exception as e:
            print(f"   ❌ 오류: {e}")
    
    print("\n" + "="*70)
    print("진단 완료")
    print("="*70)


if __name__ == "__main__":
    run_diagnosis()
