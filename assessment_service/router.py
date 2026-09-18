import json
import random
import uuid
from datetime import datetime, timezone

from course_client import get_course_instructor_id
from database import get_db
from events import publish_assessment_graded
from fastapi import APIRouter, Depends, HTTPException, Response, status
from models import (
    Assessment,
    AssessmentType,
    Question,
    QuestionType,
    Submission,
    SubmissionStatus,
    ViolationLog,
    ViolationType,
)
from redis_client import get_redis_client
from schemas import (
    AssessmentCreate,
    AssessmentResponse,
    AssessmentUpdate,
    QuestionAdminResponse,
    QuestionCreate,
    QuestionPublic,
    QuestionUpdate,
    StartSessionResponse,
    SubmitAssessmentRequest,
    SubmitAssessmentResponse,
    ViolationRequest,
)
from sqlalchemy.orm import Session

from shared.auth import get_optional_payload, require_role

router = APIRouter()

_ALL_ROLES = ["student", "teacher", "admin"]
_TEACHER_ADMIN = ["teacher", "admin"]


def _get_assessment_or_404(assessment_id: int, db: Session) -> Assessment:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        raise HTTPException(
            status_code=404,
            detail={"error": "ASSESSMENT_NOT_FOUND", "message": "Assessment not found"},
        )
    return assessment


def _get_question_or_404(question_id: int, db: Session) -> Question:
    question = db.query(Question).filter(Question.id == question_id).first()
    if not question:
        raise HTTPException(
            status_code=404,
            detail={"error": "QUESTION_NOT_FOUND", "message": "Question not found"},
        )
    return question


async def _assert_course_owner(course_id: int, requester_id: int, requester_role: str) -> None:
    if requester_role == "admin":
        return
    instructor_id = await get_course_instructor_id(course_id)
    if instructor_id is None:
        raise HTTPException(
            status_code=404, detail={"error": "COURSE_NOT_FOUND", "message": "Parent course not found"}
        )
    if instructor_id != requester_id:
        raise HTTPException(status_code=403, detail={"error": "NOT_COURSE_OWNER"})


def _question_to_admin_response(q: Question) -> QuestionAdminResponse:
    try:
        options = json.loads(q.options_json) if q.options_json else []
    except (ValueError, TypeError):
        options = []
    return QuestionAdminResponse(
        id=q.id,
        assessment_id=q.assessment_id,
        question_text=q.question_text,
        question_type=(
            q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type)
        ),
        options=options,
        correct_answer=q.correct_answer,
        marks=q.marks,
        position=q.position,
    )


# ---------------------------------------------------------------------------
# Assessment management endpoints (teacher/admin authoring)
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[AssessmentResponse])
async def list_assessments(
    course_id: int,
    payload: dict | None = Depends(get_optional_payload),
    db: Session = Depends(get_db),
):
    """List assessments for a course. Anonymous visitors and students only see published ones."""
    requester_role = payload.get("role", "").lower() if payload else ""
    q = db.query(Assessment).filter(Assessment.course_id == course_id)
    if not payload or requester_role == "student":
        q = q.filter(Assessment.is_published.is_(True))
    assessments = q.order_by(Assessment.id).all()
    return assessments


@router.post("/", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
async def create_assessment(
    body: AssessmentCreate,
    payload: dict = Depends(require_role(_TEACHER_ADMIN)),
    db: Session = Depends(get_db),
):
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()
    await _assert_course_owner(body.course_id, requester_id, requester_role)

    assessment = Assessment(
        course_id=body.course_id,
        lesson_id=body.lesson_id,
        title=body.title,
        description=body.description,
        assessment_type=AssessmentType(body.assessment_type),
        time_limit_minutes=body.time_limit_minutes,
        max_score=body.max_score,
        pass_score=body.pass_score,
        is_published=body.is_published,
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


@router.put("/{assessment_id}", response_model=AssessmentResponse)
async def update_assessment(
    assessment_id: int,
    body: AssessmentUpdate,
    payload: dict = Depends(require_role(_TEACHER_ADMIN)),
    db: Session = Depends(get_db),
):
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()
    assessment = _get_assessment_or_404(assessment_id, db)
    await _assert_course_owner(assessment.course_id, requester_id, requester_role)

    updates = body.model_dump(exclude_unset=True)
    if "assessment_type" in updates:
        updates["assessment_type"] = AssessmentType(updates["assessment_type"])
    for field, value in updates.items():
        setattr(assessment, field, value)

    db.commit()
    db.refresh(assessment)
    return assessment


@router.delete("/{assessment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assessment(
    assessment_id: int,
    payload: dict = Depends(require_role(_TEACHER_ADMIN)),
    db: Session = Depends(get_db),
):
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()
    assessment = _get_assessment_or_404(assessment_id, db)
    await _assert_course_owner(assessment.course_id, requester_id, requester_role)

    db.query(Question).filter(Question.assessment_id == assessment_id).delete()
    db.delete(assessment)
    db.commit()


# ---------------------------------------------------------------------------
# Question management endpoints (teacher/admin authoring)
# ---------------------------------------------------------------------------


@router.get("/{assessment_id}/questions", response_model=list[QuestionAdminResponse])
async def list_questions(
    assessment_id: int,
    payload: dict = Depends(require_role(_TEACHER_ADMIN)),
    db: Session = Depends(get_db),
):
    """Full question detail (including correct answers) for the owning teacher/admin."""
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()
    assessment = _get_assessment_or_404(assessment_id, db)
    await _assert_course_owner(assessment.course_id, requester_id, requester_role)

    questions = (
        db.query(Question)
        .filter(Question.assessment_id == assessment_id)
        .order_by(Question.position)
        .all()
    )
    return [_question_to_admin_response(q) for q in questions]


@router.post(
    "/{assessment_id}/questions",
    response_model=QuestionAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_question(
    assessment_id: int,
    body: QuestionCreate,
    payload: dict = Depends(require_role(_TEACHER_ADMIN)),
    db: Session = Depends(get_db),
):
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()
    assessment = _get_assessment_or_404(assessment_id, db)
    await _assert_course_owner(assessment.course_id, requester_id, requester_role)

    option_texts = [o.option_text for o in body.options]
    correct_option = next((o.option_text for o in body.options if o.is_correct), None)
    correct_answer = body.correct_answer or correct_option
    if not correct_answer:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "CORRECT_ANSWER_REQUIRED",
                "message": "Provide correct_answer, or mark one option as is_correct",
            },
        )

    question = Question(
        assessment_id=assessment_id,
        question_text=body.question_text,
        question_type=QuestionType(body.question_type),
        options_json=json.dumps(option_texts) if option_texts else None,
        correct_answer=correct_answer,
        marks=body.marks,
        position=body.position,
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return _question_to_admin_response(question)


@router.put("/questions/{question_id}", response_model=QuestionAdminResponse)
async def update_question(
    question_id: int,
    body: QuestionUpdate,
    payload: dict = Depends(require_role(_TEACHER_ADMIN)),
    db: Session = Depends(get_db),
):
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()
    question = _get_question_or_404(question_id, db)
    assessment = _get_assessment_or_404(question.assessment_id, db)
    await _assert_course_owner(assessment.course_id, requester_id, requester_role)

    updates = body.model_dump(exclude_unset=True)
    if "options" in updates:
        options = updates.pop("options") or []
        question.options_json = json.dumps([o["option_text"] for o in options]) if options else None
        correct_from_options = next((o["option_text"] for o in options if o["is_correct"]), None)
        if correct_from_options and "correct_answer" not in updates:
            question.correct_answer = correct_from_options
    if "question_type" in updates:
        updates["question_type"] = QuestionType(updates["question_type"])
    for field, value in updates.items():
        setattr(question, field, value)

    db.commit()
    db.refresh(question)
    return _question_to_admin_response(question)


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(
    question_id: int,
    payload: dict = Depends(require_role(_TEACHER_ADMIN)),
    db: Session = Depends(get_db),
):
    requester_id = int(payload["sub"])
    requester_role = payload.get("role", "").lower()
    question = _get_question_or_404(question_id, db)
    assessment = _get_assessment_or_404(question.assessment_id, db)
    await _assert_course_owner(assessment.course_id, requester_id, requester_role)

    db.delete(question)
    db.commit()


# ---------------------------------------------------------------------------
# Student-facing assessment endpoints
# ---------------------------------------------------------------------------


@router.get("/{assessment_id}", response_model=AssessmentResponse)
@router.get("/api/assessments/{assessment_id}", response_model=AssessmentResponse)
async def get_assessment(
    assessment_id: int,
    db: Session = Depends(get_db),
    _: dict = Depends(require_role(_ALL_ROLES)),
):
    """
    Get assessment metadata by ID.
    """
    return _get_assessment_or_404(assessment_id, db)


@router.post("/{assessment_id}/start", response_model=StartSessionResponse)
@router.post(
    "/api/assessments/{assessment_id}/start", response_model=StartSessionResponse
)
async def start_assessment_session(
    assessment_id: int,
    db: Session = Depends(get_db),
    payload: dict = Depends(require_role(_ALL_ROLES)),
):
    """
    Start a timed assessment session (AC1 & AC5).
    """
    student_id = int(payload["sub"])
    redis_c = get_redis_client()

    # Check duplicate active session (AC5)
    active_key = f"session:active:{student_id}:{assessment_id}"
    existing_session_id = redis_c.get(active_key)
    if existing_session_id:
        session_key = f"session:assessment:{existing_session_id}"
        remaining = redis_c.ttl(session_key)
        if remaining > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "SESSION_ALREADY_ACTIVE",
                    "session_id": existing_session_id,
                    "time_remaining_seconds": remaining,
                },
            )

    assessment = _get_assessment_or_404(assessment_id, db)

    # Fetch questions
    questions = (
        db.query(Question)
        .filter(Question.assessment_id == assessment_id)
        .order_by(Question.position.asc())
        .all()
    )

    # Create submission record
    submission = Submission(
        assessment_id=assessment_id,
        student_id=student_id,
        status=SubmissionStatus.in_progress,
        started_at=datetime.now(timezone.utc),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)

    # Generate session ID
    session_id = f"sess_{uuid.uuid4().hex}"
    time_limit_seconds = (
        (assessment.time_limit_minutes * 60) if assessment.time_limit_minutes else 3600
    )

    # Save session data to Redis
    session_payload = {
        "session_id": session_id,
        "submission_id": submission.id,
        "student_id": student_id,
        "assessment_id": assessment_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "time_limit_seconds": time_limit_seconds,
    }
    redis_c.set(
        f"session:assessment:{session_id}",
        json.dumps(session_payload),
        ex=time_limit_seconds,
    )
    redis_c.set(active_key, session_id, ex=time_limit_seconds)

    # Obfuscate questions (remove correct_answer) and shuffle
    shuffled_questions = list(questions)
    random.shuffle(shuffled_questions)

    public_questions = [
        QuestionPublic(
            id=q.id,
            question_text=q.question_text,
            question_type=(
                q.question_type.value
                if hasattr(q.question_type, "value")
                else str(q.question_type)
            ),
            options_json=q.options_json,
            marks=q.marks,
            position=q.position,
        )
        for q in shuffled_questions
    ]

    return StartSessionResponse(
        session_id=session_id,
        assessment_id=assessment_id,
        title=assessment.title,
        questions=public_questions,
        time_remaining_seconds=time_limit_seconds,
    )


@router.post("/{assessment_id}/submit", response_model=SubmitAssessmentResponse)
@router.post(
    "/api/assessments/{assessment_id}/submit", response_model=SubmitAssessmentResponse
)
async def submit_assessment(
    assessment_id: int,
    body: SubmitAssessmentRequest,
    db: Session = Depends(get_db),
    payload: dict = Depends(require_role(_ALL_ROLES)),
):
    """
    Submit assessment for auto-grading or process auto-submit on expiry (AC2 & AC3).
    """
    student_id = int(payload["sub"])
    redis_c = get_redis_client()

    session_key = f"session:assessment:{body.session_id}"
    active_key = f"session:active:{student_id}:{assessment_id}"
    raw_session_data = redis_c.get(session_key)

    auto_submitted = False
    submission_id = None

    if raw_session_data:
        session_data = json.loads(raw_session_data)
        submission_id = session_data.get("submission_id")
    else:
        # Session expired or invalid -> AC3 Auto-submit
        auto_submitted = True

    assessment = _get_assessment_or_404(assessment_id, db)
    questions = db.query(Question).filter(Question.assessment_id == assessment_id).all()

    answers_dict = {a.question_id: a.selected_option for a in body.answers}

    total_possible_marks = 0
    obtained_marks = 0
    correct_count = 0
    total_questions = len(questions)

    for q in questions:
        total_possible_marks += q.marks
        user_answer = answers_dict.get(q.id, "").strip()

        if q.question_type in (QuestionType.mcq, QuestionType.true_false):
            if (
                user_answer
                and user_answer.lower() == str(q.correct_answer).strip().lower()
            ):
                obtained_marks += q.marks
                correct_count += 1
        elif q.question_type == QuestionType.short_answer:
            # Short answer requires manual review per BR-002; 0 marks auto-graded
            pass

    score = (
        round((obtained_marks / total_possible_marks) * 100, 2)
        if total_possible_marks > 0
        else 0.0
    )
    passed = score >= assessment.pass_score

    # Update or create Submission DB record
    submission = None
    if submission_id:
        submission = db.query(Submission).filter(Submission.id == submission_id).first()

    if not submission:
        submission = (
            db.query(Submission)
            .filter(
                Submission.assessment_id == assessment_id,
                Submission.student_id == student_id,
                Submission.status == SubmissionStatus.in_progress,
            )
            .first()
        )

    if not submission:
        submission = Submission(
            assessment_id=assessment_id,
            student_id=student_id,
            started_at=datetime.now(timezone.utc),
        )
        db.add(submission)

    submission.status = SubmissionStatus.graded
    submission.score = score
    submission.answers_json = json.dumps([a.model_dump() for a in body.answers])
    submission.submitted_at = datetime.now(timezone.utc)
    db.commit()

    # Clear Redis keys
    redis_c.delete(session_key)
    redis_c.delete(active_key)

    # Publish RabbitMQ event
    publish_assessment_graded(
        student_id=student_id,
        assessment_id=assessment_id,
        score=score,
        passed=passed,
    )

    return SubmitAssessmentResponse(
        session_id=body.session_id,
        score=score,
        passed=passed,
        correct_count=correct_count,
        total_questions=total_questions,
        auto_submitted=auto_submitted,
    )


@router.post("/{assessment_id}/violations", status_code=status.HTTP_204_NO_CONTENT)
@router.post(
    "/api/assessments/{assessment_id}/violations",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def record_violation(
    assessment_id: int,
    body: ViolationRequest,
    db: Session = Depends(get_db),
    payload: dict = Depends(require_role(_ALL_ROLES)),
):
    """
    Record anti-cheat tab-switch violation (AC4).
    """
    student_id = int(payload["sub"])
    redis_c = get_redis_client()

    session_key = f"session:assessment:{body.session_id}"
    raw_session_data = redis_c.get(session_key)

    submission_id = None
    if raw_session_data:
        session_data = json.loads(raw_session_data)
        submission_id = session_data.get("submission_id")

    if not submission_id:
        sub_rec = (
            db.query(Submission)
            .filter(
                Submission.assessment_id == assessment_id,
                Submission.student_id == student_id,
            )
            .order_by(Submission.id.desc())
            .first()
        )
        if sub_rec:
            submission_id = sub_rec.id

    if not submission_id:
        # Create an in-progress submission fallback if none exists
        new_sub = Submission(
            assessment_id=assessment_id,
            student_id=student_id,
            status=SubmissionStatus.in_progress,
            started_at=datetime.now(timezone.utc),
        )
        db.add(new_sub)
        db.commit()
        db.refresh(new_sub)
        submission_id = new_sub.id

    # Parse violation_type
    v_type_str = body.violation_type.lower()
    if v_type_str in ("tab_switch", "tab-switch"):
        v_enum = ViolationType.tab_switch
    elif v_type_str in ("copy_paste", "copy-paste"):
        v_enum = ViolationType.copy_paste
    elif v_type_str in ("multiple_faces", "multiple-faces"):
        v_enum = ViolationType.multiple_faces
    elif v_type_str in ("no_face", "no-face"):
        v_enum = ViolationType.no_face
    else:
        v_enum = ViolationType.other

    violation_log = ViolationLog(
        submission_id=submission_id,
        student_id=student_id,
        violation_type=v_enum,
        detail=body.detail or f"Violation recorded: {body.violation_type}",
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(violation_log)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
