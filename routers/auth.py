"""
Authentication Router - Register, Login, Logout, Password Change
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, validator
from typing import Optional

from models.database import get_db, User, UserSettings
from utils.auth import (
    verify_password, 
    get_password_hash, 
    create_access_token, 
    decode_token,
    TokenData
)

router = APIRouter()
security = HTTPBearer()


# ===================
# Request/Response Models
# ===================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    
    @validator('password')
    def password_min_length(cls, v):
        if len(v) < 8:
            raise ValueError('비밀번호는 최소 8자 이상이어야 합니다')
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
    
    @validator('new_password')
    def password_min_length(cls, v):
        if len(v) < 8:
            raise ValueError('비밀번호는 최소 8자 이상이어야 합니다')
        return v


class AuthResponse(BaseModel):
    success: bool
    message: str
    access_token: Optional[str] = None
    user: Optional[dict] = None


# ===================
# Dependency: Get Current User
# ===================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Extract and validate user from JWT token"""
    token = credentials.credentials
    token_data = decode_token(token)
    
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 토큰입니다",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    user = db.query(User).filter(User.id == token_data.user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="비활성화된 계정입니다"
        )
    
    # Check single session (token must match current_token)
    if user.current_token != token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="다른 기기에서 로그인되어 세션이 만료되었습니다"
        )
    
    return user


# ===================
# Auth Endpoints
# ===================

@router.post("/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user"""
    # Check if email already exists
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 등록된 이메일입니다"
        )
    
    # Create new user
    password_hash = get_password_hash(request.password)
    new_user = User(
        email=request.email,
        password_hash=password_hash
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create default settings for user with default strategy configuration
    import json as json_lib
    # ★ Phase 9: min_confidence 제거
    default_strategies = {
        "squirrel": {
            "enabled": True,
            "name": "다람쥐 전략",
            "description": "하락 추세에서 작은 몸통과 긴 위꼬리를 가진 양봉 패턴 감지",
            "timeframe": "1D"
        },
        "morning": {
            "enabled": True,
            "name": "샛별형 전략",
            "description": "하락 추세 끝에서 반등 시그널 패턴 감지",
            "timeframe": "1D/4H"
        },
        "inverted_hammer": {
            "enabled": True,
            "name": "윗꼬리 양봉 전략",
            "description": "하락 추세에서 긴 위꼬리와 작은 몸통을 가진 양봉 패턴",
            "timeframe": "1D/4H"
        }
    }
    user_settings = UserSettings(
        user_id=new_user.id,
        strategy_settings=json_lib.dumps(default_strategies)
    )
    db.add(user_settings)
    db.commit()
    
    # Generate token
    token = create_access_token(new_user.id, new_user.email)
    
    # Save token for single-session enforcement
    new_user.current_token = token
    db.commit()
    
    return AuthResponse(
        success=True,
        message="회원가입이 완료되었습니다",
        access_token=token,
        user=new_user.to_dict()
    )


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login with email and password"""
    user = db.query(User).filter(User.email == request.email).first()
    
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="비활성화된 계정입니다"
        )
    
    # Generate new token (invalidates previous sessions)
    token = create_access_token(user.id, user.email)
    
    # Save token for single-session enforcement
    user.current_token = token
    db.commit()
    
    return AuthResponse(
        success=True,
        message="로그인 성공",
        access_token=token,
        user=user.to_dict()
    )


@router.post("/logout", response_model=AuthResponse)
async def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Logout and invalidate token"""
    current_user.current_token = None
    db.commit()
    
    return AuthResponse(
        success=True,
        message="로그아웃되었습니다"
    )


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user info"""
    return {
        "success": True,
        "user": current_user.to_dict()
    }


@router.post("/change-password", response_model=AuthResponse)
async def change_password(
    request: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Change user password"""
    # Verify current password
    if not verify_password(request.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="현재 비밀번호가 올바르지 않습니다"
        )
    
    # Update password
    current_user.password_hash = get_password_hash(request.new_password)
    
    # Invalidate all sessions (force re-login)
    current_user.current_token = None
    db.commit()
    
    return AuthResponse(
        success=True,
        message="비밀번호가 변경되었습니다. 다시 로그인해주세요."
    )


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
async def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db)
):
    """
    Request password reset - sends temporary password via email
    """
    import secrets
    import string
    from services.email_service import email_service
    
    # Find user by email
    user = db.query(User).filter(User.email == request.email).first()
    
    # Always return success to prevent email enumeration attacks
    if not user:
        return {
            "success": True,
            "message": "등록된 이메일이면 비밀번호 재설정 안내가 발송됩니다."
        }
    
    # Generate temporary password
    temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    
    # Update user's password
    user.password_hash = get_password_hash(temp_password)
    user.current_token = None  # Invalidate all sessions
    db.commit()
    
    # Send email with temporary password
    email_sent = email_service.send_password_reset_email(request.email, temp_password)
    
    # Also log for backup (in case email fails)
    if not email_sent:
        print(f"\n{'='*50}")
        print(f"🔐 PASSWORD RESET for {request.email}")
        print(f"   Temporary Password: {temp_password}")
        print(f"   (Email not configured - check SMTP settings)")
        print(f"{'='*50}\n")
    
    return {
        "success": True,
        "message": "등록된 이메일이면 비밀번호 재설정 안내가 발송됩니다."
    }
