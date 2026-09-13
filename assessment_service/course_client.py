import os
from typing import Optional

import httpx

COURSE_SERVICE_URL = os.getenv("COURSE_SERVICE_URL", "http://course_service:8003")


async def get_course_instructor_id(course_id: int) -> Optional[int]:
    """Looks up the instructor_id that owns a course, via course_service's
    open internal /owner endpoint (mirrors content_service's enrollment_client
    pattern for cross-service ownership checks)."""
    url = f"{COURSE_SERVICE_URL}/{course_id}/owner"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return None
        return r.json().get("instructor_id")
    except Exception:
        return None
