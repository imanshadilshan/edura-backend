import bcrypt
from database import SessionLocal
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from models import User, UserRole
from schemas import (
    ChangePasswordRequest,
    CurrentUserResponse,
    GoogleLoginRequest,
    LoginRequest,
    OtpRequest,
    OtpVerifyRequest,
    RefreshRequest,
    SetPasswordRequest,
    TokenResponse,
    UserRegisterRequest,
    UserResponse,
)
from services import GoogleAuthService, OtpService, SessionService, TokenService
from sqlalchemy.orm import Session

from shared.auth import require_role

router = APIRouter(tags=["auth"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/admin")
def admin_only(payload: dict = Depends(require_role(["admin"]))):
    return {"message": "Welcome Admin", "user_id": payload.get("sub")}


@router.post("/register", response_model=UserResponse)
def register(req: UserRegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail={"error": "EMAIL_EXISTS", "message": "Email already registered"},
        )

    # Public self-registration can only ever create student/teacher accounts.
    # Admin accounts are created out-of-band (see scripts/create_master_admin.py)
    # — never accept role=admin from a client request here.
    role = req.role if req.role != UserRole.admin else UserRole.student

    hashed = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )
    user = User(
        email=req.email,
        hashed_password=hashed,
        role=role,
        is_active=True,
        is_email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "INVALID_CREDENTIALS",
                "message": "Email or password is incorrect",
            },
        )

    # Google-only accounts have no password to check against.
    if not user.hashed_password:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "GOOGLE_ONLY_ACCOUNT",
                "message": "This account uses Google Sign-In. Please continue with Google, or set a password from your profile after signing in.",
            },
        )

    if not bcrypt.checkpw(
        req.password.encode("utf-8"), user.hashed_password.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "INVALID_CREDENTIALS",
                "message": "Email or password is incorrect",
            },
        )

    role_str = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    access_token = TokenService.create_access_token(user.id, role_str, user.email)
    refresh_token = TokenService.create_refresh_token(db, user.id)

    # Session limit management in Redis
    SessionService.create_session(user.id, role_str)

    # Set HttpOnly refresh token cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )

    return TokenResponse(access_token=access_token, token_type="Bearer", expires_in=900)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    req: RefreshRequest = None,
    refresh_token: str = Cookie(None),
    response: Response = None,
    db: Session = Depends(get_db),
):
    token_val = (
        req.refresh_token if req and req.refresh_token else None
    ) or refresh_token
    if not token_val:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "MISSING_REFRESH_TOKEN",
                "message": "Refresh token not provided",
            },
        )

    user_id, new_refresh_token = TokenService.rotate_refresh_token(db, token_val)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "USER_NOT_FOUND",
                "message": "User associated with refresh token not found",
            },
        )

    role_str = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    new_access_token = TokenService.create_access_token(user.id, role_str, user.email)

    if response:
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=7 * 24 * 3600,
        )

    return TokenResponse(
        access_token=new_access_token, token_type="Bearer", expires_in=900
    )


@router.post("/google", response_model=TokenResponse)
def google_login(req: GoogleLoginRequest, response: Response, db: Session = Depends(get_db)):
    """
    Sign in (or sign up) with Google. Unifies with an existing email+password
    account: looked up by google_id first, then by email — if a password
    account with the same email exists, this links google_id onto it instead
    of creating a duplicate, so the same account works with both methods.
    """
    google_info = GoogleAuthService.verify_access_token(req.access_token)
    if not google_info:
        raise HTTPException(
            status_code=401,
            detail={"error": "INVALID_GOOGLE_TOKEN", "message": "Invalid or expired Google token"},
        )

    google_id = google_info["google_id"]
    email = google_info["email"]

    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()

    if user:
        if not user.google_id:
            user.google_id = google_id
            if not user.hashed_password:
                user.auth_provider = "google"
        db.commit()
    else:
        user = User(
            email=email,
            hashed_password=None,
            role=UserRole.student,
            is_active=True,
            is_email_verified=True,
            google_id=google_id,
            auth_provider="google",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    role_str = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    access_token = TokenService.create_access_token(user.id, role_str, user.email)
    refresh_token = TokenService.create_refresh_token(db, user.id)

    SessionService.create_session(user.id, role_str)

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )

    return TokenResponse(access_token=access_token, token_type="Bearer", expires_in=900)


@router.get("/me", response_model=CurrentUserResponse)
def get_me(
    payload: dict = Depends(require_role(["student", "teacher", "admin"])),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=404, detail={"error": "USER_NOT_FOUND"})
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        is_email_verified=user.is_email_verified,
        auth_provider=user.auth_provider,
        has_password=user.hashed_password is not None,
    )


@router.post("/set-password")
def set_password(
    req: SetPasswordRequest,
    payload: dict = Depends(require_role(["student", "teacher", "admin"])),
    db: Session = Depends(get_db),
):
    """Let a Google-only account add a password, so it can log in either way."""
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=404, detail={"error": "USER_NOT_FOUND"})
    if user.hashed_password:
        raise HTTPException(
            status_code=400,
            detail={"error": "PASSWORD_ALREADY_SET", "message": "A password is already set. Use change-password instead."},
        )

    user.hashed_password = bcrypt.hashpw(
        req.new_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    db.commit()
    return {"message": "Password set successfully. You can now log in with email and password."}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    payload: dict = Depends(require_role(["student", "teacher", "admin"])),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user:
        raise HTTPException(status_code=404, detail={"error": "USER_NOT_FOUND"})
    if not user.hashed_password:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_PASSWORD_SET", "message": "No password set yet. Use set-password instead."},
        )
    if not bcrypt.checkpw(
        req.current_password.encode("utf-8"), user.hashed_password.encode("utf-8")
    ):
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_CREDENTIALS", "message": "Current password is incorrect"},
        )

    user.hashed_password = bcrypt.hashpw(
        req.new_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    db.commit()
    return {"message": "Password updated successfully"}


@router.post("/otp/request")
def request_otp(req: OtpRequest):
    code = OtpService.generate_otp(req.user_id, req.purpose.value)
    return {"message": "OTP sent successfully", "code": code}


@router.post("/otp/verify")
def verify_otp(req: OtpVerifyRequest):
    OtpService.verify_otp(req.user_id, req.code, req.purpose.value)
    return {"message": "OTP verified successfully"}


@router.get("/protected")
def protected_route(
    payload: dict = Depends(require_role(["student", "teacher", "admin"])),
):
    return {
        "message": "Access granted",
        "user_id": payload.get("sub"),
        "role": payload.get("role"),
    }
