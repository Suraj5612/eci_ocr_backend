from sqlalchemy.orm import Session
from app.models.user import User


def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username.lower()).first()


def create_user(db: Session, user_data: dict):
    user = User(**user_data)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user