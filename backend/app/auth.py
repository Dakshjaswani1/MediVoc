"""Password hashing, signed tokens and role guards.

Deliberately stdlib-only: pbkdf2 for passwords, HMAC-signed JSON for tokens.
ponytail: stateless tokens cannot be revoked before they expire; add a
`sessions` table if logout-everywhere is ever needed.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from app.db import connect

SECRET = os.getenv("MEDIVOC_SECRET", "medivoc-dev-secret-change-me").encode()
TOKEN_TTL_SECONDS = 7 * 24 * 3600
ROLES = ("patient", "doctor", "admin")

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


# ----------------------------- passwords -----------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return hmac.compare_digest(digest.hex(), expected)


# ------------------------------- tokens ------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user_id: int, role: str) -> str:
    payload = _b64(json.dumps({"uid": user_id, "role": role, "exp": int(time.time()) + TOKEN_TTL_SECONDS}).encode())
    signature = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_token(token: str) -> Optional[dict]:
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    data = json.loads(_unb64(payload))
    if data.get("exp", 0) < time.time():
        return None
    return data


# ---------------------------- dependencies ---------------------------

def current_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    data = read_token(credentials.credentials)
    if data is None:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, email, phone, role, doctor_id FROM users WHERE id = ?",
            (data["uid"],),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return dict(row)


def require_role(*roles: str):
    """Returns a dependency that only lets the given roles through."""

    def guard(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail=f"This action requires the {' or '.join(roles)} role.")
        return user

    return guard


# ------------------------------- routes ------------------------------

class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    phone: str = Field(min_length=10, max_length=16)
    password: str = Field(min_length=6, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "role": row["role"],
        "doctor_id": row["doctor_id"],
    }


@router.post("/register", status_code=201)
def register(payload: RegisterRequest) -> dict:
    """Self-service registration, patients only. Staff accounts are created by an admin."""
    with connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM users WHERE email = ?", (payload.email.lower(),)
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

        cursor = connection.execute(
            "INSERT INTO users (name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, 'patient')",
            (payload.name.strip(), payload.email.lower(), payload.phone.strip(), hash_password(payload.password)),
        )
        connection.commit()
        row = connection.execute(
            "SELECT id, name, email, phone, role, doctor_id FROM users WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    user = _public_user(dict(row))
    return {"token": make_token(user["id"], user["role"]), "user": user}


@router.post("/login")
def login(payload: LoginRequest) -> dict:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ?", (payload.email.lower(),)
        ).fetchone()

    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    user = _public_user(dict(row))
    return {"token": make_token(user["id"], user["role"]), "user": user}


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    return _public_user(user)
