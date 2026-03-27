from pydantic import BaseModel, EmailStr
from typing import Optional

class RegisterRequest(BaseModel):
    firstName: str
    middleName: Optional[str] = None
    lastName: str

    username: str
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None

    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"