import email
import stat

from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel, EmailStr
from typing import Optional
import hashlib
import secrets

from sympy import use

app = FastAPI(title="Auth login API")

# Data models
class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class User(BaseModel):
    id: int
    name: str
    email: EmailStr

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# In-memory "database"
users_db = {} # email -> user data
tokens_db = {} # token -> email
next_user_id = 1

# password hashing
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# random token generation
def generate_token() -> str:
    return secrets.token_urlsafe(32)

# POST /auth/register - user registration
@app.post(
    "/auth/register", 
    response_model=User,
    status_code=201
)
async def register(user: UserRegister):
    global next_user_id

    # check if email already exists
    if user.email in users_db:
        raise HTTPException(status_code=400, detail="email already registered")
    
    # create user
    new_user = User(
        id=next_user_id,
        name=user.name,
        email=user.email
    )
    users_db[user.email] = {
        "user": new_user,
        "password_hash": hash_password(user.password)
    }

    next_user_id += 1   
    return new_user

# POST /auth/login - user login
@app.post("/auth/login", response_model=Token)
async def login(user: UserLogin):
    # check if email exists
    if user.email not in users_db:
        raise HTTPException(
            status_code=401, detail="invalid email or password"
        )
    
    # check password
    stored = users_db[user.email]
    if stored["password_hash"] != hash_password(user.password):
        raise HTTPException(
            status_code=401, detail="invalid email or password"
        )

    # token generation
    token = generate_token()
    tokens_db[token] = stored["user"].id

    return Token(access_token=token)

# get current user (dependency)
def get_current_user(token: str = Header(...)) -> User:
    if token not in tokens_db:
        raise HTTPException(status_code=401, detail="invalid token"
    )
    user_id = tokens_db[token]
    for u in users_db.values():
        if u["user"].id == user_id:
            return u["user"]
    raise HTTPException(status_code=401, detail="user not found")

# GET /profile - get user profile (protected)
@app.get("/profile", response_model=User)
async def get_profile(
    current_user: User = Depends(get_current_user)
):
    return current_user
