"""
Authentication module for LocalBBS multi-user support.
Handles user registration, login, and JWT token management.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from config import settings
from database import get_db, User, UserSettings

# Set up logging (use DEBUG in development via LOG_LEVEL env var)
logger = logging.getLogger(__name__)
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))

# ============================================================================
# Security Configuration
# ============================================================================

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

# Use argon2 (modern, no length limits) with bcrypt as fallback for old hashes
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


# ============================================================================
# Pydantic Models
# ============================================================================

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    display_name: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: Optional[str]
    has_api_key: bool
    is_admin: bool
    is_verified: bool
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenData(BaseModel):
    user_id: Optional[int] = None


# ============================================================================
# Password Utilities
# ============================================================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password for storing."""
    return pwd_context.hash(password)


# ============================================================================
# Token Utilities
# ============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            logger.debug("Token decode: no 'sub' in payload")
            return None
        logger.debug(f"Token decode success: user_id={user_id}")
        return TokenData(user_id=int(user_id))
    except JWTError as e:
        logger.debug(f"Token decode failed: {e}")
        return None


# ============================================================================
# User CRUD Operations
# ============================================================================

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get a user by email address."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Get a user by ID."""
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, user_data: UserCreate, make_admin: bool = False, auto_verify: bool = False) -> User:
    """Create a new user with default settings.
    
    Args:
        db: Database session
        user_data: User creation data
        make_admin: If True, make this user an admin
        auto_verify: If True, mark the user as verified immediately
    """
    # Check if this is the first user - make them admin
    is_first_user = db.query(User).count() == 0
    
    # Create user
    hashed_password = get_password_hash(user_data.password)
    db_user = User(
        email=user_data.email,
        password_hash=hashed_password,
        display_name=user_data.display_name or user_data.email.split("@")[0],
        is_admin=make_admin or is_first_user,  # First user is always admin
        is_verified=auto_verify or is_first_user,  # First user auto-verified
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    # Create default settings for the user
    user_settings = UserSettings(user_id=db_user.id)
    db.add(user_settings)
    db.commit()
    
    return db_user


def authenticate_user(db: Session, email: str, password: str, check_verified: bool = True) -> Optional[User]:
    """Authenticate a user by email and password.
    
    Args:
        db: Database session
        email: User's email
        password: Plain text password
        check_verified: If True, require user to be verified
        
    Returns:
        User if authentication successful, None otherwise
    """
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def update_user_api_key(db: Session, user: User, api_key: str) -> User:
    """Update user's OpenRouter API key."""
    user.openrouter_api_key = api_key
    db.commit()
    db.refresh(user)
    return user


# ============================================================================
# Dependencies for FastAPI Routes
# ============================================================================

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Get the current authenticated user from JWT token.
    Can be provided via:
    - Authorization header: Bearer <token>
    - Cookie: access_token=<token>
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = None
    
    # Try to get token from Authorization header
    if credentials:
        token = credentials.credentials
        logger.debug("Token from Authorization header")
    
    # Fallback to cookie
    if not token:
        token = request.cookies.get("access_token")
        if token:
            logger.debug("Token from cookie")
    
    if not token:
        logger.debug("No token found in header or cookie")
        raise credentials_exception
    
    token_data = decode_token(token)
    if token_data is None:
        logger.debug("Token decode returned None")
        raise credentials_exception
    
    user = get_user_by_id(db, token_data.user_id)
    if user is None:
        logger.debug(f"User not found for id={token_data.user_id}")
        raise credentials_exception
    
    logger.debug(f"Authenticated user: id={user.id}, email={user.email}, is_active={user.is_active}")
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )
    
    return user


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Get the current user if authenticated, otherwise return None.
    Useful for routes that work for both authenticated and anonymous users.
    """
    try:
        return await get_current_user(request, credentials, db)
    except HTTPException:
        return None


# ============================================================================
# Helper Functions
# ============================================================================

def user_to_response(user: User) -> UserResponse:
    """Convert User model to UserResponse."""
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        has_api_key=bool(user.openrouter_api_key),
        is_admin=user.is_admin,
        is_verified=user.is_verified,
        is_active=user.is_active,
        created_at=user.created_at
    )


async def get_current_admin_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Get the current authenticated user and verify they are an admin.
    Raises 403 if user is not an admin.
    """
    user = await get_current_user(request, credentials, db)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user
