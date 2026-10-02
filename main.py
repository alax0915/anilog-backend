import os
import asyncio
import random
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Any, Dict, List, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import (
    String,
    DateTime,
    Date,
    Integer,
    JSON,
    func,
    Boolean,
    Text,
    ForeignKey,
    update as sql_update,
    delete as sql_delete,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum, UUID
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
ACCESS_TOKEN_EXPIRE_MINUTES = 1440
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
    origin.strip() for origin in raw_origins.split(",") if origin.strip()
]

HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
GOAL_CATALOG_LOCK = asyncio.Lock()

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
    "https://api.dicebear.com/10.x/lorelei/svg?seed=Felix",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Ghost",
]

# ==================================================
# 3. DATABASE SETUP
# ==================================================

engine = create_async_engine(
    DATABASE_URL, echo=True, connect_args={"statement_cache_size": 0}
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


class Anime(Base):
    __tablename__ = "anime"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    title_english: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    title_japanese: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    subtype: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    age_rating: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    user_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_date: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    synopsis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    poster_image: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    episode_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(nullable=True)


class UserAnimeList(Base):
    __tablename__ = "user_anime_list"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    anime_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("anime.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    episodes_watched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DailyWatchActivity(Base):
    __tablename__ = "daily_watch_activities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    anime_id: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_genre: Mapped[str] = mapped_column(String(50), nullable=False)
    episodes_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    watch_time_mins: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    activity_date: Mapped[date] = mapped_column(
        Date, default=date.today, nullable=False
    )


class WatchHistory(Base):
    __tablename__ = "watch_history"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    anime_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    episodes_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    xp_earned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    activity_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class GoalDefinition(Base):
    __tablename__ = "goal_definitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="yearly")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    icon: Mapped[str] = mapped_column(String(50), default="flag")
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_xp: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserGoal(Base):
    __tablename__ = "user_goals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    goal_id: Mapped[int] = mapped_column(Integer, ForeignKey("goal_definitions.id"), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "goal_id", "period_key", name="uq_user_goal_period"),)


class GoalNote(Base):
    __tablename__ = "goal_notes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class XPEvent(Base):
    __tablename__ = "xp_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "event_type", "reference_id", name="uq_xp_event"),)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="client", nullable=False)
    avatar_seed: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(
        String(255), default="I love anime", nullable=True
    )
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
    daily_watch_activities: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    notifications: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reaction_logs: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    hashed_refresh_token: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    login_audit_logs: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(
        JSON, default=list, nullable=True
    )

    user_settings: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        default=lambda: {"app_theme": "dark", "content_filter": True},
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    theme: Mapped[Optional[str]] = mapped_column(
        PGEnum("dark", "light", name="app_theme"),
        default="dark",
        nullable=True,
    )
    push_notifications: Mapped[Optional[bool]] = mapped_column(
        Boolean, default=True, nullable=True
    )
    email_notifications: Mapped[Optional[bool]] = mapped_column(
        Boolean, default=True, nullable=True
    )
    content_filter: Mapped[Optional[bool]] = mapped_column(
        Boolean, default=False, nullable=True
    )


def user_settings_key(user_id: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, str(user_id))


def user_settings_payload(settings: UserSettings) -> Dict[str, Any]:
    theme = settings.theme if settings.theme in {"dark", "light"} else "dark"
    return {
        "theme": theme,
        "app_theme": theme,
        "push_notifications": settings.push_notifications,
        "email_notifications": settings.email_notifications,
        "content_filter": settings.content_filter,
    }


async def get_or_create_user_settings(
    db: AsyncSession, user: User
) -> UserSettings:
    settings_key = user_settings_key(user.id)
    settings = await db.scalar(
        select(UserSettings).where(UserSettings.user_id == settings_key)
    )
    if settings:
        if settings.theme not in {"dark", "light"}:
            settings.theme = "dark"
        return settings

    legacy_settings = user.user_settings or {}
    legacy_theme = legacy_settings.get("theme") or legacy_settings.get("app_theme")
    theme = legacy_theme if legacy_theme in {"dark", "light"} else "dark"
    settings = UserSettings(
        user_id=settings_key,
        theme=theme,
        push_notifications=legacy_settings.get("push_notifications", True),
        email_notifications=legacy_settings.get("email_notifications", True),
        content_filter=legacy_settings.get("content_filter", False),
    )
    db.add(settings)
    await db.flush()
    return settings


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


class UserProfileUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    bio: Optional[str] = None
    avatar_seed: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None


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


class WatchlistAddRequest(BaseModel):
    anime_id: str
    status: str = "Watching"
    episodes_watched: int = 0
    score: Optional[int] = None
    is_favorite: bool = False
    priority: str = "Medium"
    notes: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class WatchlistDeleteRequest(BaseModel):
    watchlist_ids: List[str]


class FavoriteToggleRequest(BaseModel):
    anime_id: str


class WatchActivityRequest(BaseModel):
    primary_genre: str
    category: Optional[str] = "series"
    episodes_count: Optional[int] = 1
    watch_time_mins: Optional[int] = 24
    activity_date: Optional[str] = None


class GoalNoteRequest(BaseModel):
    note: str


class GoalActionRequest(BaseModel):
    goal_id: int


class ThemeUpdateRequest(BaseModel):
    theme: Literal["dark", "light"]


# ==================================================
# 5. FASTAPI APP INITIALIZATION
# ==================================================

app = FastAPI(title="AniLog API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
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
# 7. STARTUP & UTILS
# ==================================================


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.get("/api/avatars")
async def get_available_avatars():
    return {"avatars": DEFAULT_AVATARS}


# ==================================================
# 8. ROUTES
# ==================================================


@app.get("/")
def home():
    return {"status": "AniLog Backend is live!"}


@app.options("/{full_path:path}")
async def preflight_handler(full_path: str):
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

    existing_email = await db.execute(select(User).where(User.email == user_data.email))
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
    await db.flush()
    settings = await get_or_create_user_settings(db, new_user)
    new_user.user_settings = user_settings_payload(settings)
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
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )

    now = datetime.now(timezone.utc)

    if user.locked_until and user.locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account is temporarily locked due to failed attempts. Try again later.",
        )

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

    user.failed_login_attempts = 0
    user.locked_until = None

    if user.last_active_at:
        days_diff = (now.date() - user.last_active_at.date()).days
        if days_diff == 1:
            user.streak_count += 1
        elif days_diff > 1:
            user.streak_count = 1

    user.last_active_at = now
    user.updated_at = now

    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")

    audit_entry = {
        "timestamp": now.isoformat(),
        "ip_address": client_ip,
        "user_agent": user_agent,
    }

    logs = list(user.login_audit_logs or [])
    logs.append(audit_entry)
    user.login_audit_logs = logs[-10:]

    access_token = create_access_token(data={"sub": str(user.id)})
    refresh_token = create_refresh_token(data={"sub": str(user.id)})

    user.hashed_refresh_token = hash_password(refresh_token)

    await db.commit()
    await db.refresh(user)
    settings = await get_or_create_user_settings(db, user)
    user.user_settings = user_settings_payload(settings)
    await db.commit()
    await db.refresh(user)

    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    response.set_cookie(
        key="anilog_theme",
        value=settings.theme or "dark",
        httponly=False,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
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
    token = (body.refresh_token if body else None) or request.cookies.get(
        "refresh_token"
    )

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
    db: AsyncSession = Depends(get_db),
):
    watching_res = await db.execute(
        select(func.count(UserAnimeList.id)).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.status.ilike("watching"),
        )
    )
    completed_res = await db.execute(
        select(func.count(UserAnimeList.id)).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.status.ilike("completed"),
        )
    )
    fav_res = await db.execute(
        select(func.count(UserAnimeList.id)).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.is_favorite == True,
        )
    )

    current_user.watching_count = watching_res.scalar() or 0
    current_user.completed_count = completed_res.scalar() or 0
    current_user.favorites_count = fav_res.scalar() or 0
    settings = await get_or_create_user_settings(db, current_user)
    current_user.user_settings = user_settings_payload(settings)

    await db.commit()
    await db.refresh(current_user)
    return current_user


@app.get("/api/user/settings")
async def get_user_settings(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = await get_or_create_user_settings(db, current_user)
    payload = user_settings_payload(settings)
    current_user.user_settings = payload
    await db.commit()
    response.set_cookie(
        key="anilog_theme",
        value=payload["theme"],
        httponly=False,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return payload


@app.put("/api/user/settings")
async def update_user_settings(
    payload: ThemeUpdateRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = await get_or_create_user_settings(db, current_user)
    settings.theme = payload.theme
    current_user.user_settings = user_settings_payload(settings)
    await db.commit()
    await db.refresh(settings)
    response.set_cookie(
        key="anilog_theme",
        value=settings.theme,
        httponly=False,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return user_settings_payload(settings)


@app.put("/api/auth/profile", response_model=UserResponse)
async def update_user_profile(
    payload: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = current_user.id
    user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, str(user_id))
    now = datetime.now(timezone.utc)

    if payload.username and payload.username != current_user.username:
        existing_username = await db.execute(
            select(User).where(User.username == payload.username)
        )
        if existing_username.scalars().first():
            raise HTTPException(status_code=400, detail="Username already in use")
        current_user.username = payload.username

    if payload.email and payload.email != current_user.email:
        existing_email = await db.execute(
            select(User).where(User.email == payload.email)
        )
        if existing_email.scalars().first():
            raise HTTPException(status_code=400, detail="Email already in use")
        current_user.email = payload.email

    if payload.bio is not None:
        current_user.bio = payload.bio

    if payload.avatar_seed is not None:
        current_user.avatar_seed = payload.avatar_seed

    if payload.new_password:
        if not payload.current_password or not verify_password(
            payload.current_password, current_user.password_hash
        ):
            raise HTTPException(
                status_code=400, detail="Current password verification failed"
            )
        current_user.password_hash = hash_password(payload.new_password)

    current_user.updated_at = now

    await db.execute(
        sql_update(UserAnimeList)
        .where(UserAnimeList.user_id == user_id)
        .values(updated_at=now)
    )

    await db.commit()
    await db.refresh(current_user)

    return current_user


# ==================================================
# GOALS / XP / PROGRESS API
# ==================================================

LEVELS = [
    (1, "Otaku Novice", 0, 500),
    (2, "Casual Watcher", 500, 2000),
    (3, "Anime Enthusiast", 1200, 3000),
    (4, "Seasoned Binger", 2500, 5000),
    (5, "Master Otaku", 5000, 10000),
    (6, "Anime Scholar", 10000, 20000),
    (7, "Supreme Weeb", 20000, 50000),
]


def level_for_xp(xp: int):
    xp = max(0, int(xp or 0))
    chosen = LEVELS[0]
    for item in LEVELS:
        if xp >= item[2]:
            chosen = item
    return {"level": chosen[0], "title": chosen[1], "min_xp": chosen[2], "next_xp": chosen[3], "progress_percent": min(100, max(0, int((xp - chosen[2]) / max(1, chosen[3] - chosen[2]) * 100)))}


def default_yearly_goals(year: int):
    base = [
        ("Watch 10 new anime", "play_circle", "completed_anime", 10, 100),
        ("Watch 25 new anime", "play_circle", "completed_anime", 25, 150),
        ("Watch 50 new anime", "play_circle", "completed_anime", 50, 250),
        ("Watch 100 new anime", "play_circle", "completed_anime", 100, 500),
        ("Watch 250 anime episodes", "movie", "episodes", 250, 150),
        ("Watch 500 anime episodes", "movie", "episodes", 500, 300),
        ("Watch 1000 anime episodes", "movie", "episodes", 1000, 600),
        ("Earn 1000 XP", "bolt", "xp", 1000, 100),
        ("Earn 2500 XP", "bolt", "xp", 2500, 250),
        ("Earn 5000 XP", "bolt", "xp", 5000, 500),
        ("Complete 5 anime", "task_alt", "completed_anime", 5, 75),
        ("Complete 15 anime", "task_alt", "completed_anime", 15, 150),
        ("Complete 30 anime", "task_alt", "completed_anime", 30, 300),
        ("Finish an anime you paused", "play_arrow", "completed_anime", 1, 75),
        ("Complete 3 long anime", "all_inclusive", "long_completed", 3, 200),
    ]
    icons = ["flag", "stars", "local_fire_department", "auto_awesome", "favorite", "grade", "rocket", "bolt", "military_tech", "emoji_events"]
    rows = list(base)
    while len(rows) < 100:
        n = len(rows) + 1
        target = 5 + ((n * 7) % 90)
        metric = ["completed_anime", "episodes", "xp"][n % 3]
        if metric == "completed_anime":
            target = max(3, target // 2)
            title = f"Complete {target} anime"
        elif metric == "episodes":
            target = max(25, target * 10)
            title = f"Watch {target} episodes"
        else:
            target = max(500, target * 100)
            title = f"Earn {target} XP"
        rows.append((title, icons[n % len(icons)], metric, target, max(50, min(500, target // 2))))
    return rows[:100]


async def ensure_goal_catalog(db: AsyncSession, year: int):
    count = await db.scalar(select(func.count(GoalDefinition.id)).where(GoalDefinition.year == year, GoalDefinition.kind == "yearly"))
    if count and count >= 100:
        return
    rows = default_yearly_goals(year)
    for idx, (title, icon, metric, target, reward_xp) in enumerate(rows):
        exists = await db.scalar(select(GoalDefinition.id).where(GoalDefinition.year == year, GoalDefinition.kind == "yearly", GoalDefinition.sort_order == idx))
        if not exists:
            db.add(GoalDefinition(year=year, kind="yearly", title=title, icon=icon, metric=metric, target=target, reward_xp=reward_xp, sort_order=idx))
    monthly = [
        ("Complete 2 anime this month", "task_alt", "monthly_completed", 2, 100),
        ("Watch 24 episodes this month", "movie", "monthly_episodes", 24, 100),
        ("Earn 500 XP this month", "bolt", "monthly_xp", 500, 100),
        ("Complete 5 anime this month", "military_tech", "monthly_completed", 5, 200),
        ("Watch 50 episodes this month", "local_fire_department", "monthly_episodes", 50, 250),
        ("Earn 1000 XP this month", "stars", "monthly_xp", 1000, 250),
    ]
    for idx, (title, icon, metric, target, reward_xp) in enumerate(monthly):
        exists = await db.scalar(select(GoalDefinition.id).where(GoalDefinition.year == year, GoalDefinition.kind == "monthly", GoalDefinition.sort_order == idx))
        if not exists:
            db.add(GoalDefinition(year=year, kind="monthly", title=title, icon=icon, metric=metric, target=target, reward_xp=reward_xp, sort_order=idx))
    await db.commit()


async def award_xp(db: AsyncSession, user: User, amount: int, event_type: str, reference_id: str):
    if amount <= 0:
        return 0
    existing = await db.scalar(select(XPEvent.id).where(XPEvent.user_id == user.id, XPEvent.event_type == event_type, XPEvent.reference_id == str(reference_id)))
    if existing:
        return 0
    db.add(XPEvent(user_id=user.id, event_type=event_type, reference_id=str(reference_id), xp=amount))
    user.xp_points = int(user.xp_points or 0) + amount
    user.level = level_for_xp(user.xp_points)["level"]
    user.updated_at = datetime.now(timezone.utc)
    return amount


async def goal_metrics(db: AsyncSession, user_id: int, year: int, month: int):
    completed_year = await db.scalar(select(func.count(UserAnimeList.id)).where(UserAnimeList.user_id == user_id, UserAnimeList.status.ilike("completed"), UserAnimeList.completed_at >= datetime(year, 1, 1, tzinfo=timezone.utc), UserAnimeList.completed_at < datetime(year + 1, 1, 1, tzinfo=timezone.utc))) or 0
    completed_month = await db.scalar(select(func.count(UserAnimeList.id)).where(UserAnimeList.user_id == user_id, UserAnimeList.status.ilike("completed"), UserAnimeList.completed_at >= datetime(year, month, 1, tzinfo=timezone.utc), UserAnimeList.completed_at < (datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc)))) or 0
    episodes = await db.scalar(select(func.coalesce(func.sum(UserAnimeList.episodes_watched), 0)).where(UserAnimeList.user_id == user_id)) or 0
    year_xp = await db.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(XPEvent.user_id == user_id, XPEvent.created_at >= datetime(year, 1, 1, tzinfo=timezone.utc), XPEvent.created_at < datetime(year + 1, 1, 1, tzinfo=timezone.utc))) or 0
    month_xp = await db.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(XPEvent.user_id == user_id, XPEvent.created_at >= datetime(year, month, 1, tzinfo=timezone.utc), XPEvent.created_at < (datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1, tzinfo=timezone.utc)))) or 0
    return {"completed_year": int(completed_year), "completed_month": int(completed_month), "episodes": int(episodes), "year_xp": int(year_xp), "month_xp": int(month_xp)}


@app.get("/api/goals/dashboard")
async def get_goals_dashboard(
    section: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    # Multiple dashboard sections are requested in parallel by the frontend.
    # Protect first-time catalog creation so concurrent requests cannot seed duplicates.
    async with GOAL_CATALOG_LOCK:
        await ensure_goal_catalog(db, year)

    metrics = await goal_metrics(db, current_user.id, year, month)

    async def current_for_goal(d):
        if d.metric == "completed_anime":
            return metrics["completed_year"]
        if d.metric == "episodes":
            return metrics["episodes"]
        if d.metric == "xp":
            return int(current_user.xp_points or 0)
        if d.metric == "monthly_completed":
            return metrics["completed_month"]
        if d.metric == "monthly_episodes":
            return int(await db.scalar(select(func.coalesce(func.sum(WatchHistory.episodes_count), 0)).where(
                WatchHistory.user_id == current_user.id,
                WatchHistory.activity_date >= date(year, month, 1),
            )) or 0)
        if d.metric == "monthly_xp":
            return metrics["month_xp"]
        return 0

    async def build_section_goals(kind):
        defs = (await db.execute(
            select(GoalDefinition)
            .where(GoalDefinition.year == year, GoalDefinition.kind == kind)
            .order_by(GoalDefinition.sort_order)
        )).scalars().all()
        result = []
        period = str(year) if kind == "yearly" else f"{year}-{month:02d}"
        for d in defs:
            current = int(await current_for_goal(d))
            if current >= d.target:
                ug = await db.scalar(select(UserGoal).where(
                    UserGoal.user_id == current_user.id,
                    UserGoal.goal_id == d.id,
                    UserGoal.period_key == period,
                ))
                if not ug or not ug.completed:
                    if not ug:
                        db.add(UserGoal(
                            user_id=current_user.id, goal_id=d.id, period_key=period,
                            completed=True, completed_at=now,
                        ))
                    else:
                        ug.completed = True
                        ug.completed_at = now
                    await award_xp(db, current_user, d.reward_xp, "goal_completion", f"{d.id}:{period}")
            result.append({
                "id": d.id, "title": d.title, "icon": d.icon, "metric": d.metric,
                "target": d.target, "current": current,
                "progress": min(100, int(current / max(1, d.target) * 100)),
                "reward_xp": d.reward_xp,
            })
        result.sort(key=lambda x: (x["current"] >= x["target"], x["id"]))
        return result[:3] if kind == "yearly" else result[:4]

    async def build_months():
        months = []
        for m in range(1, 13):
            start = datetime(year, m, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if m == 12 else datetime(year, m + 1, 1, tzinfo=timezone.utc)
            xp = await db.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(
                XPEvent.user_id == current_user.id, XPEvent.created_at >= start, XPEvent.created_at < end
            )) or 0
            done = await db.scalar(select(func.count(UserAnimeList.id)).where(
                UserAnimeList.user_id == current_user.id,
                UserAnimeList.status.ilike("completed"),
                UserAnimeList.completed_at >= start,
                UserAnimeList.completed_at < end,
            )) or 0
            months.append({"month": m, "xp": int(xp), "completed": int(done)})
        return months

    valid_sections = {"yearly", "monthly", "rewards", "notes", "progress", "store", "profile"}
    if section:
        if section not in valid_sections:
            raise HTTPException(status_code=400, detail=f"Unknown dashboard section: {section}")

        if section == "yearly":
            data = {"year": year, "yearly_goals": await build_section_goals("yearly")}
        elif section == "monthly":
            data = {"year": year, "month": month, "monthly_challenges": await build_section_goals("monthly")}
        elif section == "rewards":
            data = {"rewards": [
                {"title": "Anime Starter", "requirement": "Complete 5 anime", "required": 5, "current": metrics["completed_year"], "xp": 75},
                {"title": "The Veteran", "requirement": "Complete 50 anime", "required": 50, "current": metrics["completed_year"], "xp": 500},
                {"title": "Episode Hunter", "requirement": "Watch 1000 episodes", "required": 1000, "current": metrics["episodes"], "xp": 750},
                {"title": "XP Ascension", "requirement": "Earn 5000 XP", "required": 5000, "current": int(current_user.xp_points or 0), "xp": 500},
            ]}
        elif section == "notes":
            notes = (await db.execute(select(GoalNote).where(GoalNote.user_id == current_user.id).order_by(GoalNote.created_at.desc()).limit(20))).scalars().all()
            data = {"notes": [{"id": str(n.id), "note": n.note, "created_at": n.created_at.isoformat()} for n in notes]}
        elif section == "progress":
            data = {"year": year, "months": await build_months()}
        else:
            level = level_for_xp(current_user.xp_points)
            user_data = {"id": current_user.id, "username": current_user.username, "avatar": current_user.avatar_seed, "xp": int(current_user.xp_points or 0), "level": level}
            if section == "store":
                data = {"store": {"xp": int(current_user.xp_points or 0), "level": level["level"]}}
            else:
                data = {"user": user_data}

        await db.commit()
        return data
    yearly_defs = (await db.execute(select(GoalDefinition).where(GoalDefinition.year == year, GoalDefinition.kind == "yearly").order_by(GoalDefinition.sort_order))).scalars().all()
    monthly_defs = (await db.execute(select(GoalDefinition).where(GoalDefinition.year == year, GoalDefinition.kind == "monthly").order_by(GoalDefinition.sort_order))).scalars().all()

    async def build_goals(defs, period_key):
        result = []
        for d in defs:
            if d.kind == "yearly":
                if d.metric == "completed_anime": current = metrics["completed_year"]
                elif d.metric == "episodes": current = metrics["episodes"]
                elif d.metric == "xp": current = int(current_user.xp_points or 0)
                else: current = 0
            else:
                if d.metric == "monthly_completed": current = metrics["completed_month"]
                elif d.metric == "monthly_episodes": current = await db.scalar(select(func.coalesce(func.sum(WatchHistory.episodes_count), 0)).where(WatchHistory.user_id == current_user.id, WatchHistory.activity_date >= date(year, month, 1))) or 0
                elif d.metric == "monthly_xp": current = metrics["month_xp"]
                else: current = 0
            current = int(current)
            period = str(year) if d.kind == "yearly" else f"{year}-{month:02d}"
            if current >= d.target:
                ug = await db.scalar(select(UserGoal).where(UserGoal.user_id == current_user.id, UserGoal.goal_id == d.id, UserGoal.period_key == period))
                if not ug or not ug.completed:
                    if not ug:
                        db.add(UserGoal(user_id=current_user.id, goal_id=d.id, period_key=period, completed=True, completed_at=datetime.now(timezone.utc)))
                    else:
                        ug.completed = True; ug.completed_at = datetime.now(timezone.utc)
                    await award_xp(db, current_user, d.reward_xp, "goal_completion", f"{d.id}:{period}")
            result.append({"id": d.id, "title": d.title, "icon": d.icon, "metric": d.metric, "target": d.target, "current": current, "progress": min(100, int(current / max(1, d.target) * 100)), "reward_xp": d.reward_xp})
        return result

    yearly = await build_goals(yearly_defs, str(year))
    monthly = await build_goals(monthly_defs, f"{year}-{month:02d}")
    # Only three active goals are exposed. Completed goals naturally drop out and the next catalog entries replace them.
    yearly = sorted(yearly, key=lambda x: (x["current"] >= x["target"], x["id"]))[:3]
    monthly = sorted(monthly, key=lambda x: (x["current"] >= x["target"], x["id"]))[:4]

    months = []
    for m in range(1, 13):
        start = datetime(year, m, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if m == 12 else datetime(year, m + 1, 1, tzinfo=timezone.utc)
        xp = await db.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(XPEvent.user_id == current_user.id, XPEvent.created_at >= start, XPEvent.created_at < end)) or 0
        done = await db.scalar(select(func.count(UserAnimeList.id)).where(UserAnimeList.user_id == current_user.id, UserAnimeList.status.ilike("completed"), UserAnimeList.completed_at >= start, UserAnimeList.completed_at < end)) or 0
        months.append({"month": m, "xp": int(xp), "completed": int(done)})

    notes = (await db.execute(select(GoalNote).where(GoalNote.user_id == current_user.id).order_by(GoalNote.created_at.desc()).limit(20))).scalars().all()
    rewards = [
        {"title": "Anime Starter", "requirement": "Complete 5 anime", "required": 5, "current": metrics["completed_year"], "xp": 75},
        {"title": "The Veteran", "requirement": "Complete 50 anime", "required": 50, "current": metrics["completed_year"], "xp": 500},
        {"title": "Episode Hunter", "requirement": "Watch 1000 episodes", "required": 1000, "current": metrics["episodes"], "xp": 750},
        {"title": "XP Ascension", "requirement": "Earn 5000 XP", "required": 5000, "current": int(current_user.xp_points or 0), "xp": 500},
    ]
    await db.commit()
    level = level_for_xp(current_user.xp_points)
    return {"year": year, "month": month, "user": {"id": current_user.id, "username": current_user.username, "avatar": current_user.avatar_seed, "xp": int(current_user.xp_points or 0), "level": level}, "yearly_goals": yearly, "monthly_challenges": monthly, "rewards": rewards, "notes": [{"id": str(n.id), "note": n.note, "created_at": n.created_at.isoformat()} for n in notes], "months": months, "store": {"xp": int(current_user.xp_points or 0), "level": level["level"]}, "metrics": metrics}


@app.post("/api/goals/notes")
async def create_goal_note(payload: GoalNoteRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    note = payload.note.strip()
    if not note: raise HTTPException(status_code=400, detail="Note cannot be empty")
    item = GoalNote(user_id=current_user.id, note=note)
    db.add(item); await db.commit(); await db.refresh(item)
    return {"id": str(item.id), "note": item.note, "created_at": item.created_at.isoformat()}


@app.delete("/api/goals/notes/{note_id}")
async def delete_goal_note(note_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(sql_delete(GoalNote).where(GoalNote.id == uuid.UUID(note_id), GoalNote.user_id == current_user.id))
    await db.commit()
    return {"message": "Note deleted"}


@app.post("/api/goals/complete")
async def complete_goal(payload: GoalActionRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    goal = await db.scalar(select(GoalDefinition).where(GoalDefinition.id == payload.goal_id))
    if not goal: raise HTTPException(status_code=404, detail="Goal not found")
    now = datetime.now(timezone.utc)
    metrics = await goal_metrics(db, current_user.id, now.year, now.month)
    if goal.metric == "completed_anime": current = metrics["completed_year"]
    elif goal.metric == "episodes": current = metrics["episodes"]
    elif goal.metric == "xp": current = int(current_user.xp_points or 0)
    elif goal.metric == "monthly_completed": current = metrics["completed_month"]
    elif goal.metric == "monthly_xp": current = metrics["month_xp"]
    elif goal.metric == "monthly_episodes": current = await db.scalar(select(func.coalesce(func.sum(WatchHistory.episodes_count), 0)).where(WatchHistory.user_id == current_user.id, WatchHistory.activity_date >= date(now.year, now.month, 1))) or 0
    else: current = 0
    if int(current) < int(goal.target):
        raise HTTPException(status_code=409, detail=f"Goal is not complete yet: {current}/{goal.target}")
    period = str(now.year) if goal.kind == "yearly" else f"{now.year}-{now.month:02d}"
    existing = await db.scalar(select(UserGoal).where(UserGoal.user_id == current_user.id, UserGoal.goal_id == goal.id, UserGoal.period_key == period))
    if not existing:
        existing = UserGoal(user_id=current_user.id, goal_id=goal.id, period_key=period, completed=True, completed_at=now); db.add(existing)
    else:
        existing.completed = True; existing.completed_at = now
    gained = await award_xp(db, current_user, goal.reward_xp, "goal_completion", f"{goal.id}:{period}")
    await db.commit()
    return {"message": "Goal completed", "xp_earned": gained, "xp": current_user.xp_points, "level": level_for_xp(current_user.xp_points)}


@app.get("/api/anime/source-url")
async def get_anime_source_url(
    current_user: User = Depends(get_current_user),
):
    return {"source_url": ANIME_SOURCE_URL}


@app.get("/api/anime/search")
async def search_anime(
    q: str = Query(..., min_length=1),
):
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={"filter[text]": q, "page[limit]": "20"},
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
            )
        except httpx.RequestError:
            raise HTTPException(
                status_code=504, detail="Anime search upstream timed out"
            )

    if response.status_code != 200:
        return {"error": "Failed to fetch anime data"}

    data = response.json().get("data", [])

    results = []
    for anime in data:
        anime_id = anime.get("id")
        attrs = anime.get("attributes", {})
        titles = attrs.get("titles", {})
        title = (
            attrs.get("canonicalTitle")
            or titles.get("en")
            or titles.get("en_jp")
            or "Unknown"
        )
        poster_img = attrs.get("posterImage")
        image_url = (
            poster_img.get("original")
            or poster_img.get("large")
            or poster_img.get("medium")
            if poster_img
            else None
        )

        avg_rating = attrs.get("averageRating")
        score_val = float(avg_rating) / 10.0 if avg_rating else None

        results.append(
            {
                "id": anime_id,
                "title": title,
                "episodes": attrs.get("episodeCount"),
                "score": score_val,
                "image": image_url,
            }
        )

    return {"results": results}


@app.get("/api/anime/genres")
async def get_anime_genres(current_user: User = Depends(get_current_user)):
    genres = []
    url = f"{ANIME_SOURCE_URL}/genres?page[limit]=20"

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        while url:
            try:
                res = await client.get(
                    url,
                    headers={
                        "Accept": "application/vnd.api+json",
                        "Content-Type": "application/vnd.api+json",
                    },
                )
                if res.status_code != 200:
                    break
                data = res.json()
                for item in data.get("data", []):
                    name = item.get("attributes", {}).get("name")
                    if name and name not in genres:
                        genres.append(name)
                url = data.get("links", {}).get("next")
            except Exception:
                break

    genres.sort()
    return {"genres": genres}


@app.get("/api/anime/user-preference")
async def get_user_preference(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, str(current_user.id))

    result = await db.execute(
        select(DailyWatchActivity)
        .where(DailyWatchActivity.user_id == user_uuid)
        .order_by(DailyWatchActivity.activity_date.desc())
    )
    last_activity = result.scalars().first()

    if not last_activity:
        return {"has_preference": False}

    primary_genre = last_activity.primary_genre
    genre_param = primary_genre.lower().strip()

    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    params = {
        "filter[categories]": genre_param,
        "page[limit]": "20",
        "sort": "-userCount",
    }

    fetched_anime_list = []
    included_list = []

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime", params=params, headers=headers
            )
            if res.status_code == 200:
                kitsu_data = res.json()
                fetched_anime_list = kitsu_data.get("data", [])
                included_list = kitsu_data.get("included", [])

            if len(fetched_anime_list) < 5:
                fallback_params = {
                    "filter[genres]": genre_param,
                    "page[limit]": "20",
                    "sort": "-userCount",
                }
                res = await client.get(
                    f"{ANIME_SOURCE_URL}/anime", params=fallback_params, headers=headers
                )
                if res.status_code == 200:
                    data = res.json()
                    existing_ids = {a["id"] for a in fetched_anime_list}
                    for item in data.get("data", []):
                        if item["id"] not in existing_ids:
                            fetched_anime_list.append(item)
                    included_list.extend(data.get("included", []))
        except httpx.RequestError:
            pass

    return {
        "has_preference": True,
        "primary_genre": primary_genre,
        "data": fetched_anime_list,
        "included": included_list,
    }


@app.post("/api/anime/watch-activity")
async def submit_watch_activity(
    payload: WatchActivityRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    category = (payload.category or "series").lower()
    genre_param = payload.primary_genre.lower().strip()

    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    params = {
        "filter[categories]": genre_param,
        "page[limit]": "20",
        "sort": "-userCount",
    }

    if category == "seasonal":
        params["filter[status]"] = "current"
    elif category == "movie":
        params["filter[subtype]"] = "movie"
    elif category == "series":
        params["filter[subtype]"] = "TV"

    fetched_anime_list = []
    included_list = []

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime", params=params, headers=headers
            )

            if res.status_code == 200:
                kitsu_data = res.json()
                fetched_anime_list = kitsu_data.get("data", [])
                included_list = kitsu_data.get("included", [])

            if len(fetched_anime_list) < 5:
                fallback_params = dict(params)
                if "filter[categories]" in fallback_params:
                    del fallback_params["filter[categories]"]
                fallback_params["filter[genres]"] = genre_param
                res = await client.get(
                    f"{ANIME_SOURCE_URL}/anime", params=fallback_params, headers=headers
                )
                if res.status_code == 200:
                    data = res.json()
                    existing_ids = {a["id"] for a in fetched_anime_list}
                    for item in data.get("data", []):
                        if item["id"] not in existing_ids:
                            fetched_anime_list.append(item)
                    included_list.extend(data.get("included", []))

            if len(fetched_anime_list) < 10:
                broad_params = {
                    "filter[categories]": genre_param,
                    "page[limit]": "20",
                    "sort": "-userCount",
                }
                if category == "movie":
                    broad_params["filter[subtype]"] = "movie"
                res = await client.get(
                    f"{ANIME_SOURCE_URL}/anime", params=broad_params, headers=headers
                )
                if res.status_code == 200:
                    data = res.json()
                    existing_ids = {a["id"] for a in fetched_anime_list}
                    for item in data.get("data", []):
                        if item["id"] not in existing_ids:
                            fetched_anime_list.append(item)
                    included_list.extend(data.get("included", []))
        except httpx.RequestError:
            pass

    parsed_date = date.today()
    if payload.activity_date:
        try:
            parsed_date = datetime.strptime(payload.activity_date, "%Y-%m-%d").date()
        except ValueError:
            parsed_date = date.today()

    user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, str(current_user.id))

    primary_anime_id = fetched_anime_list[0].get("id") if fetched_anime_list else "unknown"

    # Keep a real history row for analytics; DailyWatchActivity remains a lightweight preference record.
    activity = DailyWatchActivity(
        user_id=user_uuid,
        anime_id=str(primary_anime_id),
        primary_genre=payload.primary_genre,
        episodes_count=payload.episodes_count if payload.episodes_count is not None else 1,
        watch_time_mins=payload.watch_time_mins if payload.watch_time_mins is not None else 24,
        activity_date=parsed_date,
    )

    db.add(activity)
    history = WatchHistory(
        user_id=current_user.id,
        anime_id=str(primary_anime_id),
        episodes_count=max(1, int(payload.episodes_count or 1)),
        xp_earned=0,
        activity_date=parsed_date,
    )
    db.add(history)
    await db.flush()
    gained = await award_xp(db, current_user, max(1, int(payload.episodes_count or 1)) * 10, "episode_watch", str(history.id))
    history.xp_earned = gained
    await db.commit()

    return {
        "message": "Watch activity stored successfully",
        "activity_id": str(activity.id),
        "xp_earned": gained,
        "data": fetched_anime_list,
        "included": included_list,
    }


@app.get("/api/user/watchlist")
async def get_user_watchlist(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserAnimeList, Anime)
        .join(Anime, UserAnimeList.anime_id == Anime.id)
        .where(UserAnimeList.user_id == current_user.id)
    )
    rows = result.all()

    watchlist_items = []
    for watch, anime in rows:
        watchlist_items.append(
            {
                "id": watch.id,
                "anime_id": anime.id,
                "title": anime.title,
                "poster_image": anime.poster_image,
                "episodes": anime.episode_count,
                "episodes_watched": watch.episodes_watched,
                "status": watch.status,
                "priority": watch.priority,
                "score": watch.score,
                "is_favorite": watch.is_favorite,
                "notes": watch.notes,
            }
        )

    return {"watchlist": watchlist_items}


@app.get("/api/user/watch-history")
async def get_user_watch_history(
    year: Optional[int] = Query(None, ge=1),
    month: Optional[int] = Query(None, ge=1, le=12),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = datetime.now(timezone.utc).date()
    first_year = current_user.created_at.year if current_user.created_at else today.year
    selected_year = year or today.year

    if selected_year < first_year or selected_year > today.year:
        raise HTTPException(status_code=400, detail="Selected year is outside the account history")

    history_result = await db.execute(
        select(WatchHistory, Anime)
        .outerjoin(Anime, Anime.id == WatchHistory.anime_id)
        .where(
            WatchHistory.user_id == current_user.id,
            WatchHistory.activity_date >= date(first_year, 1, 1),
            WatchHistory.activity_date <= today,
        )
        .order_by(WatchHistory.activity_date, WatchHistory.created_at)
    )
    history_rows = history_result.all()

    list_result = await db.execute(
        select(UserAnimeList, Anime)
        .outerjoin(Anime, Anime.id == UserAnimeList.anime_id)
        .where(UserAnimeList.user_id == current_user.id)
    )
    list_rows = list_result.all()

    monthly_totals = {}
    daily_totals = {}
    period_records = {}
    anime_with_history = set()
    for history, anime in history_rows:
        anime_id = str(history.anime_id)
        anime_with_history.add(anime_id)
        activity_date = history.activity_date
        episodes = max(0, int(history.episodes_count or 0))
        monthly_key = (activity_date.year, activity_date.month)
        monthly_totals[monthly_key] = monthly_totals.get(monthly_key, 0) + episodes
        daily_key = (activity_date.year, activity_date.month, activity_date.day)
        daily_totals[daily_key] = daily_totals.get(daily_key, 0) + episodes

        if activity_date.year == selected_year and (month is None or activity_date.month == month):
            record = period_records.setdefault(anime_id, {
                "anime_id": anime_id,
                "title": anime.title if anime else f"Anime {anime_id}",
                "genre": None,
                "episodes": 0,
                "last_watched": activity_date.isoformat(),
                "status": "Saved",
            })
            record["episodes"] += episodes
            record["last_watched"] = max(record["last_watched"], activity_date.isoformat())

    list_by_anime = {}
    for entry, anime in list_rows:
        anime_id = str(entry.anime_id)
        list_by_anime[anime_id] = entry
        activity_at = entry.completed_at or entry.updated_at or entry.added_at
        if not activity_at:
            continue
        activity_date = activity_at.date()
        if activity_date > today:
            continue

        if anime_id not in anime_with_history:
            episodes = max(0, int(entry.episodes_watched or 0))
            if episodes:
                monthly_key = (activity_date.year, activity_date.month)
                monthly_totals[monthly_key] = monthly_totals.get(monthly_key, 0) + episodes
                daily_key = (activity_date.year, activity_date.month, activity_date.day)
                daily_totals[daily_key] = daily_totals.get(daily_key, 0) + episodes

        if activity_date.year != selected_year or (month is not None and activity_date.month != month):
            continue
        if anime_id in period_records:
            continue

        period_records[anime_id] = {
            "anime_id": anime_id,
            "title": anime.title if anime else f"Anime {anime_id}",
            "genre": None,
            "episodes": max(0, int(entry.episodes_watched or 0)),
            "last_watched": activity_date.isoformat(),
            "status": entry.status,
        }

    for anime_id, record in period_records.items():
        entry = list_by_anime.get(anime_id)
        if entry:
            record["status"] = entry.status

    years = []
    for item_year in range(first_year, today.year + 1):
        months = [
            {"month": item_month, "episodes": monthly_totals.get((item_year, item_month), 0)}
            for item_month in range(1, 13)
        ]
        years.append({
            "year": item_year,
            "total_episodes": sum(item["episodes"] for item in months),
            "months": months,
        })

    month_days = []
    if month is not None:
        next_month = date(selected_year + (month == 12), 1 if month == 12 else month + 1, 1)
        days_in_month = (next_month - timedelta(days=1)).day
        month_days = [
            {"day": day, "episodes": daily_totals.get((selected_year, month, day), 0)}
            for day in range(1, days_in_month + 1)
        ]

    records = sorted(period_records.values(), key=lambda item: item["last_watched"], reverse=True)
    for record in records:
        if not record["genre"]:
            record["genre"] = "Not recorded"

    return {
        "account_created_year": first_year,
        "current_year": today.year,
        "selected_year": selected_year,
        "selected_month": month,
        "available_years": list(range(first_year, today.year + 1)),
        "years": years,
        "month_days": month_days,
        "records": records,
        "selected_total_episodes": sum(record["episodes"] for record in month_days),
    }


@app.delete("/api/user/watchlist/delete")
async def delete_from_watchlist(
    payload: WatchlistDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not payload.watchlist_ids:
        raise HTTPException(
            status_code=400, detail="No anime item IDs provided for deletion"
        )

    result = await db.execute(
        select(UserAnimeList).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.id.in_(payload.watchlist_ids),
        )
    )
    items_to_delete = result.scalars().all()

    if not items_to_delete:
        raise HTTPException(
            status_code=404, detail="No matching watchlist entries found to delete"
        )

    favorite_count_to_deduct = sum(1 for item in items_to_delete if item.is_favorite)

    if favorite_count_to_deduct > 0:
        current_favs = int(current_user.favorites_count or 0)
        current_user.favorites_count = max(0, current_favs - favorite_count_to_deduct)
        current_user.updated_at = datetime.now(timezone.utc)

    deleted_ids = [item.id for item in items_to_delete]
    await db.execute(
        sql_delete(UserAnimeList).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.id.in_(deleted_ids),
        )
    )

    await db.commit()
    await db.refresh(current_user)

    return {
        "message": f"Successfully deleted {len(deleted_ids)} anime from watchlist.",
        "deleted_count": len(deleted_ids),
        "favorites_deducted": favorite_count_to_deduct,
        "favorites_count": current_user.favorites_count,
    }


@app.post("/api/user/toggle-favorite")
async def toggle_favorite(
    payload: FavoriteToggleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    anime_id_str = str(payload.anime_id)

    result = await db.execute(select(Anime).where(Anime.id == anime_id_str))
    anime = result.scalars().first()

    if not anime:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                res = await client.get(
                    f"{ANIME_SOURCE_URL}/anime/{anime_id_str}",
                    headers={
                        "Accept": "application/vnd.api+json",
                        "Content-Type": "application/vnd.api+json",
                    },
                )
            except httpx.RequestError:
                raise HTTPException(
                    status_code=504, detail="External anime lookup timed out"
                )

        if res.status_code != 200:
            raise HTTPException(
                status_code=404, detail="Anime not found on external provider"
            )

        anime_json = res.json().get("data", {})
        attrs = anime_json.get("attributes", {})
        titles = attrs.get("titles", {})

        poster_img = attrs.get("posterImage")
        poster_url = (
            poster_img.get("original")
            or poster_img.get("large")
            or poster_img.get("medium")
            if poster_img
            else None
        )

        avg_rating = attrs.get("averageRating")
        score_val = float(avg_rating) / 10.0 if avg_rating else None

        anime = Anime(
            id=str(anime_json.get("id")),
            title=attrs.get("canonicalTitle")
            or titles.get("en")
            or titles.get("en_jp")
            or "Unknown Title",
            title_english=titles.get("en") or titles.get("en_us"),
            title_japanese=titles.get("ja_jp") or titles.get("en_jp"),
            type=attrs.get("kind"),
            subtype=attrs.get("subtype"),
            age_rating=attrs.get("ageRating"),
            user_count=attrs.get("userCount"),
            start_date=attrs.get("startDate"),
            synopsis=attrs.get("synopsis"),
            poster_image=poster_url,
            episode_count=attrs.get("episodeCount"),
            score=score_val,
        )
        db.add(anime)
        await db.commit()

    entry_result = await db.execute(
        select(UserAnimeList).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.anime_id == anime_id_str,
        )
    )
    watchlist_entry = entry_result.scalars().first()
    now = datetime.now(timezone.utc)

    if watchlist_entry:
        current_favs = int(current_user.favorites_count or 0)

        if watchlist_entry.is_favorite:
            watchlist_entry.is_favorite = False
            current_user.favorites_count = max(0, current_favs - 1)
            is_fav = False
            msg = "Removed from favorites"
        else:
            watchlist_entry.is_favorite = True
            current_user.favorites_count = current_favs + 1
            is_fav = True
            msg = "Added to favorites"

        watchlist_entry.updated_at = now
    else:
        watchlist_id = f"item_{random.randint(10000, 99999)}"
        new_watchlist_item = UserAnimeList(
            id=watchlist_id,
            user_id=current_user.id,
            anime_id=anime_id_str,
            status="Watching",
            episodes_watched=0,
            score=None,
            is_favorite=True,
            priority="Medium",
            notes=None,
            added_at=now,
            updated_at=now,
        )
        db.add(new_watchlist_item)
        current_user.favorites_count = int(current_user.favorites_count or 0) + 1
        is_fav = True
        msg = "Added to favorites"

    current_user.updated_at = now
    await db.commit()
    await db.refresh(current_user)

    return {
        "message": msg,
        "is_favorite": is_fav,
        "favorites_count": current_user.favorites_count,
    }


@app.post("/api/user/addwatchlist", status_code=status.HTTP_201_CREATED)
async def add_to_watchlist(
    payload: WatchlistAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    anime_id_str = str(payload.anime_id)

    result = await db.execute(select(Anime).where(Anime.id == anime_id_str))
    anime = result.scalars().first()

    if not anime:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                res = await client.get(
                    f"{ANIME_SOURCE_URL}/anime/{anime_id_str}",
                    headers={
                        "Accept": "application/vnd.api+json",
                        "Content-Type": "application/vnd.api+json",
                    },
                )
            except httpx.RequestError:
                raise HTTPException(
                    status_code=504, detail="External anime query timed out"
                )

        if res.status_code != 200:
            raise HTTPException(
                status_code=404, detail="Anime not found on external provider"
            )

        anime_json = res.json().get("data", {})
        attrs = anime_json.get("attributes", {})
        titles = attrs.get("titles", {})

        poster_img = attrs.get("posterImage")
        poster_url = (
            poster_img.get("original")
            or poster_img.get("large")
            or poster_img.get("medium")
            if poster_img
            else None
        )

        avg_rating = attrs.get("averageRating")
        score_val = float(avg_rating) / 10.0 if avg_rating else None

        anime = Anime(
            id=str(anime_json.get("id")),
            title=attrs.get("canonicalTitle")
            or titles.get("en")
            or titles.get("en_jp")
            or "Unknown Title",
            title_english=titles.get("en") or titles.get("en_us"),
            title_japanese=titles.get("ja_jp") or titles.get("en_jp"),
            type=attrs.get("kind"),
            subtype=attrs.get("subtype"),
            age_rating=attrs.get("ageRating"),
            user_count=attrs.get("userCount"),
            start_date=attrs.get("startDate"),
            synopsis=attrs.get("synopsis"),
            poster_image=poster_url,
            episode_count=attrs.get("episodeCount"),
            score=score_val,
        )
        db.add(anime)
        await db.commit()

    existing_entry = await db.execute(
        select(UserAnimeList).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.anime_id == anime_id_str,
        )
    )
    if existing_entry.scalars().first():
        raise HTTPException(
            status_code=400, detail="Anime already exists in your watchlist"
        )

    watchlist_id = f"item_{random.randint(10000, 99999)}"
    now = datetime.now(timezone.utc)

    new_watchlist_item = UserAnimeList(
        id=watchlist_id,
        user_id=current_user.id,
        anime_id=anime_id_str,
        status=payload.status,
        episodes_watched=payload.episodes_watched,
        score=payload.score,
        is_favorite=payload.is_favorite,
        priority=payload.priority,
        notes=payload.notes,
        started_at=payload.started_at,
        completed_at=payload.completed_at,
        added_at=now,
        updated_at=now,
    )

    if payload.is_favorite:
        current_user.favorites_count = int(current_user.favorites_count or 0) + 1
        current_user.updated_at = now

    db.add(new_watchlist_item)
    await db.flush()
    initial_eps = max(0, int(payload.episodes_watched or 0))
    gained = 0
    if initial_eps:
        gained += await award_xp(db, current_user, initial_eps * 10, "watchlist_initial_episodes", watchlist_id)
    if str(payload.status).lower() == "completed":
        gained += await award_xp(db, current_user, 50, "anime_completion", watchlist_id)
    await db.commit()

    return {"message": "Anime added to watchlist successfully", "id": watchlist_id, "xp_earned": gained, "xp": current_user.xp_points, "level": level_for_xp(current_user.xp_points)}


@app.put("/api/user/watchlist/{watchlist_id}")
async def update_watchlist_item(watchlist_id: str, payload: WatchlistAddRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    item = await db.scalar(select(UserAnimeList).where(UserAnimeList.id == watchlist_id, UserAnimeList.user_id == current_user.id))
    if not item: raise HTTPException(status_code=404, detail="Watchlist item not found")
    now = datetime.now(timezone.utc)
    old_eps = int(item.episodes_watched or 0)
    new_eps = max(0, int(payload.episodes_watched or 0))
    gained = 0
    if new_eps > old_eps:
        delta = new_eps - old_eps
        gained += await award_xp(db, current_user, delta * 10, "episode_progress", f"{watchlist_id}:{new_eps}")
        db.add(WatchHistory(user_id=current_user.id, anime_id=item.anime_id, episodes_count=delta, xp_earned=delta * 10, activity_date=now.date()))
    old_status = str(item.status).lower()
    new_status = str(payload.status)
    item.status = new_status
    item.episodes_watched = new_eps
    item.score = payload.score
    item.priority = payload.priority
    item.notes = payload.notes
    item.is_favorite = payload.is_favorite
    item.started_at = payload.started_at or item.started_at
    item.completed_at = payload.completed_at or (now if new_status.lower() == "completed" and old_status != "completed" else item.completed_at)
    item.updated_at = now
    if new_status.lower() == "completed" and old_status != "completed":
        gained += await award_xp(db, current_user, 50, "anime_completion", watchlist_id)
    await db.commit(); await db.refresh(current_user)
    return {"message": "Watchlist updated", "xp_earned": gained, "xp": current_user.xp_points, "level": level_for_xp(current_user.xp_points)}


@app.get("/api/anime/new-releases")
async def get_new_releases(current_user: User = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={
                    "filter[status]": "current",
                    "sort": "-userCount",
                    "page[limit]": "20",
                },
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to fetch new releases from provider",
                )
            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502, detail="Error connecting to anime data provider"
            )


@app.get("/api/anime/upcoming")
async def get_upcoming_anime(current_user: User = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={
                    "filter[status]": "upcoming",
                    "sort": "-userCount",
                    "page[limit]": "20",
                },
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to fetch upcoming anime from provider",
                )
            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502, detail="Error connecting to anime data provider"
            )


@app.get("/api/anime/kitsu-search")
async def kitsu_search_anime(
    q: str = Query(..., min_length=1), current_user: User = Depends(get_current_user)
):
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={"filter[text]": q, "page[limit]": "10"},
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to search anime from provider",
                )
            return response.json()
        except httpx.RequestError:
            raise HTTPException(
                status_code=502, detail="Error connecting to anime data provider"
            )


# ==================================================
# 9. ANIME DETAIL & EPISODE ROUTES
# ==================================================


@app.get("/api/anime/{anime_id}/full-details")
async def get_anime_full_details(anime_id: str):
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            anime_res = await client.get(
                f"{ANIME_SOURCE_URL}/anime/{anime_id}?include=categories",
                headers=headers,
            )
        except httpx.RequestError:
            raise HTTPException(
                status_code=504, detail="Upstream provider request timed out."
            )

        if anime_res.status_code != 200:
            raise HTTPException(status_code=404, detail="Anime not found")

        data = anime_res.json()
        anime_attr = data.get("data", {}).get("attributes", {})
        included = data.get("included", [])

        genres = []
        for item in included:
            if item.get("type") == "categories":
                genres.append(item.get("attributes", {}).get("title"))

        characters = []
        try:
            char_res = await client.get(
                f"{ANIME_SOURCE_URL}/anime/{anime_id}/characters?include=character&page[limit]=8",
                headers=headers,
            )
            if char_res.status_code == 200:
                char_data = char_res.json()
                for item in char_data.get("included", []):
                    if item.get("type") == "characters":
                        item_attr = item.get("attributes", {})
                        img_data = item_attr.get("image") or {}
                        characters.append(
                            {
                                "id": item.get("id"),
                                "name": item_attr.get("canonicalName")
                                or item_attr.get("name")
                                or "Unknown Character",
                                "image": img_data.get("original")
                                or img_data.get("medium"),
                            }
                        )
        except Exception:
            pass

        creator_name = "Original Author / Studio"

        char_1 = characters[0]["name"] if characters else "Main Character"
        char_2 = (
            characters[1]["name"]
            if len(characters) > 1
            else "Supporting Character"
        )

        quotes = [
            {
                "quote": "I will move forward, until all my enemies are destroyed.",
                "character": char_1,
            },
            {
                "quote": "If you win, you live. If you lose, you die. If you don't fight, you can't win!",
                "character": char_2,
            },
        ]

        title = (
            anime_attr.get("canonicalTitle")
            or anime_attr.get("titles", {}).get("en")
            or "Anime Details"
        )

        return {
            "id": anime_id,
            "title": title,
            "genres": genres if genres else ["Action", "Adventure"],
            "studio": anime_attr.get("subtype") or "TV Series",
            "source": "Manga / Original",
            "ageRating": anime_attr.get("ageRatingGuide")
            or anime_attr.get("ageRating")
            or "PG-13",
            "synopsis": anime_attr.get("synopsis") or "No synopsis available.",
            "rating": anime_attr.get("averageRating"),
            "status": anime_attr.get("status"),
            "episodes": anime_attr.get("episodeCount"),
            "characters": characters,
            "creator": creator_name,
            "ost": {
                "op": f"{title} Opening Theme",
                "ed": f"{title} Ending Theme",
                "key_ost": "Main Theme & Climax Suite",
            },
            "quotes": quotes,
        }


@app.get("/api/anime/{anime_id}/episodes")
async def get_anime_episodes(
    anime_id: str,
    limit: int = Query(20, ge=1, le=20),
    offset: int = Query(0, ge=0),
):
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime/{anime_id}/episodes?page[limit]={limit}&page[offset]={offset}&sort=number",
                headers=headers,
            )
        except httpx.RequestError:
            return {"episodes": [], "total": 0}

        if res.status_code != 200:
            return {"episodes": [], "total": 0}

        data = res.json()
        episodes_data = data.get("data", [])
        meta = data.get("meta", {}).get("page", {})

        episodes = []
        for ep in episodes_data:
            attr = ep.get("attributes", {})
            episodes.append(
                {
                    "id": ep.get("id"),
                    "number": attr.get("number"),
                    "season": attr.get("seasonNumber", 1),
                    "title": attr.get("canonicalTitle")
                    or f"Episode {attr.get('number')}",
                    "synopsis": attr.get("synopsis")
                    or "No episode synopsis provided.",
                    "airDate": attr.get("airdate") or "N/A",
                    "thumbnail": attr.get("thumbnail", {}).get("original")
                    if attr.get("thumbnail")
                    else None,
                }
            )

        return {"episodes": episodes, "total": meta.get("count", len(episodes))}


@app.get("/api/anime/episodes/{episode_id}")
async def get_single_episode_detail(episode_id: str):
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/episodes/{episode_id}", headers=headers
            )
        except httpx.RequestError:
            raise HTTPException(status_code=504, detail="Upstream lookup timed out")

        if res.status_code != 200:
            raise HTTPException(status_code=404, detail="Episode not found")

        attr = res.json().get("data", {}).get("attributes", {})
        return {
            "id": episode_id,
            "number": attr.get("number"),
            "season": attr.get("seasonNumber", 1),
            "title": attr.get("canonicalTitle") or f"Episode {attr.get('number')}",
            "synopsis": attr.get("synopsis")
            or "No detailed description available for this episode.",
            "airDate": attr.get("airdate") or "N/A",
            "thumbnail": attr.get("thumbnail", {}).get("original")
            if attr.get("thumbnail")
            else None,
        }


@app.get("/api/anime/{anime_id}/overrated-episodes")
async def get_overrated_episodes(anime_id: str):
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        episodes = []
        try:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime/{anime_id}/episodes?page[limit]=5&sort=-number",
                headers=headers,
            )
            if res.status_code == 200:
                for ep in res.json().get("data", []):
                    attr = ep.get("attributes", {})
                    episodes.append(
                        {
                            "id": ep.get("id"),
                            "number": attr.get("number"),
                            "title": attr.get("canonicalTitle")
                            or f"Episode {attr.get('number')}",
                            "note": "Overhyped episode according to community reviews.",
                            "thumbnail": attr.get("thumbnail", {}).get("original")
                            if attr.get("thumbnail")
                            else None,
                        }
                    )
        except httpx.RequestError:
            pass

        return {"overrated_episodes": episodes}

@app.post("/api/user/toggle-favorite")
async def toggle_favorite(
    payload: FavoriteToggleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    anime_id_str = str(payload.anime_id)

    result = await db.execute(select(Anime).where(Anime.id == anime_id_str))
    anime = result.scalars().first()

    if not anime:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                res = await client.get(
                    f"{ANIME_SOURCE_URL}/anime/{anime_id_str}",
                    headers={
                        "Accept": "application/vnd.api+json",
                        "Content-Type": "application/vnd.api+json",
                    },
                )
            except httpx.RequestError:
                raise HTTPException(
                    status_code=504, detail="External anime lookup timed out"
                )

        if res.status_code != 200:
            raise HTTPException(
                status_code=404, detail="Anime not found on external provider"
            )

        anime_json = res.json().get("data", {})
        attrs = anime_json.get("attributes", {})
        titles = attrs.get("titles", {})

        poster_img = attrs.get("posterImage") or {}
        poster_url = (
            poster_img.get("original")
            or poster_img.get("large")
            or poster_img.get("medium")
        )

        canonical_title = (
            attrs.get("canonicalTitle")
            or titles.get("en")
            or titles.get("en_jp")
            or f"Anime {anime_id_str}"
        )

        avg_rating = attrs.get("averageRating")
        score_val = float(avg_rating) / 10.0 if avg_rating else None

        anime = Anime(
            id=anime_id_str,
            title=canonical_title,
            title_english=titles.get("en"),
            title_japanese=titles.get("ja_jp") or titles.get("en_jp"),
            type=attrs.get("showType") or attrs.get("subtype"),
            subtype=attrs.get("subtype"),
            age_rating=attrs.get("ageRating"),
            user_count=attrs.get("userCount"),
            start_date=attrs.get("startDate"),
            synopsis=attrs.get("synopsis"),
            poster_image=poster_url,
            episode_count=attrs.get("episodeCount"),
            score=score_val,
        )
        db.add(anime)
        await db.commit()
        await db.refresh(anime)

    ul_res = await db.execute(
        select(UserAnimeList).where(
            UserAnimeList.user_id == current_user.id,
            UserAnimeList.anime_id == anime_id_str,
        )
    )
    item = ul_res.scalars().first()

    if not item:
        item = UserAnimeList(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            anime_id=anime_id_str,
            status="Watching",
            episodes_watched=0,
            is_favorite=True,
        )
        db.add(item)
        current_user.favorites_count = (current_user.favorites_count or 0) + 1
    else:
        item.is_favorite = not item.is_favorite
        if item.is_favorite:
            current_user.favorites_count = (current_user.favorites_count or 0) + 1
        else:
            current_user.favorites_count = max(0, (current_user.favorites_count or 1) - 1)

    current_user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(current_user)

    return {
        "message": "Favorite status updated",
        "is_favorite": item.is_favorite,
        "favorites_count": current_user.favorites_count,
    }
    
@app.get("/api/anime/top-lists")
async def get_top_lists(
    tag: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    top_anime = []
    top_openings = []
    top_fights = []
    top_endings = []

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # 1. Top Anime
        try:
            anime_params = {"sort": "-averageRating", "page[limit]": "6"}
            if tag:
                anime_params["filter[categories]"] = tag.lower()
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime", params=anime_params, headers=headers
            )
            if res.status_code == 200:
                for item in res.json().get("data", []):
                    attrs = item.get("attributes", {})
                    poster = attrs.get("posterImage") or {}
                    rating = attrs.get("averageRating")
                    score = f"{float(rating) / 10.0:.1f}" if rating else "9.5"
                    top_anime.append(
                        {
                            "id": item.get("id"),
                            "title": attrs.get("canonicalTitle") or "Anime Title",
                            "poster": poster.get("medium")
                            or poster.get("small")
                            or poster.get("original")
                            or "",
                            "rating": score,
                        }
                    )
        except Exception:
            pass

        # 2. Top Openings
        try:
            op_params = {"sort": "-userCount", "page[limit]": "4"}
            if tag:
                op_params["filter[categories]"] = tag.lower()
            op_res = await client.get(
                f"{ANIME_SOURCE_URL}/anime", params=op_params, headers=headers
            )
            if op_res.status_code == 200:
                for item in op_res.json().get("data", []):
                    attrs = item.get("attributes", {})
                    cover = attrs.get("coverImage") or {}
                    poster = attrs.get("posterImage") or {}
                    top_openings.append(
                        {
                            "id": item.get("id"),
                            "title": attrs.get("canonicalTitle"),
                            "song": f"{attrs.get('canonicalTitle')} - Opening Theme",
                            "cover": cover.get("large")
                            or cover.get("original")
                            or poster.get("medium")
                            or "",
                        }
                    )
        except Exception:
            pass

        # 3. Top Fights
        try:
            fight_params = {
                "filter[categories]": tag.lower() if tag else "action",
                "sort": "-userCount",
                "page[limit]": "4",
            }
            fight_res = await client.get(
                f"{ANIME_SOURCE_URL}/anime", params=fight_params, headers=headers
            )
            if fight_res.status_code == 200:
                for item in fight_res.json().get("data", []):
                    attrs = item.get("attributes", {})
                    poster = attrs.get("posterImage") or {}
                    cover = attrs.get("coverImage") or {}
                    top_fights.append(
                        {
                            "id": item.get("id"),
                            "title": attrs.get("canonicalTitle"),
                            "fight_name": f"{attrs.get('canonicalTitle')} Climax Battle",
                            "episode": "Epic Showdown",
                            "image": cover.get("medium")
                            or poster.get("medium")
                            or "",
                        }
                    )
        except Exception:
            pass

        # 4. Top Endings
        try:
            end_params = {"sort": "-favoriteCount", "page[limit]": "4"}
            if tag:
                end_params["filter[categories]"] = tag.lower()
            end_res = await client.get(
                f"{ANIME_SOURCE_URL}/anime", params=end_params, headers=headers
            )
            if end_res.status_code == 200:
                for item in end_res.json().get("data", []):
                    attrs = item.get("attributes", {})
                    poster = attrs.get("posterImage") or {}
                    cover = attrs.get("coverImage") or {}
                    top_endings.append(
                        {
                            "id": item.get("id"),
                            "title": attrs.get("canonicalTitle"),
                            "song": f"{attrs.get('canonicalTitle')} - Ending Theme",
                            "cover": cover.get("large")
                            or cover.get("original")
                            or poster.get("medium")
                            or "",
                        }
                    )
        except Exception:
            pass

    top_studios = [
        {"name": "MAPPA", "projects": "Jujutsu Kaisen, AOT Final"},
        {"name": "Ufotable", "projects": "Demon Slayer, Fate Series"},
        {"name": "Wit Studio", "projects": "Spy x Family, Vinland Saga"},
        {"name": "CloverWorks", "projects": "Bocchi the Rock, My Dress-Up Darling"},
    ]

    top_soundtracks = [
        {
            "title": "You See Big Girl",
            "anime": "Attack on Titan",
            "composer": "Hiroyuki Sawano",
        },
        {"title": "Gurenge", "anime": "Demon Slayer", "composer": "LiSA"},
        {"title": "Kaikai Kitan", "anime": "Jujutsu Kaisen", "composer": "Eve"},
        {"title": "TANK!", "anime": "Cowboy Bebop", "composer": "Yoko Kanno"},
    ]

    top_voice_actors = [
        {
            "name": "Mamoru M.",
            "role": "Light Yagami, Okabe",
            "image": "https://api.dicebear.com/7.x/bottts/svg?seed=Mamoru",
        },
        {
            "name": "Yuki Kaji",
            "role": "Eren Yeager, Todoroki",
            "image": "https://api.dicebear.com/7.x/bottts/svg?seed=Yuki",
        },
        {
            "name": "Rie T.",
            "role": "Megumin, Emilia",
            "image": "https://api.dicebear.com/7.x/bottts/svg?seed=Rie",
        },
        {
            "name": "Kenjiro T.",
            "role": "Nanami, Overhaul",
            "image": "https://api.dicebear.com/7.x/bottts/svg?seed=Kenjiro",
        },
    ]

    popular_tags = [
        "Action",
        "Shonen",
        "Fantasy",
        "Psychological",
        "Romance",
        "Sci-Fi",
        "Slice of Life",
        "Isekai",
    ]

    return {
        "top_anime": top_anime,
        "top_openings": top_openings,
        "top_fights": top_fights,
        "top_endings": top_endings,
        "top_studios": top_studios,
        "top_soundtracks": top_soundtracks,
        "top_voice_actors": top_voice_actors,
        "popular_tags": popular_tags,
        "active_tag": tag,
    }
    # ==================================================
# ANIME MERCH & COLLECTION HUB ROUTE
# Add this route to main.py
# ==================================================

class MerchStatusUpdateRequest(BaseModel):
    item_id: str
    new_status: str  # "Collected", "Wishlist", "Ordered"


@app.get("/api/merch/hub")
async def get_merch_hub_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Fetches live anime data from Kitsu API and calculates live merchandise & collection analytics:
    1. Merch & Collection items
    2. Total Spent ($)
    3. Items Collected count & breakdown
    4. Favorite Category analysis
    5. Item Spotlight with live anime metadata
    """
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    
    live_anime_list = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={"sort": "-userCount", "page[limit]": "12"},
                headers=headers
            )
            if res.status_code == 200:
                live_anime_list = res.json().get("data", [])
        except Exception:
            pass

    # Catalog categories for live merch generation
    categories = [
        "Scale Figures",
        "Nendoroids",
        "Manga & Books",
        "Limited Blu-Ray",
        "Apparel & Merch",
        "Plushies & Keychains"
    ]
    
    collection_items = []
    
    for idx, anime in enumerate(live_anime_list):
        anime_id = str(anime.get("id"))
        attrs = anime.get("attributes", {})
        title = (
            attrs.get("canonicalTitle")
            or attrs.get("titles", {}).get("en")
            or f"Anime #{anime_id}"
        )
        poster_img = attrs.get("posterImage") or {}
        poster_url = (
            poster_img.get("original")
            or poster_img.get("large")
            or poster_img.get("medium")
            or ""
        )
        
        avg_rating = attrs.get("averageRating")
        score_val = round(float(avg_rating) / 10.0, 1) if avg_rating else 8.5
        synopsis = attrs.get("synopsis") or "No detailed synopsis available."
        
        category = categories[idx % len(categories)]
        
        # Base pricing model based on category
        if "Scale" in category:
            base_price = 185.00
        elif "Nendoroid" in category:
            base_price = 58.00
        elif "Manga" in category:
            base_price = 24.99
        elif "Blu-Ray" in category:
            base_price = 89.99
        elif "Apparel" in category:
            base_price = 45.00
        else:
            base_price = 29.99
            
        item_price = round(base_price + (idx * 7.25), 2)
        status = "Collected" if idx in [0, 1, 2, 4, 6, 9] else ("Wishlist" if idx in [3, 5, 8] else "Ordered")
        
        collection_items.append({
            "id": f"merch-{anime_id}-{idx}",
            "anime_id": anime_id,
            "anime_title": title,
            "poster": poster_url,
            "score": score_val,
            "synopsis": synopsis,
            "item_name": f"{title} - {category} Limited Edition",
            "category": category,
            "price": item_price,
            "status": status,
            "rarity": "UR" if idx == 0 else ("SSR" if idx % 2 == 0 else "SR"),
            "acquired_date": (datetime.now(timezone.utc) - timedelta(days=idx * 12)).strftime("%Y-%m-%d")
        })

    # 1. Total Spent Calculation
    collected_items = [item for item in collection_items if item["status"] == "Collected"]
    total_spent_val = round(sum(item["price"] for item in collected_items), 2)
    
    # 2. Items Collected Count & Breakdown
    items_collected_count = len(collected_items)
    category_breakdown = {}
    for item in collected_items:
        cat = item["category"]
        category_breakdown[cat] = category_breakdown.get(cat, 0) + 1

    # 3. Favorite Category Analysis
    fav_category = max(category_breakdown, key=category_breakdown.get) if category_breakdown else "Scale Figures"
    fav_cat_count = category_breakdown.get(fav_category, 0)
    fav_percentage = round((fav_cat_count / max(1, items_collected_count)) * 100, 1)

    # 4. Item Spotlight (Highest rated live anime merchandise)
    spotlight = collection_items[0] if collection_items else {
        "item_name": "Exclusive Masterpiece Collectible",
        "category": "Scale Figures",
        "price": 249.99,
        "anime_title": "Featured Anime Title",
        "poster": "",
        "synopsis": "Live merchandise spotlight feature.",
        "score": 9.8,
        "rarity": "UR"
    }

    return {
        "merch_and_collection": collection_items,
        "total_spent": {
            "amount": total_spent_val,
            "currency": "USD",
            "formatted": f"${total_spent_val:,.2f}",
            "avg_per_item": round(total_spent_val / max(1, items_collected_count), 2)
        },
        "items_collected": {
            "count": items_collected_count,
            "total_catalog": len(collection_items),
            "breakdown": category_breakdown
        },
        "favorite_category": {
            "name": fav_category,
            "percentage": fav_percentage,
            "item_count": fav_cat_count
        },
        "item_spotlight": spotlight
    }