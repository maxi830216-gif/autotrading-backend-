"""
Telegram Notification Service
Sends trade alerts and system notifications
"""
from typing import Optional
import asyncio
from telegram import Bot
from telegram.error import TelegramError

from models.database import SessionLocal, get_setting
from utils.logger import setup_logger
from utils.encryption import encryptor

logger = setup_logger(__name__)


class TelegramService:
    """Service for sending Telegram notifications"""
    
    def __init__(self):
        self._bot: Optional[Bot] = None
        self._chat_id: Optional[str] = None
        self._is_enabled = False
    
    def initialize(self):
        """Initialize Telegram bot from stored settings"""
        try:
            db = SessionLocal()
            
            token_encrypted = get_setting(db, "telegram_token")
            self._chat_id = get_setting(db, "telegram_chat_id")
            self._is_enabled = get_setting(db, "is_telegram_enabled") == "true"
            
            db.close()
            
            if token_encrypted and self._chat_id:
                token = encryptor.decrypt(token_encrypted)
                self._bot = Bot(token=token)
                logger.info("Telegram bot initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")
            self._bot = None
    
    def set_credentials(self, token: str, chat_id: str, is_enabled: bool = True):
        """Update Telegram credentials"""
        try:
            db = SessionLocal()
            from models.database import set_setting
            
            set_setting(db, "telegram_token", encryptor.encrypt(token))
            set_setting(db, "telegram_chat_id", chat_id)
            set_setting(db, "is_telegram_enabled", "true" if is_enabled else "false")
            
            db.close()
            
            self._chat_id = chat_id
            self._is_enabled = is_enabled
            self._bot = Bot(token=token)
            
            logger.info("Telegram credentials updated")
            
        except Exception as e:
            logger.error(f"Failed to set Telegram credentials: {e}")
    
    async def send_message_async(self, message: str) -> bool:
        """Send a message asynchronously"""
        if not self._is_enabled or not self._bot or not self._chat_id:
            return False
        
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
                parse_mode="HTML"
            )
            return True
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
    def send_message(self, message: str) -> bool:
        """Send a message synchronously"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If already in async context, create task
                asyncio.create_task(self.send_message_async(message))
                return True
            else:
                return loop.run_until_complete(self.send_message_async(message))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(self.send_message_async(message))
    
    def send_trade_alert(
        self,
        side: str,
        coin: str,
        price: float,
        quantity: float,
        strategy: str,
        pnl_percent: float = None
    ):
        """Send trade execution alert"""
        emoji = "🟢" if side.lower() == "buy" else "🔴"
        strategy_name = "상승 다람쥐" if strategy == "squirrel" else "샛별형"
        
        message = f"""
{emoji} <b>{side.upper()} 체결</b>

📊 <b>종목:</b> {coin}
💰 <b>가격:</b> {price:,.0f} KRW
📦 <b>수량:</b> {quantity:.8f}
🎯 <b>전략:</b> {strategy_name}
"""
        
        if pnl_percent is not None:
            pnl_emoji = "📈" if pnl_percent >= 0 else "📉"
            message += f"{pnl_emoji} <b>수익률:</b> {pnl_percent:+.2f}%\n"
        
        self.send_message(message)
    
    def send_system_alert(self, title: str, message: str, level: str = "info"):
        """Send system status alert"""
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "🚨",
            "success": "✅"
        }
        emoji = emoji_map.get(level, "ℹ️")
        
        formatted = f"""
{emoji} <b>{title}</b>

{message}
"""
        self.send_message(formatted)
    
    def send_panic_alert(self, sold_positions: list):
        """Send panic sell notification"""
        message = "🚨 <b>긴급 매도 실행 완료</b>\n\n"
        
        for pos in sold_positions:
            message += f"• {pos['market']}: {pos['executed_price']:,.0f} KRW\n"
        
        message += f"\n총 {len(sold_positions)}개 포지션 청산"
        
        self.send_message(message)
    
    async def test_connection(self) -> tuple[bool, str]:
        """Test Telegram connection"""
        if not self._bot:
            return False, "Bot not initialized"
        
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text="🔔 Upbit Trading Bot 연결 테스트 성공!"
            )
            return True, "Connection successful"
        except TelegramError as e:
            return False, str(e)
    
    def send_user_trade_alert(
        self,
        user_id: int,
        side: str,
        coin: str,
        price: float,
        quantity: float,
        strategy: str,
        pnl_percent: float = None,
        is_simulation: bool = True,
        confidence: float = None,
        total_krw: float = None,
        remaining_balance: float = None,
        entry_price: float = None,
        # ★ Phase 8: sell_ratio 제거 (100% 청산만 지원)
        mode: str = None,  # For Bybit - 'simulation' or 'real'
        exchange: str = "upbit",  # 'upbit' or 'bybit'
        leverage: int = None  # For Bybit leverage
    ):
        """Send trade alert to specific user's Telegram"""
        try:
            from models.database import SessionLocal, UserSettings
            
            db = SessionLocal()
            user_settings = db.query(UserSettings).filter(
                UserSettings.user_id == user_id
            ).first()
            
            if not user_settings or not user_settings.telegram_enabled:
                db.close()
                return
            
            if not user_settings.telegram_token or not user_settings.telegram_chat_id:
                db.close()
                return
            
            token = encryptor.decrypt(user_settings.telegram_token)
            chat_id = user_settings.telegram_chat_id
            db.close()
            
            # Determine simulation mode from mode parameter if provided
            if mode is not None:
                is_simulation = (mode == 'simulation')
            
            # Exchange-specific settings
            is_bybit = exchange.lower() == "bybit"
            currency = "USDT" if is_bybit else "KRW"
            
            # 코인 이름 정리
            if is_bybit:
                coin_name = coin.replace('USDT', '')
            else:
                coin_name = coin.replace('KRW-', '')
            
            # 모드 태그
            exchange_tag = "[Bybit]" if is_bybit else "[Upbit]"
            mode_tag = f"{exchange_tag}[모의]" if is_simulation else f"{exchange_tag}[실전]"
            
            # 전략 한글명 매핑
            strategy_map = {
                "squirrel": "다람쥐",
                "morning": "샛별형",
                "inverted_hammer": "윗꼬리양봉",
                "divergence": "다이버전스",
                "harmonic": "하모닉",
                "leading_diagonal": "리딩다이아"
            }
            # 전략명에서 타임프레임 분리
            base_strategy = strategy.split('(')[0] if '(' in strategy else strategy
            strategy_name = strategy_map.get(base_strategy, strategy)
            
            # 레버리지 문자열
            leverage_str = f" (레버리지 {leverage}x)" if leverage else ""
            
            if side.lower() == "buy":
                # 매수 메시지
                conf_str = f" 신뢰도 {int(confidence*100)}%" if confidence else ""
                
                if is_bybit:
                    # Bybit 매수 메시지
                    message = f"""{mode_tag} {coin_name} 롱 진입{leverage_str}

- 종목: {coin_name}
- 진입가격: ${price:,.2f}
- 진입수량: {quantity:.4f}
- 진입전략: {strategy_name}{conf_str}"""
                else:
                    # Upbit 매수 메시지
                    message = f"""{mode_tag} {coin_name} 매수 하였습니다.

- 종목: {coin_name}
- 진입가격: {price:,.0f} KRW
- 진입수량: {quantity:.8f}
- 진입전략: {strategy_name}{conf_str}
- 총 구매 KRW: {total_krw:,.0f} KRW
- 보유금액: {remaining_balance:,.0f} KRW""" if total_krw and remaining_balance else f"""{mode_tag} {coin_name} 매수 하였습니다.

- 종목: {coin_name}
- 진입가격: {price:,.0f} KRW
- 진입수량: {quantity:.8f}
- 진입전략: {strategy_name}{conf_str}"""
            else:
                # 매도/청산 메시지
                # ★ Phase 8: sell_ratio 제거 - 100% 청산만
                
                if is_bybit:
                    # Bybit 청산 메시지
                    pnl_str = f"\n- 수익률: {pnl_percent:+.2f}%" if pnl_percent is not None else ""
                    message = f"""{mode_tag} {coin_name} 청산{leverage_str}

- 종목: {coin_name}
- 청산가격: ${price:,.2f}
- 청산수량: {quantity:.4f}
- 진입전략: {strategy_name}{pnl_str}"""
                else:
                    # Upbit 청산 메시지
                    sell_amount = price * quantity
                    
                    message = f"""{mode_tag} {coin_name} 청산 하였습니다.

- 종목: {coin_name}
- 진입가격: {entry_price:,.0f} KRW
- 청산가격: {price:,.0f} KRW
- 청산수량: {quantity:.8f}
- 진입전략: {strategy_name}
- 청산금액: {sell_amount:,.0f} KRW
- 보유금액: {remaining_balance:,.0f} KRW
- 수익률: {pnl_percent:+.2f}%""" if entry_price and remaining_balance else f"""{mode_tag} {coin_name} 청산 하였습니다.

- 종목: {coin_name}
- 청산가격: {price:,.0f} KRW
- 청산수량: {quantity:.8f}
- 진입전략: {strategy_name}
- 수익률: {pnl_percent:+.2f}%""" if pnl_percent else f"""{mode_tag} {coin_name} 청산 하였습니다.

- 종목: {coin_name}
- 청산가격: {price:,.0f} KRW
- 청산수량: {quantity:.8f}
- 진입전략: {strategy_name}"""
            
            # Send message
            import asyncio
            bot = Bot(token=token)
            
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(bot.send_message(chat_id=chat_id, text=message))
                else:
                    loop.run_until_complete(bot.send_message(chat_id=chat_id, text=message))
            except RuntimeError:
                asyncio.run(bot.send_message(chat_id=chat_id, text=message))
                
        except Exception as e:
            logger.error(f"Failed to send user trade alert: {e}")
    
    def send_to_all_enabled_users(
        self,
        side: str,
        coin: str,
        price: float,
        quantity: float,
        strategy: str,
        pnl_percent: float = None,
        is_simulation: bool = True
    ):
        """Send trade alert to all users with Telegram enabled"""
        try:
            from models.database import SessionLocal, UserSettings
            
            db = SessionLocal()
            users = db.query(UserSettings).filter(
                UserSettings.telegram_enabled == True,
                UserSettings.telegram_token != None,
                UserSettings.telegram_chat_id != None
            ).all()
            
            for user_settings in users:
                self.send_user_trade_alert(
                    user_id=user_settings.user_id,
                    side=side,
                    coin=coin,
                    price=price,
                    quantity=quantity,
                    strategy=strategy,
                    pnl_percent=pnl_percent,
                    is_simulation=is_simulation
                )
            
            db.close()
            
        except Exception as e:
            logger.error(f"Failed to send to all users: {e}")


    def send_buy_preview_alert(
        self,
        user_id: int,
        exchange: str,  # 'upbit' or 'bybit'
        mode: str,  # 'simulation' or 'real'
        timeframe: str,  # '1D' or '4H'
        preview_time: str,  # e.g., "2024-01-05 09:00"
        buy_signals: list  # List of signal dicts
    ):
        """
        Send buy preview alert to a specific user
        
        buy_signals format:
        [
            {
                'strategy': 'squirrel',
                'coin': 'KRW-BTC',
                'confidence': 0.85,
                'entry_price': 150000000,
                'stop_loss': 142500000,
                'take_profit_1': 157500000
            },
            ...
        ]
        """
        try:
            from models.database import SessionLocal, UserSettings
            
            db = SessionLocal()
            user_settings = db.query(UserSettings).filter(
                UserSettings.user_id == user_id
            ).first()
            
            if not user_settings or not user_settings.telegram_enabled:
                db.close()
                return
            
            if not user_settings.telegram_token or not user_settings.telegram_chat_id:
                db.close()
                return
            
            token = encryptor.decrypt(user_settings.telegram_token)
            chat_id = user_settings.telegram_chat_id
            db.close()
            
            # Build message header
            exchange_tag = "Upbit" if exchange.lower() == "upbit" else "Bybit"
            mode_tag = "모의" if mode == "simulation" else "실전"
            tf_tag = "일일" if timeframe == "1D" else "4시간"
            currency = "원" if exchange.lower() == "upbit" else "USDT"
            
            header = f"📢 [{exchange_tag} {tf_tag} {mode_tag} 매수 예정] {preview_time}\n"
            
            # Strategy emoji mapping
            strategy_emoji = {
                "squirrel": "🐿️",
                "morning": "⭐",
                "inverted_hammer": "🔨",
                "divergence": "📊",
                "harmonic": "🦋",
                "leading_diagonal": "💎"
            }
            
            strategy_name_kr = {
                "squirrel": "다람쥐 (Squirrel)",
                "morning": "샛별형 (Morning Star)",
                "inverted_hammer": "윗꼬리양봉 (Inverted Hammer)",
                "divergence": "다이버전스 (Divergence)",
                "harmonic": "하모닉 (Harmonic)",
                "leading_diagonal": "리딩다이아 (Leading Diagonal)"
            }
            
            if not buy_signals:
                # No signals
                message = f"{header}\n📭 매수 예정 종목이 없습니다."
            else:
                message = header + "\n"
                
                for i, signal in enumerate(buy_signals):
                    if i > 0:
                        message += "\n"
                    
                    base_strategy = signal['strategy'].split('(')[0] if '(' in signal['strategy'] else signal['strategy']
                    emoji = strategy_emoji.get(base_strategy, "📈")
                    name = strategy_name_kr.get(base_strategy, signal['strategy'])
                    
                    # Coin name
                    if exchange.lower() == "upbit":
                        coin_display = signal['coin'].replace('KRW-', '')
                    else:
                        coin_display = signal['coin'].replace('USDT', '')
                    
                    # Format prices
                    entry = signal.get('entry_price', 0)
                    stop = signal.get('stop_loss', 0)
                    tp1 = signal.get('take_profit_1', 0)
                    confidence = signal.get('confidence', 0)
                    
                    if exchange.lower() == "upbit":
                        entry_str = f"{entry:,.0f}원"
                        stop_str = f"{stop:,.0f}원" if stop else "N/A"
                        tp1_str = f"{tp1:,.0f}원" if tp1 else "N/A"
                    else:
                        entry_str = f"${entry:,.2f}"
                        stop_str = f"${stop:,.2f}" if stop else "N/A"
                        tp1_str = f"${tp1:,.2f}" if tp1 else "N/A"
                    
                    # Calculate percentages
                    stop_pct = ((stop - entry) / entry * 100) if stop and entry else 0
                    tp1_pct = ((tp1 - entry) / entry * 100) if tp1 and entry else 0
                    
                    message += f"""{emoji} 전략: {name}
🪙 종목: {coin_display}
📊 신뢰도: {int(confidence * 100)}%
💰 예상 진입가: {entry_str}
🔴 손절선: {stop_str} ({stop_pct:+.1f}%)
🟢 1차 익절: {tp1_str} ({tp1_pct:+.1f}%)
"""
            
            # Send message
            import asyncio
            bot = Bot(token=token)
            
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(bot.send_message(chat_id=chat_id, text=message))
                else:
                    loop.run_until_complete(bot.send_message(chat_id=chat_id, text=message))
            except RuntimeError:
                asyncio.run(bot.send_message(chat_id=chat_id, text=message))
            
            logger.info(f"Sent buy preview alert to user {user_id}: {len(buy_signals)} signals for {exchange} {mode}")
                
        except Exception as e:
            logger.error(f"Failed to send buy preview alert to user {user_id}: {e}")


# Global instance
telegram_service = TelegramService()

