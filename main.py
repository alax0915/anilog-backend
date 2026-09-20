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
from sqlalchemy import (
    String,
    DateTime,
    Integer,
    JSON,
    func,
    Boolean,
    Text,
    ForeignKey,
    delete as sql_delete,
)
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
    origin.strip() for origin in raw_origins.split(",") if origin.strip()
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
            f"{ANIME_SOURCE_URL}/anime",
            params={"filter[text]": q, "page[limit]": "5"},
            headers={
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json",
            },
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

    # 1. Retrieve the watchlist items belonging to current user that are slated for deletion
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

    # 2. Check how many of the selected items were marked as favorite
    favorite_count_to_deduct = sum(1 for item in items_to_delete if item.is_favorite)

    # 3. Deduct from user's favorites_count column accordingly
    if favorite_count_to_deduct > 0:
        current_favs = int(current_user.favorites_count or 0)
        current_user.favorites_count = max(0, current_favs - favorite_count_to_deduct)
        current_user.updated_at = datetime.now(timezone.utc)

    # 4. Perform deletion
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
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime/{anime_id_str}",
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
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

    # 1. Check if anime exists in local database, fetch & cache if missing
    result = await db.execute(select(Anime).where(Anime.id == anime_id_str))
    anime = result.scalars().first()

    if not anime:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{ANIME_SOURCE_URL}/anime/{anime_id_str}",
                headers={
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                },
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

    # 2. Check if already in user's watchlist
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

    # 3. Create watchlist entry & increment user's favorites_count if is_favorite is True
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
    await db.commit()

    return {"message": "Anime added to watchlist successfully", "id": watchlist_id}


@app.get("/api/anime/new-releases")
async def get_new_releases(current_user: User = Depends(get_current_user)):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={
                    "filter[status]": "current",
                    "sort": "-userCount",
                    "page[limit]": "15",
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
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={
                    "filter[status]": "upcoming",
                    "sort": "-userCount",
                    "page[limit]": "10",
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
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{ANIME_SOURCE_URL}/anime",
                params={"filter[text]": q, "page[limit]": "6"},
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
