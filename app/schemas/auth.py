from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional


class RegisterRequest(BaseModel):
    firstName: str
    middleName: Optional[str] = None
    lastName: str

    username: str
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None

    password: str

    @field_validator("firstName")
    def validate_first_name(cls, v):
        if not v or not v.strip():
            raise ValueError("First name is required")
        return v

    @field_validator("username")
    def validate_username(cls, v):
        if not v or not v.strip():
            raise ValueError("Username is required")
        if len(v) < 5:
            raise ValueError("Username must be at least 5 characters")
        return v.lower()

    @field_validator("password")
    def validate_password(cls, v):
        if not v:
            raise ValueError("Password is required")
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v

    @field_validator("mobile")
    def validate_mobile(cls, v):
        if v and len(v) != 10:
            raise ValueError("Mobile number must be 10 digits")
        return v

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"