import logging

from database import Base, engine
from fastapi import FastAPI
from router import router

logger = logging.getLogger(__name__)

app = FastAPI(title="User Service")


@app.on_event("startup")
def on_startup():
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("User service database tables verified.")
    except Exception as e:
        logger.error(f"Error initializing DB tables on startup: {e}")


@app.get("/health")
def health():
    return {"status": "ok", "service": "user_service"}


app.include_router(router, tags=["users"])
