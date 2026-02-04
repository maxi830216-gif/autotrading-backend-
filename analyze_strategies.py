from services.upbit_client import UpbitClient
from services.whitelist_service import whitelist_service
from services.strategy_inverted_hammer import InvertedHammerStrategy
from services.strategy_morning import MorningStarStrategy
import pandas as pd
import time

# Create instances for both timeframes
hammer_1d = InvertedHammerStrategy(timeframe="day")
hammer_4h = InvertedHammerStrategy(timeframe="minute240")
morning_1d = MorningStarStrategy(timeframe="day")
morning_4h = MorningStarStrategy(timeframe="minute240")

# Get whitelist
whitelist = whitelist_service.get_whitelist()
markets = [coin["market"] for coin in whitelist[:20]]

print("=" * 100)
print("📊 감시 종목 전략 분석 리포트 (가중치 기반 신뢰도)")
print(f"분석 시간: {pd.Timestamp.now()}")
print("=" * 100)

# Inverted Hammer Analysis - Both timeframes
print("\n🔷 윗꼬리양봉 전략 (가중치 기반)")
print("-" * 100)

hammer_results = []
for market in markets:
    for tf, strategy, tf_label in [("day", hammer_1d, "1D"), ("minute240", hammer_4h, "4H")]:
        try:
            signal = strategy.analyze(market)
            hammer_results.append((market, tf_label, signal))
            
            status = "🟢" if signal.signal_type == "buy" else "⚪"
            conf = f"{signal.confidence*100:.0f}%"
            print(f"{status} {market:<10} ({tf_label}) | 신뢰도: {conf:<4} | {signal.reason[:70]}")
        except Exception as e:
            print(f"❌ {market:<10} ({tf_label}) | Error: {e}")
        time.sleep(0.05)

# Morning Star Analysis - Both timeframes
print("\n" + "=" * 100)
print("🔶 샛별형 전략 (가중치 기반)")
print("-" * 100)

morning_results = []
for market in markets:
    for tf, strategy, tf_label in [("day", morning_1d, "1D"), ("minute240", morning_4h, "4H")]:
        try:
            signal = strategy.analyze(market)
            morning_results.append((market, tf_label, signal))
            
            status = "🟢" if signal.signal_type == "buy" else "⚪"
            conf = f"{signal.confidence*100:.0f}%"
            reason = signal.reason[:70] if len(signal.reason) > 70 else signal.reason
            print(f"{status} {market:<10} ({tf_label}) | 신뢰도: {conf:<4} | {reason}")
        except Exception as e:
            print(f"❌ {market:<10} ({tf_label}) | Error: {e}")
        time.sleep(0.05)

# Summary
print("\n" + "=" * 100)
print("📝 매수 가능 종목 요약 (신뢰도 30% 이상)")
print("=" * 100)

# Filter results with confidence >= 30%
hammer_buys = [(m, tf, s) for m, tf, s in hammer_results if s.confidence >= 0.3]
morning_buys = [(m, tf, s) for m, tf, s in morning_results if s.confidence >= 0.3]

hammer_buys.sort(key=lambda x: x[2].confidence, reverse=True)
morning_buys.sort(key=lambda x: x[2].confidence, reverse=True)

if hammer_buys:
    print("\n🏆 윗꼬리양봉 TOP 5:")
    for m, tf, s in hammer_buys[:5]:
        print(f"   {m} ({tf}) - 신뢰도 {s.confidence*100:.0f}%")
else:
    print("\n🏆 윗꼬리양봉: 신뢰도 30% 이상 종목 없음")

if morning_buys:
    print("\n🏆 샛별형 TOP 5:")
    for m, tf, s in morning_buys[:5]:
        print(f"   {m} ({tf}) - 신뢰도 {s.confidence*100:.0f}%")
else:
    print("\n🏆 샛별형: 신뢰도 30% 이상 종목 없음")

print("\n" + "=" * 100)
print("💡 설정의 전략을 설정하면 위 종목들이 매수됩니다")
