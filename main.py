import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import httpx
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import String, DateTime, Integer, JSON, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.future import select

# --------------------------------------------------
# 1. ENVIRONMENT CONFIGURATION
# --------------------------------------------------
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key-change-this")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # Token valid for 24 hours

# CORS Configuration - Add your frontend URLs here
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is missing! Check your .env file.")

# --------------------------------------------------
# 2. SECURITY & AUTH SETUP
# --------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# Pool of gender-neutral profile avatar images
DEFAULT_AVATARS = [
    "https://api.dicebear.com/7.x/bottts/svg?seed=Shadow",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Panda",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Phoenix",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Cyber",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Pixel",
    "https://api.dicebear.com/7.x/bottts/svg?seed=Ghost"
]

# --------------------------------------------------
# 3. NEON DATABASE SETUP (SQLAlchemy Async)
# --------------------------------------------------
engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="client", nullable=False)
    avatar_seed: Mapped[str] = mapped_column(String(255), nullable=True)
    bio: Mapped[str] = mapped_column(String(255), default="I love anime", nullable=True)
    streak_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    xp_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc), 
        onupdate=lambda: datetime.now(timezone.utc)
    )
    daily_watch_activities: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    notifications: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reaction_logs: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    refresh_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    user_anime_lists: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    user_settings: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, 
        default=lambda: {"app_theme": "dark", "content_filter": True}
    )

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# --------------------------------------------------
# 4. PYDANTIC SCHEMAS
# --------------------------------------------------
class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    avatar_seed: Optional[str]
    bio: Optional[str]
    streak_count: int
    last_active_at: datetime
    xp_points: int
    level: int
    created_at: datetime
    updated_at: datetime
    daily_watch_activities: Optional[Dict[str, Any]] = None
    notifications: int
    reaction_logs: Optional[Dict[str, Any]] = None
    refresh_tokens: int
    user_anime_lists: Optional[Dict[str, Any]] = None
    user_settings: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

# --------------------------------------------------
# 5. AUTH DEPENDENCY
# --------------------------------------------------
async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except (JWTError, ValueError):
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user

# --------------------------------------------------
# 6. FASTAPI APP INITIALIZATION
# --------------------------------------------------
app = FastAPI(title="AniLog API")

# --------------------------------------------------
# CORS MIDDLEWARE CONFIGURATION
# --------------------------------------------------
# Configure CORS to allow your frontend to communicate with the backend
# 
# For development: allow_origins=["*"] works but is less secure
# For production: specify exact origins like ["https://yourfrontend.com"]
#
# If using credentials (cookies, auth headers), you cannot use "*"
# You must specify exact origins when allow_credentials=True

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,  # Configure via ALLOWED_ORIGINS env var
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,  # Cache preflight requests for 10 minutes
)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --------------------------------------------------
# 7. ROUTES
# --------------------------------------------------

# Health Check
@app.get("/")
def home():
    return {"status": "AniLog Backend is live!"}

# CORS Preflight Handler (optional - FastAPI handles this automatically with CORSMiddleware)
@app.options("/{full_path:path}")
async def preflight_handler():
    return {"message": "CORS preflight OK"}

# Register Route
@app.post("/api/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    # Check existing username
    existing_user = await db.execute(select(User).where(User.username == user_data.username))
    if existing_user.scalars().first():
        raise HTTPException(status_code=400, detail="Username already registered")

    # Check existing email
    existing_email = await db.execute(select(User).where(User.email == user_data.email))
    if existing_email.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Determine Role: 1st person registered = admin, otherwise client
    user_count_result = await db.execute(select(func.count(User.id)))
    total_users = user_count_result.scalar() or 0
    assigned_role = "admin" if total_users == 0 else "client"

    # Select random gender-neutral avatar
    random_avatar = random.choice(DEFAULT_AVATARS)
    now = datetime.now(timezone.utc)

    # Create & Save User
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        role=assigned_role,
        avatar_seed=random_avatar,
        bio="I love anime",
        streak_count=1,
        last_active_at=now,
        xp_points=0,
        level=1,
        created_at=now,
        updated_at=now,
        daily_watch_activities=None,
        notifications=0,
        reaction_logs=None,
        refresh_tokens=0,
        user_anime_lists=None,
        user_settings={"app_theme": "dark", "content_filter": True}
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

# Login Route (Accepts Email and Password)
@app.post("/api/auth/login", response_model=Token)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalars().first()

    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password"
        )

    # Handle Streak logic and last_active_at
    now = datetime.now(timezone.utc)
    if user.last_active_at:
        last_active_date = user.last_active_at.date()
        today = now.date()
        days_diff = (today - last_active_date).days

        if days_diff == 1:
            user.streak_count += 1
        elif days_diff > 1:
            user.streak_count = 1

    user.last_active_at = now
    user.updated_at = now
    await db.commit()

    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

# Protected Current User Profile Route
@app.get("/api/auth/me", response_model=UserResponse)
async def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user

# Anime Proxy Search Route
@app.get("/api/anime/search")
async def search_anime(q: str = Query(..., min_length=1)):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.jikan.moe/v4/anime?q={q}&limit=5")
        
        if response.status_code == 200:
            data = response.json().get("data", [])
            results = [
                {
                    "id": anime["mal_id"],
                    "title": anime["title"],
                    "episodes": anime["episodes"],
                    "score": anime["score"],
                    "image": anime["images"]["jpg"]["image_url"]
                }
                for anime in data
            ]
            return {"results": results}
        
        return {"error": "Failed to fetch anime data"}