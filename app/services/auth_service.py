from sqlalchemy.orm import Session
from app.repositories.user_repo import (
    get_user_by_username,
    get_user_by_mobile,
    get_user_by_email,
    create_user
)
from app.utils.exceptions import AppException
from app.core.security import hash_password, verify_password, create_access_token


def register_user(db: Session, data):
    username = data.username.lower()

    if get_user_by_username(db, username):
        raise AppException(
            status_code=400,
            code="USERNAME_EXISTS",
            message="Username already exists",
            field="username"
        )

    if data.email and get_user_by_email(db, data.email):
        raise AppException(
            status_code=400,
            code="EMAIL_EXISTS",
            message="Email already registered",
            field="email"
        )

    if data.mobile and get_user_by_mobile(db, data.mobile):
        raise AppException(
            status_code=400,
            code="MOBILE_EXISTS",
            message="Mobile number already registered",
            field="mobile"
        )

    valid_roles = ["superadmin", "mandal", "district", "constituency", "booth"]

    if data.role not in valid_roles:
        raise AppException(
            status_code=400,
            code="INVALID_ROLE",
            message="Invalid user role",
            field="role"
        )

    mandal_id = None
    district_id = None
    constituency_id = None
    booth_id = None

    if data.role == "mandal":
        if not data.mandal_id:
            raise AppException(400, "MANDAL_REQUIRED", "mandal_id is required")
        mandal_id = data.mandal_id

    elif data.role == "district":
        if not data.district_id:
            raise AppException(400, "DISTRICT_REQUIRED", "district_id is required")
        district_id = data.district_id

    elif data.role == "constituency":
        if not data.constituency_id:
            raise AppException(400, "CONSTITUENCY_REQUIRED", "constituency_id is required")
        constituency_id = data.constituency_id

    elif data.role == "booth":
        if not data.booth_id:
            raise AppException(400, "BOOTH_REQUIRED", "booth_id is required")
        booth_id = data.booth_id

    user_dict = {
        "first_name": data.firstName,
        "middle_name": data.middleName,
        "last_name": data.lastName,
        "username": username,
        "email": data.email,
        "mobile": data.mobile,
        "hashed_password": hash_password(data.password),

        "role": data.role,
        "mandal_id": mandal_id,
        "district_id": district_id,
        "constituency_id": constituency_id,
        "booth_id": booth_id,
    }

    return create_user(db, user_dict)


def login_user(db: Session, username: str, password: str):
    username = username.lower()

    user = get_user_by_username(db, username)

    if not user or not verify_password(password, user.hashed_password):
        raise AppException(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message="Invalid username or password"
        )

    token = create_access_token({"sub": user.username})

    return {
        "access_token": token,
        "token_type": "bearer"
    }