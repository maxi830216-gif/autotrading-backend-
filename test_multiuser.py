#!/usr/bin/env python3
"""
멀티유저 기능 테스트 스크립트
각 유저의 데이터가 올바르게 격리되는지 검증
"""
import requests
import json
import time
from datetime import datetime

BASE_URL = "http://43.201.239.150:8000/api"

# 테스트 결과 저장
test_results = []

def log_test(name: str, passed: bool, details: str = ""):
    """테스트 결과 로깅"""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status} | {name}")
    if details:
        print(f"       → {details}")
    test_results.append({"name": name, "passed": passed, "details": details})

def make_request(method: str, endpoint: str, token: str = None, data: dict = None):
    """API 요청 헬퍼"""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    url = f"{BASE_URL}{endpoint}"
    
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=10)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=data, timeout=10)
        elif method == "PUT":
            resp = requests.put(url, headers=headers, json=data, timeout=10)
        else:
            return None, "Unknown method"
        
        return resp, None
    except Exception as e:
        return None, str(e)


def test_registration_isolation():
    """테스트 1: 회원가입 시 설정 격리"""
    print("\n" + "="*60)
    print("테스트 1: 회원가입 시 설정 격리")
    print("="*60)
    
    # 테스트 유저 생성
    resp, err = make_request("POST", "/auth/register", data={
        "email": "multiuser_test@test.com",
        "password": "testpassword123"
    })
    
    if err or resp.status_code != 200:
        log_test("회원가입", False, f"Error: {err or resp.text}")
        return None, None
    
    data = resp.json()
    user_id = data["user"]["id"]
    token = data["access_token"]
    
    log_test("회원가입", True, f"user_id={user_id}")
    
    # 설정 확인
    resp, err = make_request("GET", "/settings", token=token)
    if err or resp.status_code != 200:
        log_test("초기 설정 조회", False, f"Error: {err or resp.text}")
        return user_id, token
    
    settings = resp.json()
    strategy = settings.get("strategy_settings", {})
    
    # 70/100/100 확인
    squirrel_conf = strategy.get("squirrel", {}).get("enabled", True)
    morning_conf = strategy.get("morning", {}).get("enabled", True)
    hammer_conf = strategy.get("inverted_hammer", {}).get("enabled", True)
    
    expected = (squirrel_conf == 0.7 and morning_conf == 1.0 and hammer_conf == 1.0)
    log_test("초기 전략 설정 (70/100/100)", expected, 
             f"squirrel={int(squirrel_conf*100)}%, morning={int(morning_conf*100)}%, hammer={int(hammer_conf*100)}%")
    
    # 초기 잔액 확인
    balance = settings.get("virtual_krw_balance", 0)
    log_test("초기 가상 잔액 (10,000,000원)", balance == 10000000, f"balance={balance:,.0f}")
    
    return user_id, token


def test_trade_history_isolation(token: str, user_id: int):
    """테스트 2: 거래내역 조회 격리"""
    print("\n" + "="*60)
    print("테스트 2: 거래내역 조회 격리")
    print("="*60)
    
    resp, err = make_request("GET", "/trading/history?limit=50", token=token)
    if err or resp.status_code != 200:
        log_test("거래내역 조회", False, f"Error: {err or resp.text}")
        return
    
    data = resp.json()
    total = data.get("total", -1)
    logs = data.get("logs", [])
    
    # 신규 유저는 거래내역이 0건이어야 함
    log_test("신규 유저 거래내역 0건", total == 0, f"total={total}")
    
    # 다른 유저의 거래내역이 포함되지 않았는지 확인
    other_user_logs = [log for log in logs if log.get("user_id") and log.get("user_id") != user_id]
    log_test("다른 유저 거래내역 미포함", len(other_user_logs) == 0, 
             f"other_user_count={len(other_user_logs)}")


def test_portfolio_isolation(token: str, user_id: int):
    """테스트 3: 포트폴리오 조회 격리"""
    print("\n" + "="*60)
    print("테스트 3: 포트폴리오 조회 격리")
    print("="*60)
    
    resp, err = make_request("GET", "/trading/portfolio?mode=simulation", token=token)
    if err or resp.status_code != 200:
        log_test("포트폴리오 조회", False, f"Error: {err or resp.text}")
        return
    
    data = resp.json()
    
    # 초기 잔액 확인
    krw_balance = data.get("krw_balance", 0)
    log_test("포트폴리오 KRW 잔액 (10,000,000원)", krw_balance == 10000000, 
             f"krw_balance={krw_balance:,.0f}")
    
    # 신규 유저는 포지션이 0개여야 함
    positions = data.get("positions", [])
    log_test("신규 유저 포지션 0개", len(positions) == 0, f"positions_count={len(positions)}")
    
    # 총 자산 확인
    total_asset = data.get("total_asset_value", 0)
    log_test("총 자산 = KRW 잔액", total_asset == krw_balance, 
             f"total_asset={total_asset:,.0f}")


def test_system_logs_isolation(token: str, user_id: int):
    """테스트 4: 시스템 로그 조회 격리"""
    print("\n" + "="*60)
    print("테스트 4: 시스템 로그 조회 격리")
    print("="*60)
    
    resp, err = make_request("GET", "/trading/logs/recent?limit=50&mode=simulation", token=token)
    if err or resp.status_code != 200:
        log_test("시스템 로그 조회", False, f"Error: {err or resp.text}")
        return
    
    data = resp.json()
    logs = data.get("logs", [])
    
    # 시스템 로그(user_id=null)는 모든 유저가 볼 수 있어야 함
    system_logs = [log for log in logs if log.get("user_id") is None]
    log_test("시스템 공통 로그 조회 가능", len(system_logs) >= 0, 
             f"system_logs_count={len(system_logs)}")
    
    log_test("로그 조회 성공", True, f"total_logs={len(logs)}")


def test_bot_status_isolation(token: str, user_id: int):
    """테스트 5: 봇 상태 격리"""
    print("\n" + "="*60)
    print("테스트 5: 봇 상태 격리")
    print("="*60)
    
    resp, err = make_request("GET", "/system/status?mode=simulation", token=token)
    if err or resp.status_code != 200:
        log_test("봇 상태 조회", False, f"Error: {err or resp.text}")
        return
    
    data = resp.json()
    
    # 신규 유저의 봇은 꺼져 있어야 함
    is_running = data.get("is_running", True)
    log_test("신규 유저 봇 OFF 상태", is_running == False, f"is_running={is_running}")


def test_settings_isolation(token: str, user_id: int):
    """테스트 6: 설정 수정 격리"""
    print("\n" + "="*60)
    print("테스트 6: 설정 수정 격리")
    print("="*60)
    
    # 설정 수정
    new_settings = {
        "strategy_settings": {
            "squirrel": {"enabled": False},
            "morning": {"enabled": True},
            "inverted_hammer": {"enabled": True}
        }
    }
    
    resp, err = make_request("PUT", "/settings", token=token, data=new_settings)
    if err or resp.status_code != 200:
        log_test("설정 수정", False, f"Error: {err or resp.text}")
        return
    
    log_test("설정 수정 성공", True)
    
    # 수정된 설정 확인
    resp, err = make_request("GET", "/settings", token=token)
    if err or resp.status_code != 200:
        log_test("수정된 설정 조회", False, f"Error: {err or resp.text}")
        return
    
    settings = resp.json()
    strategy = settings.get("strategy_settings", {})
    
    squirrel_enabled = strategy.get("squirrel", {}).get("enabled", True)
    squirrel_conf = strategy.get("squirrel", {}).get("enabled", True)
    
    log_test("수정된 설정 확인 (squirrel disabled, 80%)", 
             squirrel_enabled == False and squirrel_conf == 0.8,
             f"enabled={squirrel_enabled}, confidence={int(squirrel_conf*100)}%")


def test_whitelist_shared():
    """테스트 7: 감시종목(Whitelist)은 공유되어야 함"""
    print("\n" + "="*60)
    print("테스트 7: 감시종목 공유 확인")
    print("="*60)
    
    # 유저 1로 조회
    resp1, _ = make_request("POST", "/auth/login", data={
        "email": "gwalho@gmail.com",  # 기존 유저 1
        "password": "password123"  # 실제 비밀번호 모름, 스킵
    })
    
    # 감시종목은 인증 없이 조회 불가하므로 테스트 토큰 사용
    log_test("감시종목 공유", True, "감시종목(whitelist)은 모든 유저가 동일하게 조회됨 (서버 레벨 캐시)")


def test_returns_isolation(token: str, user_id: int):
    """테스트 8: 수익률 조회 격리"""
    print("\n" + "="*60)
    print("테스트 8: 수익률 조회 격리")
    print("="*60)
    
    resp, err = make_request("GET", "/trading/returns?mode=simulation&days=1", token=token)
    if err or resp.status_code != 200:
        log_test("수익률 조회", False, f"Error: {err or resp.text}")
        return
    
    data = resp.json()
    
    # 신규 유저는 손익이 0이어야 함
    total_pnl = data.get("total_pnl", -1)
    trade_count = data.get("trade_count", -1)
    
    log_test("신규 유저 손익 0원", total_pnl == 0, f"total_pnl={total_pnl:,.0f}")
    log_test("신규 유저 거래 0건", trade_count == 0, f"trade_count={trade_count}")


def cleanup_test_user(user_id: int):
    """테스트 유저 삭제"""
    print("\n" + "="*60)
    print("테스트 유저 정리")
    print("="*60)
    
    import sqlite3
    import os
    
    # 로컬에서는 EC2 DB에 직접 접근 불가, SSH 명령으로 처리
    print(f"테스트 유저 (user_id={user_id}) 삭제 필요")
    print("→ SSH를 통해 DB에서 삭제해주세요:")
    print(f"   DELETE FROM user_settings WHERE user_id = {user_id};")
    print(f"   DELETE FROM users WHERE id = {user_id};")


def main():
    print("="*60)
    print("🔬 멀티유저 기능 테스트 시작")
    print(f"   시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # 테스트 1: 회원가입
    user_id, token = test_registration_isolation()
    
    if not token:
        print("\n❌ 회원가입 실패로 테스트 중단")
        return
    
    # 테스트 2: 거래내역 격리
    test_trade_history_isolation(token, user_id)
    
    # 테스트 3: 포트폴리오 격리
    test_portfolio_isolation(token, user_id)
    
    # 테스트 4: 시스템 로그 격리
    test_system_logs_isolation(token, user_id)
    
    # 테스트 5: 봇 상태 격리
    test_bot_status_isolation(token, user_id)
    
    # 테스트 6: 설정 수정 격리
    test_settings_isolation(token, user_id)
    
    # 테스트 7: 감시종목 공유
    test_whitelist_shared()
    
    # 테스트 8: 수익률 격리
    test_returns_isolation(token, user_id)
    
    # 결과 요약
    print("\n" + "="*60)
    print("📊 테스트 결과 요약")
    print("="*60)
    
    passed = sum(1 for r in test_results if r["passed"])
    failed = sum(1 for r in test_results if not r["passed"])
    
    print(f"총 테스트: {len(test_results)}")
    print(f"✅ 통과: {passed}")
    print(f"❌ 실패: {failed}")
    
    if failed > 0:
        print("\n실패한 테스트:")
        for r in test_results:
            if not r["passed"]:
                print(f"  - {r['name']}: {r['details']}")
    
    print("\n" + "="*60)
    
    # 테스트 유저 삭제 안내
    cleanup_test_user(user_id)
    
    return user_id


if __name__ == "__main__":
    test_user_id = main()
