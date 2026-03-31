from pydantic import BaseModel, EmailStr, field_validator, model_validator
from typing import Optional


class RegisterRequest(BaseModel):
    firstName: str
    middleName: Optional[str] = None
    lastName: str

    username: str
    email: Optional[EmailStr] = None
    mobile: Optional[str] = None

    password: str

    # 🔥 NEW FIELDS
    role: str
    mandal_id: Optional[int] = None
    district_id: Optional[int] = None
    constituency_id: Optional[int] = None
    booth_id: Optional[int] = None

    # -----------------------
    # FIELD VALIDATORS
    # -----------------------

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

    @field_validator("role")
    def validate_role(cls, v):
        valid_roles = ["superadmin", "mandal", "district", "constituency", "booth"]
        if v not in valid_roles:
            raise ValueError("Invalid role")
        return v

    # -----------------------
    # 🔥 HIERARCHY VALIDATION
    # -----------------------

    @model_validator(mode="after")
    def validate_hierarchy(self):
        if self.role == "mandal" and not self.mandal_id:
            raise ValueError("mandal_id is required for mandal role")

        if self.role == "district" and not self.district_id:
            raise ValueError("district_id is required for district role")

        if self.role == "constituency" and not self.constituency_id:
            raise ValueError("constituency_id is required for constituency role")

        if self.role == "booth" and not self.booth_id:
            raise ValueError("booth_id is required for booth role")

        return self

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"