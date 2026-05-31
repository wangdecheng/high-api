import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from app.config import settings

logger = logging.getLogger("high-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connection
    yield
    # Shutdown: clean up connections
    from app.database import engine
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Custom Exception ---

class AppException(Exception):
    """Custom application exception with error code."""
    def __init__(self, status_code: int, error: str, code: str):
        self.status_code = status_code
        self.error = error
        self.code = code


# --- Exception Handlers (uniform error format) ---

def _extract_first_error(exc: RequestValidationError) -> str:
    """Extract the first user-facing error message from validation errors."""
    for error in exc.errors():
        msg = error.get("msg", "")
        # Pydantic returns "Value error, ..." for field_validator errors
        if "Value error, " in msg:
            return msg.split("Value error, ", 1)[1]
        if error.get("type") == "missing":
            field = error.get("loc", [])[-1] if error.get("loc") else "field"
            return f"{field} 是必填项"
    return "请求参数校验失败"


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "code": exc.code},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": _extract_first_error(exc), "code": "VALIDATION_ERROR"},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "code": "INTERNAL_ERROR"},
    )


# --- Routers ---

from app.routers import auth, api_keys, proxy, usage, models  # noqa: E402

app.include_router(auth.router)
app.include_router(api_keys.router)
app.include_router(proxy.router)
app.include_router(usage.router)
app.include_router(models.router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
