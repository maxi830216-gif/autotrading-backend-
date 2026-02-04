"""
Settings API Router
API keys, Telegram configuration - Per-user settings
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json

from models.database import get_db, User, UserSettings
from models.schemas import (
    TelegramTestRequest,
    TelegramTestResponse
)
from services.upbit_client import upbit_client
from services.telegram_service import telegram_service
from routers.auth import get_current_user
from utils.encryption import encryptor
from utils.logger import setup_logger
import asyncio

logger = setup_logger(__name__)

router = APIRouter()

# Default strategy configurations (★ Phase 9: min_confidence 제거)
DEFAULT_STRATEGY_SETTINGS = {
    "squirrel": {
        "enabled": True,
        "name": "다람쥐 전략",
        "description": "일봉 기준 상승 추세에서 눌림목 매수",
        "timeframe": "1D"
    },
    "morning": {
        "enabled": True,
        "name": "샛별형 전략",
        "description": "일봉/4시간봉 기준 과매도 구간 반등 매수",
        "timeframe": "1D, 4H"
    },
    "inverted_hammer": {
        "enabled": True,
        "name": "윗꼬리양봉 전략",
        "description": "일봉/4시간봉 기준 하락 브레이크 후 반등 매수 (거래량 스파이크 필수)",
        "timeframe": "1D, 4H"
    },
    "divergence": {
        "enabled": False,
        "name": "다이버전스 전략",
        "description": "가격-RSI 괴리를 통한 바닥 반전 감지",
        "timeframe": "1D"
    },
    "harmonic": {
        "enabled": False,
        "name": "하모닉 패턴 전략",
        "description": "피보나치 비율 기반 정밀 반전 지점 감지 (가틀리/배트)",
        "timeframe": "1D"
    },
    "leading_diagonal": {
        "enabled": False,
        "name": "리딩 다이아고날 전략",
        "description": "하락 쐐기 패턴 상단 돌파 시 상승 추세 시작 감지",
        "timeframe": "1D"
    }
}


# Response/Request models for per-user settings
class UserSettingsResponse(BaseModel):
    upbit_access_key: str = ""  # Masked
    upbit_secret_key: str = ""  # Masked
    telegram_token: str = ""  # Masked
    telegram_chat_id: str = ""
    telegram_enabled: bool = False
    strategy_settings: Dict[str, Any] = {}
    hard_cap_ratio: float = 0.5
    virtual_krw_balance: float = 10000000


class UserSettingsUpdateRequest(BaseModel):
    upbit_access_key: Optional[str] = None
    upbit_secret_key: Optional[str] = None
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: Optional[bool] = None
    strategy_settings: Optional[Dict[str, Any]] = None
    hard_cap_ratio: Optional[float] = None
    virtual_krw_balance: Optional[float] = None


class StrategySettingsRequest(BaseModel):
    strategy_id: str  # e.g., "squirrel", "morning"
    enabled: Optional[bool] = None
    # ★ Phase 9: min_confidence 제거됨


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.get("", response_model=UserSettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    exchange: str = "upbit"  # "upbit" or "bybit"
):
    """Get current user's settings (with masked sensitive data)"""
    try:
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if not user_settings:
            # Create default settings if not exist
            user_settings = UserSettings(
                user_id=current_user.id,
                strategy_settings=json.dumps(DEFAULT_STRATEGY_SETTINGS)
            )
            db.add(user_settings)
            db.commit()
            db.refresh(user_settings)
        
        # Decrypt and mask for display (with fallback on failure)
        try:
            access_key = encryptor.decrypt(user_settings.upbit_access_key) if user_settings.upbit_access_key else ""
        except Exception:
            access_key = ""
            logger.warning("Failed to decrypt upbit_access_key, using empty string")
        
        try:
            secret_key = encryptor.decrypt(user_settings.upbit_secret_key) if user_settings.upbit_secret_key else ""
        except Exception:
            secret_key = ""
            logger.warning("Failed to decrypt upbit_secret_key, using empty string")
        
        try:
            telegram_token = encryptor.decrypt(user_settings.telegram_token) if user_settings.telegram_token else ""
        except Exception:
            telegram_token = ""
            logger.warning("Failed to decrypt telegram_token, using empty string")
        
        # Parse strategy settings based on exchange
        if exchange == "bybit":
            settings_field = user_settings.bybit_strategy_settings
        else:
            settings_field = user_settings.strategy_settings or user_settings.upbit_strategy_settings
        
        try:
            strategy_settings = json.loads(settings_field) if settings_field else DEFAULT_STRATEGY_SETTINGS
        except json.JSONDecodeError:
            strategy_settings = DEFAULT_STRATEGY_SETTINGS
        
        # Merge with defaults (for new strategies)
        for key, default_value in DEFAULT_STRATEGY_SETTINGS.items():
            if key not in strategy_settings:
                strategy_settings[key] = default_value
        
        return UserSettingsResponse(
            upbit_access_key=encryptor.mask_key(access_key) if access_key else "",
            upbit_secret_key=encryptor.mask_key(secret_key) if secret_key else "",
            telegram_token=encryptor.mask_key(telegram_token) if telegram_token else "",
            telegram_chat_id=user_settings.telegram_chat_id or "",
            telegram_enabled=user_settings.telegram_enabled,
            strategy_settings=strategy_settings,
            hard_cap_ratio=user_settings.hard_cap_ratio,
            virtual_krw_balance=user_settings.virtual_krw_balance
        )
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("")
async def update_settings(
    settings: UserSettingsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    exchange: str = "upbit"  # "upbit" or "bybit"
):
    """Update current user's settings"""
    try:
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if not user_settings:
            user_settings = UserSettings(
                user_id=current_user.id,
                strategy_settings=json.dumps(DEFAULT_STRATEGY_SETTINGS)
            )
            db.add(user_settings)
        
        updated_fields = []
        
        # Update Upbit API keys (encrypted)
        if settings.upbit_access_key:
            user_settings.upbit_access_key = encryptor.encrypt(settings.upbit_access_key)
            updated_fields.append("upbit_access_key")
        
        if settings.upbit_secret_key:
            user_settings.upbit_secret_key = encryptor.encrypt(settings.upbit_secret_key)
            updated_fields.append("upbit_secret_key")
        
        # Update Telegram settings (token encrypted)
        if settings.telegram_token:
            user_settings.telegram_token = encryptor.encrypt(settings.telegram_token)
            updated_fields.append("telegram_token")
        
        if settings.telegram_chat_id is not None:
            user_settings.telegram_chat_id = settings.telegram_chat_id
            updated_fields.append("telegram_chat_id")
        
        if settings.telegram_enabled is not None:
            user_settings.telegram_enabled = settings.telegram_enabled
            updated_fields.append("telegram_enabled")
        
        # Update strategy settings (JSON) - based on exchange
        if settings.strategy_settings is not None:
            if exchange == "bybit":
                user_settings.bybit_strategy_settings = json.dumps(settings.strategy_settings)
                updated_fields.append("bybit_strategy_settings")
            else:
                user_settings.strategy_settings = json.dumps(settings.strategy_settings)
                updated_fields.append("strategy_settings")
        
        if settings.hard_cap_ratio is not None:
            user_settings.hard_cap_ratio = settings.hard_cap_ratio
            updated_fields.append("hard_cap_ratio")
        
        if settings.virtual_krw_balance is not None:
            user_settings.virtual_krw_balance = settings.virtual_krw_balance
            updated_fields.append("virtual_krw_balance")
        
        db.commit()
        
        return {
            "success": True,
            "updated_fields": updated_fields,
            "message": f"Updated {len(updated_fields)} setting(s)"
        }
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/telegram/test", response_model=TelegramTestResponse)
async def test_telegram(
    request: TelegramTestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Send a test message to the user's Telegram"""
    try:
        from telegram import Bot
        from telegram.error import TelegramError
        
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if not user_settings or not user_settings.telegram_token or not user_settings.telegram_chat_id:
            return TelegramTestResponse(
                success=False,
                message="텔레그램 설정이 없습니다. 봇 토큰과 Chat ID를 먼저 저장해주세요."
            )
        
        # Decrypt credentials
        token = encryptor.decrypt(user_settings.telegram_token)
        chat_id = user_settings.telegram_chat_id
        
        # Create a new bot instance with user's credentials and send test message
        try:
            bot = Bot(token=token)
            await bot.send_message(
                chat_id=chat_id,
                text=request.message or "🔔 Upbit Trading Bot 테스트 메시지입니다!"
            )
            return TelegramTestResponse(
                success=True,
                message="테스트 메시지가 전송되었습니다!"
            )
        except TelegramError as e:
            return TelegramTestResponse(
                success=False,
                message=f"텔레그램 전송 실패: {str(e)}"
            )
    except Exception as e:
        logger.error(f"Error testing Telegram: {e}")
        return TelegramTestResponse(
            success=False,
            message=str(e)
        )


@router.post("/validate-upbit")
async def validate_upbit_credentials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Validate user's Upbit API credentials"""
    try:
        user_settings = db.query(UserSettings).filter(
            UserSettings.user_id == current_user.id
        ).first()
        
        if not user_settings or not user_settings.upbit_access_key or not user_settings.upbit_secret_key:
            return {
                "valid": False,
                "message": "API credentials not configured"
            }
        
        # Decrypt credentials
        access_key = encryptor.decrypt(user_settings.upbit_access_key)
        secret_key = encryptor.decrypt(user_settings.upbit_secret_key)
        
        # Validate by trying to get balance
        # TODO: Create per-user upbit client
        import pyupbit
        try:
            upbit = pyupbit.Upbit(access_key, secret_key)
            balance = upbit.get_balance("KRW")
            
            if balance is not None:
                return {
                    "valid": True,
                    "krw_balance": balance,
                    "message": "API credentials are valid"
                }
            else:
                return {
                    "valid": False,
                    "message": "Failed to validate credentials"
                }
        except Exception as e:
            return {
                "valid": False,
                "message": str(e)
            }
    except Exception as e:
        logger.error(f"Error validating Upbit credentials: {e}")
        return {
            "valid": False,
            "message": str(e)
        }

