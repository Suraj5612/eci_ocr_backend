from sqlalchemy.orm import Session
from app.models.booths import Booth
from app.models.constituency import Constituency
from app.models.districts import District
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

    role = data.role

    mandal_id = None
    district_id = None
    constituency_id = None
    booth_id = None


    if role == "booth":
        booth = db.query(Booth).filter(Booth.id == data.booth_id).first()

        if not booth:
            raise AppException(400, "INVALID_BOOTH", "Invalid booth_id")

        booth_id = booth.id
        constituency_id = booth.constituency_id

        constituency = db.query(Constituency).filter(
            Constituency.id == constituency_id
        ).first()

        district_id = constituency.district_id
        mandal_id = db.query(District).filter(
            District.district_id == district_id
        ).first().mandala_id


    elif role == "constituency":
        constituency = db.query(Constituency).filter(
            Constituency.id == data.constituency_id
        ).first()

        if not constituency:
            raise AppException(400, "INVALID_CONSTITUENCY", "Invalid constituency_id")

        constituency_id = constituency.id
        district_id = constituency.district_id

        mandal_id = db.query(District).filter(
            District.district_id == district_id
        ).first().mandala_id


    elif role == "district":
        district = db.query(District).filter(
            District.district_id == data.district_id
        ).first()

        if not district:
            raise AppException(400, "INVALID_DISTRICT", "Invalid district_id")

        district_id = district.district_id
        mandal_id = district.mandala_id


    elif role == "mandal":
        mandal_id = data.mandal_id


    elif role == "superadmin":
        pass

    else:
        raise AppException(400, "INVALID_ROLE", "Invalid role")

    user_dict = {
        "first_name": data.firstName,
        "middle_name": data.middleName,
        "last_name": data.lastName,
        "username": username,
        "email": data.email,
        "mobile": data.mobile,
        "hashed_password": hash_password(data.password),

        "role": role,
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