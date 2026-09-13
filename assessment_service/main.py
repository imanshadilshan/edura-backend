import logging

from database import Base, engine
from fastapi import FastAPI
from router import router

logger = logging.getLogger(__name__)

app = FastAPI(title="Assessment Service")


@app.on_event("startup")
def on_startup():
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Assessment service database tables verified.")
    except Exception as e:
        logger.error(f"Error initializing DB tables on startup: {e}")


# Registered before the router: /{assessment_id} is a plain wildcard path
# segment at the routing level, so it matches "health" too — whichever route
# is registered first wins that match, and its Depends() (auth) runs before
# FastAPI's own path-type validation ever gets a chance to 422 on "health".
@app.get("/health")
def health():
    return {"status": "ok", "service": "assessment_service"}


app.include_router(router)
