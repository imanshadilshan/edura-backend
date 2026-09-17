import secrets
import string

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, status
from models import GradeSubject, ProfileRole, Stream, UserProfile
from schemas import (
    GradeSubjectCreate,
    GradeSubjectResponse,
    GradeSubjectUpdate,
    PaginatedUsersResponse,
    RoleUpdateRequest,
    StreamCreate,
    StreamResponse,
    StreamUpdate,
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
        school=profile.school,
        district=profile.district,
        grade=profile.grade,
        stream_id=profile.stream_id,
        nic_number=profile.nic_number,
        selected_subjects=profile.selected_subjects,
        referral_code=profile.referral_code,
        referred_by_user_id=profile.referred_by_user_id,
        created_at=profile.created_at,
    )


def _generate_referral_code(db: Session, first_name: str) -> str:
    """Short, human-shareable code like `JOHN4F2A` — collision-checked
    against the (unique) referral_code column with a handful of retries."""
    prefix = "".join(ch for ch in first_name.upper() if ch.isalpha())[:4] or "USER"
    for _ in range(10):
        suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        code = f"{prefix}{suffix}"
        if not db.query(UserProfile).filter(UserProfile.referral_code == code).first():
            return code
    return secrets.token_hex(6).upper()


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

    referred_by_user_id = None
    if body.referral_code:
        referrer = (
            db.query(UserProfile)
            .filter(UserProfile.referral_code == body.referral_code.strip().upper())
            .first()
        )
        if referrer:
            referred_by_user_id = referrer.user_id
        # An unrecognised code is ignored rather than blocking registration.

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
        school=body.school,
        district=body.district,
        grade=body.grade,
        stream_id=body.stream_id,
        nic_number=body.nic_number,
        selected_subjects=body.selected_subjects,
        referral_code=_generate_referral_code(db, body.first_name),
        referred_by_user_id=referred_by_user_id,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    email = payload.get("email")
    return _to_response(profile, email=email)


# ── Streams & Subjects reference data ────────────────────────────────────────
# Registered before the /{user_id} catch-all below — otherwise FastAPI/Starlette
# matches "/streams" and "/subjects" against that path first and fails int()
# conversion on the segment instead of falling through to these routes.
# Public reads; writes are admin-only.


@router.get("/streams", response_model=list[StreamResponse])
async def list_streams(active_only: bool = Query(True), db: Session = Depends(get_db)):
    q = db.query(Stream)
    if active_only:
        q = q.filter(Stream.is_active == True)  # noqa: E712
    return q.order_by(Stream.name).all()


@router.post("/streams", response_model=StreamResponse, status_code=status.HTTP_201_CREATED)
async def create_stream(
    body: StreamCreate,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    stream = Stream(**body.model_dump())
    db.add(stream)
    db.commit()
    db.refresh(stream)
    return stream


@router.put("/streams/{stream_id}", response_model=StreamResponse)
async def update_stream(
    stream_id: int,
    body: StreamUpdate,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    stream = db.query(Stream).filter(Stream.id == stream_id).first()
    if not stream:
        raise HTTPException(status_code=404, detail={"error": "STREAM_NOT_FOUND"})
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(stream, field, value)
    db.commit()
    db.refresh(stream)
    return stream


@router.delete("/streams/{stream_id}", status_code=204)
async def delete_stream(
    stream_id: int,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    stream = db.query(Stream).filter(Stream.id == stream_id).first()
    if not stream:
        raise HTTPException(status_code=404, detail={"error": "STREAM_NOT_FOUND"})
    db.delete(stream)
    db.commit()


@router.get("/grade-subjects", response_model=list[GradeSubjectResponse])
async def list_grade_subjects(
    grade: int | None = Query(None),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
):
    q = db.query(GradeSubject)
    if grade is not None:
        q = q.filter(GradeSubject.grade == grade)
    if active_only:
        q = q.filter(GradeSubject.is_active == True)  # noqa: E712
    return q.order_by(GradeSubject.grade, GradeSubject.name).all()


@router.post("/grade-subjects", response_model=GradeSubjectResponse, status_code=status.HTTP_201_CREATED)
async def create_grade_subject(
    body: GradeSubjectCreate,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    subject = GradeSubject(**body.model_dump())
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject


@router.put("/grade-subjects/{subject_id}", response_model=GradeSubjectResponse)
async def update_grade_subject(
    subject_id: int,
    body: GradeSubjectUpdate,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    subject = db.query(GradeSubject).filter(GradeSubject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail={"error": "SUBJECT_NOT_FOUND"})
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(subject, field, value)
    db.commit()
    db.refresh(subject)
    return subject


@router.delete("/grade-subjects/{subject_id}", status_code=204)
async def delete_grade_subject(
    subject_id: int,
    payload: dict = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    subject = db.query(GradeSubject).filter(GradeSubject.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail={"error": "SUBJECT_NOT_FOUND"})
    db.delete(subject)
    db.commit()


@router.get("/subjects", response_model=list[str])
async def get_subjects_for_grade(
    grade: int = Query(..., ge=5, le=13),
    stream_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Subjects available to pick from at registration/profile time.
    Grades 12-13 draw from the chosen stream's subject list; grades 5-11
    draw from the grade_subjects catalogue.
    """
    if grade in (12, 13):
        if stream_id is None:
            return []
        stream = db.query(Stream).filter(Stream.id == stream_id, Stream.is_active == True).first()  # noqa: E712
        return stream.subjects if stream else []

    rows = (
        db.query(GradeSubject)
        .filter(GradeSubject.grade == grade, GradeSubject.is_active == True)  # noqa: E712
        .order_by(GradeSubject.name)
        .all()
    )
    return [r.name for r in rows]


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
