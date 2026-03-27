from fastapi import FastAPI
from app.db.session import engine
from app.db.base import Base

from app.api.routes import auth

app = FastAPI()

# Create tables
Base.metadata.create_all(bind=engine)

app.include_router(auth.router, prefix="/auth", tags=["Auth"])