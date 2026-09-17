from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, status
from models import ProfileRole, UserProfile
from schemas import (
    PaginatedUsersResponse,
    RoleUpdateRequest,
    UserProfileCreate,
    UserProfileResponse,
    UserProfileUpdate,
)
from sqlalchemy.orm import Session

from shared.auth import require_role

router = APIRouter()

_ALL_ROLES = ["student", "teacher", "admin"]


def _to_response(profile: UserProfile, email: str | None = None) -> UserProfileResponse:
    return UserProfileResponse(
        id=profile.user_id,
        name=f"{profile.first_name} {profile.last_name}",
        email=email,
        role=profile.role.value,
        first_name=profile.first_name,
        last_name=profile.last_name,
        mobile_no=profile.mobile_no,
        date_of_birth=profile.date_of_birth,
        bio=profile.bio,
        avatar_url=profile.avatar_url,
        avatar_public_id=profile.avatar_public_id,
        created_at=profile.created_at,
    )


@router.get("/", response_model=PaginatedUsersResponse)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Admin: paginated list of all user profiles."""
    offset = (page - 1) * page_size
    total = db.query(UserProfile).count()
    profiles = db.query(UserProfile).offset(offset).limit(page_size).all()
    return PaginatedUsersResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[_to_response(p) for p in profiles],
    )


@router.post("/", response_model=UserProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_own_profile(
    body: UserProfileCreate,
    payload: dict = Depends(require_role(_ALL_ROLES)),
    db: Session = Depends(get_db),
):
    """
    Create the profile for the currently authenticated user (self-service).
    auth_service creates the login record on /register but has no way to
    populate name/contact fields — the client calls this right after its
    first login to finish setting up the account. Role is taken from the
    JWT (assigned at registration), never from the request body.
    """
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()

    existing = db.query(UserProfile).filter(UserProfile.user_id == requester_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "PROFILE_EXISTS", "message": "Profile already exists for this user"},
        )

    profile = UserProfile(
        user_id=requester_id,
        role=ProfileRole(requester_role),
        first_name=body.first_name,
        last_name=body.last_name,
        mobile_no=body.mobile_no,
        date_of_birth=body.date_of_birth,
        bio=body.bio,
        avatar_url=body.avatar_url,
        avatar_public_id=body.avatar_public_id,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    email = payload.get("email")
    return _to_response(profile, email=email)


@router.get("/{user_id}", response_model=UserProfileResponse)
async def get_user(
    user_id: int,
    payload: dict = Depends(require_role(_ALL_ROLES)),
    db: Session = Depends(get_db),
):
    """Get a user profile. Own record always allowed; other records require admin."""
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "")

    if requester_id != user_id and requester_role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "INSUFFICIENT_PERMISSIONS"},
        )

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail={"error": "USER_NOT_FOUND"})

    # Include email from JWT when the requester is viewing their own record
    email = payload.get("email") if requester_id == user_id else None
    return _to_response(profile, email=email)


@router.put("/{user_id}", response_model=UserProfileResponse)
async def update_user(
    user_id: int,
    body: UserProfileUpdate,
    payload: dict = Depends(require_role(_ALL_ROLES)),
    db: Session = Depends(get_db),
):
    """Update a user profile. Own record allowed; other records require admin."""
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "")

    if requester_id != user_id and requester_role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "INSUFFICIENT_PERMISSIONS"},
        )

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail={"error": "USER_NOT_FOUND"})

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return _to_response(profile)


@router.put("/{user_id}/role", response_model=UserProfileResponse)
async def update_user_role(
    user_id: int,
    body: RoleUpdateRequest,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Admin only: assign a new role to a user."""
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail={"error": "USER_NOT_FOUND"})

    profile.role = ProfileRole(body.role)
    db.commit()
    db.refresh(profile)
    return _to_response(profile)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Admin only: soft-delete a user profile. Auth service is notified via RabbitMQ (integration epic)."""
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail={"error": "USER_NOT_FOUND"})

    db.delete(profile)
    db.commit()
