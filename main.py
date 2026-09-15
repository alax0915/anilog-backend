import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict, List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import String, DateTime, Integer, JSON, func, Boolean
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.future import select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ==================================================
# 1. ENVIRONMENT CONFIGURATION
# ==================================================

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key-change-this")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is missing!")

ANIME_SOURCE_URL = os.getenv("ANIME_SOURCE_URL", "https://kitsu.io/api/edge")

raw_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://127.0.0.1:5500,http://localhost:5500",
)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in raw_origins.split(",")
    if origin.strip()
]

# ==================================================
# 2. SECURITY & AUTHENTICATION
# ==================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


DEFAULT_AVATARS = [
    "https://api.dicebear.com/7.x/bottts/svg?seed=Shadow",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Panda",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Phoenix",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Cyber",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Pixel",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Ghost",
]


# ==================================================
# 3. DATABASE SETUP
# ==================================================

engine = create_async_engine(DATABASE_URL, echo=True)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="client", nullable=False)
    avatar_seed: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(String(255), default="I love anime", nullable=True)
    streak_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    watching_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    favorites_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    xp_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    daily_watch_activities: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    notifications: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reaction_logs: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    # Enhanced security & token management fields
    hashed_refresh_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    login_audit_logs: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSON, default=list, nullable=True)

    user_anime_lists: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    user_settings: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        default=lambda: {"app_theme": "dark", "content_filter": True},
    )


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ==================================================
# 4. PYDANTIC SCHEMAS
# ==================================================

class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    avatar_seed: Optional[str]
    bio: Optional[str]
    streak_count: int
    watching_count: int
    completed_count: int
    favorites_count: int
    last_active_at: datetime
    xp_points: int
    level: int
    created_at: datetime
    updated_at: datetime
    daily_watch_activities: Optional[Dict[str, Any]] = None
    notifications: int
    reaction_logs: Optional[Dict[str, Any]] = None
    user_anime_lists: Optional[Dict[str, Any]] = None
    user_settings: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user: UserResponse


# ==================================================
# 5. FASTAPI APP INITIALIZATION
# ==================================================

app = FastAPI(title="AniLog API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,
)


# ==================================================
# 6. AUTH DEPENDENCIES
# ==================================================

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")

        if user_id is None or token_type != "access":
            raise credentials_exception
    except (JWTError, ValueError):
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalars().first()

    if user is None:
        raise credentials_exception

    return user


# ==================================================
# 7. STARTUP
# ==================================================

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ==================================================
# 8. ROUTES
# ==================================================

@app.get("/")
def home():
    return {"status": "AniLog Backend is live!"}


@app.options("/{full_path:path}")
async def preflight_handler():
    return {"message": "CORS preflight OK"}


@app.post(
    "/api/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    user_data: UserRegister,
    db: AsyncSession = Depends(get_db),
):
    existing_user = await db.execute(
        select(User).where(User.username == user_data.username)
    )
    if existing_user.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="Username already registered",
        )

    existing_email = await db.execute(
        select(User).where(User.email == user_data.email)
    )
    if existing_email.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="Email already registered",
        )

    user_count = await db.execute(select(func.count(User.id)))
    total_users = user_count.scalar() or 0

    assigned_role = "admin" if total_users == 0 else "client"
    now = datetime.now(timezone.utc)

    new_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        role=assigned_role,
        avatar_seed=random.choice(DEFAULT_AVATARS),
        bio="I love anime",
        streak_count=1,
        watching_count=0,
        completed_count=0,
        favorites_count=0,
        last_active_at=now,
        xp_points=0,
        level=1,
        created_at=now,
        updated_at=now,
        login_audit_logs=[],
        user_settings={"app_theme": "dark", "content_filter": True},
    )

    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return new_user


@app.post(
    "/api/auth/login",
    response_model=Token,
)
async def login(
    credentials: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.email == credentials.email)
    )
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )

    now = datetime.now(timezone.utc)

    # Check for Account Lockout
    if user.locked_until and user.locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account is temporarily locked due to failed attempts. Try again later.",
        )

    # Verify Password
    if not verify_password(credentials.password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
        
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )

    # Successful Password Validation Reset
    user.failed_login_attempts = 0
    user.locked_until = None

    # Handle Daily Streak Update
    if user.last_active_at:
        days_diff = (now.date() - user.last_active_at.date()).days
        if days_diff == 1:
            user.streak_count += 1
        elif days_diff > 1:
            user.streak_count = 1

    user.last_active_at = now
    user.updated_at = now

    # Audit Logging
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    audit_entry = {
        "timestamp": now.isoformat(),
        "ip_address": client_ip,
        "user_agent": user_agent,
    }
    
    logs = list(user.login_audit_logs or [])
    logs.append(audit_entry)
    user.login_audit_logs = logs[-10:]  # Keep last 10 logins

    # Generate Tokens
    access_token = create_access_token(data={"sub": str(user.id)})
    refresh_token = create_refresh_token(data={"sub": str(user.id)})

    # Hash and Store Refresh Token
    user.hashed_refresh_token = hash_password(refresh_token)

    await db.commit()
    await db.refresh(user)

    # Set Secure HttpOnly Cookie
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        secure=True,
        samesite="lax",
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
    }


@app.post("/api/auth/refresh")
async def refresh_token_endpoint(
    body: Optional[RefreshTokenRequest] = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    token = (body.refresh_token if body else None) or request.cookies.get("refresh_token")
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing",
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")

        if user_id is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalars().first()

    if not user or not user.hashed_refresh_token:
        raise HTTPException(status_code=401, detail="Token revoked or user not found")

    if not verify_password(token, user.hashed_refresh_token):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    new_access_token = create_access_token(data={"sub": str(user.id)})
    new_refresh_token = create_refresh_token(data={"sub": str(user.id)})

    user.hashed_refresh_token = hash_password(new_refresh_token)
    await db.commit()

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@app.get(
    "/api/auth/me",
    response_model=UserResponse,
)
async def read_current_user(
    current_user: User = Depends(get_current_user),
):
    return current_user


@app.get("/api/anime/source-url")
async def get_anime_source_url(
    current_user: User = Depends(get_current_user),
):
    return {"source_url": ANIME_SOURCE_URL}


@app.get("/api/anime/search")
async def search_anime(
    q: str = Query(..., min_length=1),
):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.jikan.moe/v4/anime?q={q}&limit=5"
        )

    if response.status_code != 200:
        return {"error": "Failed to fetch anime data"}

    data = response.json().get("data", [])

    results = [
        {
            "id": anime["mal_id"],
            "title": anime["title"],
            "episodes": anime["episodes"],
            "score": anime["score"],
            "image": anime["images"]["jpg"]["image_url"],
        }
        for anime in data
    ]
 
    return {"results": results}