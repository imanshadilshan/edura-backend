from typing import Optional

from models import OtpPurpose, UserRole
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 900


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class OtpRequest(BaseModel):
    user_id: int
    purpose: OtpPurpose = OtpPurpose.email_verify


class OtpVerifyRequest(BaseModel):
    user_id: int
    code: str
    purpose: OtpPurpose = OtpPurpose.email_verify


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: UserRole = UserRole.student


class UserResponse(BaseModel):
    id: int
    email: str
    role: UserRole
    is_active: bool
    is_email_verified: bool

    model_config = {"from_attributes": True}


class CurrentUserResponse(BaseModel):
    id: int
    email: str
    role: UserRole
    is_active: bool
    is_email_verified: bool
    auth_provider: str
    has_password: bool

    model_config = {"from_attributes": True}


class ErrorResponse(BaseModel):
    error: str
    message: str


# ── Google Sign-In (implicit flow: the frontend sends an OAuth *access token*
# obtained via @react-oauth/google's useGoogleLogin({flow: 'implicit'}), which
# this service verifies by calling Google's userinfo endpoint) ──────────────


class GoogleLoginRequest(BaseModel):
    access_token: str


class SetPasswordRequest(BaseModel):
    """Let a Google-only account (no password yet) add one, unlocking
    email+password login on the same account alongside Google Sign-In."""

    new_password: str = Field(..., min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)
