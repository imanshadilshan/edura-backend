import logging
import os

import httpx

logger = logging.getLogger(__name__)

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user_service:8002")


async def create_profile_internal(user_id: int, role: str, first_name: str, last_name: str) -> None:
    """
    Creates the matching user_service profile row right after auth_service
    creates a privileged (admin) account — the public register->login->
    createProfile flow doesn't apply here since this account is created by
    another admin, not by the person logging in as it. Best-effort: logged,
    not fatal, mirroring how Redis session creation is handled elsewhere —
    the admin account still exists and can complete its own profile later
    via the normal self-service endpoint if this call fails.
    """
    url = f"{USER_SERVICE_URL}/internal/create-profile"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                url,
                json={
                    "user_id": user_id,
                    "role": role,
                    "first_name": first_name,
                    "last_name": last_name,
                },
            )
        if r.status_code >= 400:
            logger.error(f"user_service rejected internal profile creation for {user_id}: {r.text}")
    except Exception as e:
        logger.error(f"Could not reach user_service to create profile for {user_id}: {e}")
