"""FastAPI 앱 조립: 앱 생성, CORS, 예외 처리, 라우터 등록.

라우트는 routers/, 의존성은 deps.py, 로직은 service.py, 저장은 store.py, 검증은 models.py가 맡는다.
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from errors import AppError
from routers import chat, conversations, data

load_dotenv()

app = FastAPI(
    title="Summer Seoul Chat API",
    description="서울 월별 불쾌지수 데이터 CRUD · 요약 · 데이터 기반 AI 채팅",
    version="1.0.0",
)

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


app.include_router(data.router)
app.include_router(chat.router)
app.include_router(conversations.router)
