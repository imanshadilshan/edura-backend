from datetime import datetime
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


# ── Admin management (admin-only; never reachable from public /register) ────


class CreateAdminRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    first_name: str
    last_name: str


class UserStatusUpdateRequest(BaseModel):
    is_active: bool


class InternalUserResponse(BaseModel):
    """Bulk-listing shape for other services (e.g. user_service enriching an
    admin-facing profile list with the is_active/email fields that only
    auth_service owns) — served from an unauthenticated /internal/ route,
    matching the pattern already used for cross-service reads elsewhere
    (course_service's /owner, content_service's /internal/receipt-upload)."""

    id: int
    email: str
    role: UserRole
    is_active: bool
    is_email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}
