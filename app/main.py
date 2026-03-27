from fastapi import FastAPI
from app.db.session import engine
from app.db.base import Base

from app.api.routes import auth

from fastapi.responses import JSONResponse
from fastapi.requests import Request
from fastapi.exceptions import RequestValidationError

app = FastAPI()

# Create tables
Base.metadata.create_all(bind=engine)

app.include_router(auth.router, prefix="/auth", tags=["Auth"])

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []

    for err in exc.errors():
        errors.append({
            "field": err["loc"][-1],
            "message": err["msg"]
        })

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid input",
                "details": errors
            }
        }
    )