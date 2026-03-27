from sqlalchemy.orm import Session
from app.repositories.user_repo import get_user_by_username, create_user
from app.core.security import hash_password, verify_password, create_access_token
from fastapi import HTTPException, status


def register_user(db: Session, data):
    username = data.username.lower()

    # check if user exists
    existing_user = get_user_by_username(db, username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )

    user_dict = {
        "first_name": data.firstName,
        "middle_name": data.middleName,
        "last_name": data.lastName,
        "username": username,
        "email": data.email,
        "mobile": data.mobile,
        "hashed_password": hash_password(data.password),
    }

    user = create_user(db, user_dict)

    return user


def login_user(db: Session, username: str, password: str):
    username = username.lower()

    user = get_user_by_username(db, username)

    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    token = create_access_token({"sub": user.username})

    return {
        "access_token": token,
        "token_type": "bearer"
    }