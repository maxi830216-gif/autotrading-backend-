#!/usr/bin/env python3
"""
Local Test Runner CLI
로컬 테스트 실행 CLI

사용법:
    python -m local_testing.run_local_test --exchange upbit --strategy morning_star --case take_profit
    python -m local_testing.run_local_test --exchange upbit --all
    python -m local_testing.run_local_test --all
    python -m local_testing.run_local_test --list
"""
import sys
import argparse
import time
from pathlib import Path
from typing import List, Dict

# 프로젝트 루트 경로 추가
backend_path = Path(__file__).parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from local_testing.test_scheduler import LocalTestScheduler, TestResult
from local_testing.strategies.all_scenarios import (
    UPBIT_SCENARIOS, BYBIT_SCENARIOS,
    get_all_upbit_scenarios, get_all_bybit_scenarios, get_scenario
)


class Colors:
    """터미널 색상"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header():
    """헤더 출력"""
    print(f"""
{Colors.CYAN}================================{Colors.RESET}
{Colors.BOLD}🧪 Auto Trading Local Test Runner{Colors.RESET}
{Colors.CYAN}================================{Colors.RESET}
""")


def print_scenario_list():
    """사용 가능한 시나리오 목록 출력"""
    print_header()
    
    print(f"{Colors.BLUE}📈 UPBIT Scenarios:{Colors.RESET}")
    for strategy in UPBIT_SCENARIOS:
        cases = list(UPBIT_SCENARIOS[strategy].keys())
        print(f"  {strategy}: {', '.join(cases)}")
    
    print()
    print(f"{Colors.BLUE}📊 BYBIT Scenarios:{Colors.RESET}")
    for strategy in BYBIT_SCENARIOS:
        cases = list(BYBIT_SCENARIOS[strategy].keys())
        direction = '(Short)' if strategy in ['shooting_star', 'bearish_divergence', 'evening_star', 'bearish_engulfing', 'leading_diagonal_breakdown'] else '(Long)'
        print(f"  {strategy} {direction}: {', '.join(cases)}")
    
    print()
    print(f"Total: {len(UPBIT_SCENARIOS) * 3} Upbit + {len(BYBIT_SCENARIOS) * 3} Bybit = {(len(UPBIT_SCENARIOS) + len(BYBIT_SCENARIOS)) * 3} scenarios")


def run_single_test(exchange: str, strategy: str, case: str) -> TestResult:
    """단일 테스트 실행"""
    scenario = get_scenario(exchange, strategy, case)
    if not scenario:
        return TestResult(
            scenario_name=f"{strategy}.{case}",
            strategy=strategy,
            exchange=exchange,
            expected_exit=None,
            actual_entry=False,
            actual_exit_reason=None,
            passed=False,
            error="Scenario not found"
        )
    
    scenario['strategy'] = strategy
    scheduler = LocalTestScheduler(exchange, scenario)
    return scheduler.run_full_scenario()


def print_result(result: TestResult, index: int = None, total: int = None):
    """결과 출력"""
    prefix = f"[{index}/{total}] " if index and total else ""
    
    if result.passed:
        status = f"{Colors.GREEN}✅ PASSED{Colors.RESET}"
    else:
        status = f"{Colors.RED}❌ FAILED{Colors.RESET}"
    
    print(f"{prefix}{result.strategy}.{result.scenario_name.split('_')[-1] if '_' in result.scenario_name else result.scenario_name}")
    print(f"       {status}")
    
    if result.error:
        print(f"       {Colors.RED}Error: {result.error}{Colors.RESET}")
    elif result.trades:
        trade = result.trades[-1]
        direction_emoji = "📈" if trade.direction == 'long' else "📉"
        pnl_color = Colors.GREEN if trade.pnl_percent > 0 else Colors.RED
        print(f"       {direction_emoji} Entry: #{trade.entry_tick} @ {trade.entry_price:,.2f}")
        print(f"       Exit: #{trade.exit_tick} @ {trade.exit_price:,.2f} ({trade.exit_reason})")
        print(f"       {pnl_color}PnL: {trade.pnl_percent:+.2f}%{Colors.RESET}")
    elif result.expected_exit is None:
        print(f"       No entry as expected ✓")
    else:
        print(f"       Expected: {result.expected_exit}, Actual: {result.actual_exit_reason}")
    print()


def run_exchange_tests(exchange: str) -> List[TestResult]:
    """거래소별 전체 테스트 실행"""
    if exchange == 'upbit':
        scenarios = get_all_upbit_scenarios()
        emoji = "📈"
    else:
        scenarios = get_all_bybit_scenarios()
        emoji = "📊"
    
    print(f"\n{emoji} {Colors.BOLD}{exchange.upper()} Results{Colors.RESET}")
    print("-" * 40)
    
    results = []
    start_time = time.time()
    
    for i, scenario in enumerate(scenarios, 1):
        scenario_copy = scenario.copy()
        strategy = scenario_copy.pop('strategy', 'unknown')
        scenario_copy['strategy'] = strategy
        
        scheduler = LocalTestScheduler(exchange, scenario_copy)
        result = scheduler.run_full_scenario()
        results.append(result)
        
        print_result(result, i, len(scenarios))
    
    elapsed = time.time() - start_time
    passed = sum(1 for r in results if r.passed)
    
    print(f"{exchange.upper()} Total: {Colors.GREEN}{passed}{Colors.RESET}/{len(results)} PASSED ({elapsed:.2f}s)")
    
    return results


def run_all_tests():
    """전체 테스트 실행"""
    print_header()
    
    all_results = []
    
    # Upbit 테스트
    upbit_results = run_exchange_tests('upbit')
    all_results.extend(upbit_results)
    
    # Bybit 테스트
    bybit_results = run_exchange_tests('bybit')
    all_results.extend(bybit_results)
    
    # 최종 요약
    print(f"\n{Colors.CYAN}================================{Colors.RESET}")
    print(f"{Colors.BOLD}📊 Final Summary{Colors.RESET}")
    print(f"{Colors.CYAN}================================{Colors.RESET}")
    
    upbit_passed = sum(1 for r in upbit_results if r.passed)
    bybit_passed = sum(1 for r in bybit_results if r.passed)
    total_passed = upbit_passed + bybit_passed
    total = len(all_results)
    
    upbit_status = f"{Colors.GREEN}✅{Colors.RESET}" if upbit_passed == len(upbit_results) else f"{Colors.RED}❌{Colors.RESET}"
    bybit_status = f"{Colors.GREEN}✅{Colors.RESET}" if bybit_passed == len(bybit_results) else f"{Colors.RED}❌{Colors.RESET}"
    
    print(f"Upbit:  {upbit_passed}/{len(upbit_results)} {upbit_status}")
    print(f"Bybit:  {bybit_passed}/{len(bybit_results)} {bybit_status}")
    print(f"{Colors.BOLD}Total:  {total_passed}/{total}{Colors.RESET}")
    
    if total_passed == total:
        print(f"\n{Colors.GREEN}🎉 All tests passed!{Colors.RESET}")
    else:
        print(f"\n{Colors.RED}⚠️ Some tests failed{Colors.RESET}")
    
    print(f"{Colors.CYAN}================================{Colors.RESET}\n")
    
    return all_results


def main():
    parser = argparse.ArgumentParser(
        description="Trading Bot Local Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m local_testing.run_local_test --list
    python -m local_testing.run_local_test --exchange upbit --strategy morning_star --case take_profit
    python -m local_testing.run_local_test --exchange upbit --all
    python -m local_testing.run_local_test --all
        """
    )
    
    parser.add_argument("--exchange", choices=["upbit", "bybit"], help="거래소 선택")
    parser.add_argument("--strategy", help="전략 이름 (예: morning_star)")
    parser.add_argument("--case", help="테스트 케이스 (take_profit, stop_loss, no_signal)")
    parser.add_argument("--all", action="store_true", help="모든 시나리오 실행")
    parser.add_argument("--list", action="store_true", help="시나리오 목록 출력")
    
    args = parser.parse_args()
    
    if args.list:
        print_scenario_list()
        return
    
    if args.all:
        if args.exchange:
            # 특정 거래소 전체 테스트
            print_header()
            run_exchange_tests(args.exchange)
        else:
            # 전체 테스트
            run_all_tests()
        return
    
    if args.exchange and args.strategy and args.case:
        # 단일 테스트
        print_header()
        print(f"Exchange: {args.exchange}")
        print(f"Strategy: {args.strategy}")
        print(f"Case: {args.case}")
        print()
        
        result = run_single_test(args.exchange, args.strategy, args.case)
        print_result(result)
        return
    
    # 인자 부족
    parser.print_help()


if __name__ == "__main__":
    main()
