from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse
from app.services.auth_service import register_user, login_user

router = APIRouter()


@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    user = register_user(db, data)
    return {
        "message": "User registered successfully",
        "username": user.username
    }


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    return login_user(db, data.username, data.password)