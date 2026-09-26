"""FastAPI 앱: CORS, 라우트, 예외 처리. 로직은 service.py, 저장은 store.py에 위임한다."""

import os
from functools import lru_cache

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from errors import AppError, NotFoundError
from models import (
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationDetail,
    ConversationMeta,
    DataCreate,
    DataItem,
    DataUpdate,
    Summary,
)
from service import ChatService, OpenAIChatClient, compute_summary
from store import FirestoreStore, create_firestore_client

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


# ---------- 의존성 (테스트에서 dependency_overrides로 교체) ----------


@lru_cache
def get_store() -> FirestoreStore:
    return FirestoreStore(create_firestore_client())


@lru_cache
def get_llm() -> OpenAIChatClient:
    return OpenAIChatClient()


def get_chat_service(store=Depends(get_store), llm=Depends(get_llm)) -> ChatService:
    return ChatService(store, llm)


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# ---------- 데이터 ----------


@app.get("/api/data/summary", response_model=Summary, tags=["data"])
def get_summary(store=Depends(get_store)):
    """데이터 요약 (채팅 시스템 프롬프트에 주입되는 값과 동일)."""
    summary = compute_summary(store.list_data())
    if summary is None:
        raise NotFoundError("요약할 데이터가 없습니다.")
    return summary


@app.get("/api/data", response_model=list[DataItem], tags=["data"])
def list_data(store=Depends(get_store)):
    return store.list_data()


@app.post("/api/data", response_model=DataItem, status_code=status.HTTP_201_CREATED, tags=["data"])
def create_data(item: DataCreate, store=Depends(get_store)):
    return store.create_data(item)


@app.put("/api/data/{data_id}", response_model=DataItem, tags=["data"])
def update_data(data_id: str, item: DataUpdate, store=Depends(get_store)):
    return store.update_data(data_id, item)


@app.delete("/api/data/{data_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["data"])
def delete_data(data_id: str, store=Depends(get_store)):
    store.delete_data(data_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 채팅 ----------


@app.post("/api/chat", response_model=ChatResponse, tags=["chat"])
def chat(req: ChatRequest, service: ChatService = Depends(get_chat_service)):
    """요약 조회 → 시스템 프롬프트 주입 → GPT 호출 → 대화 자동 저장."""
    conversation_id, reply = service.chat(req.message, req.conversation_id)
    return ChatResponse(conversation_id=conversation_id, reply=reply)


# ---------- 대화 기록 ----------


@app.post(
    "/api/conversations",
    response_model=ConversationMeta,
    status_code=status.HTTP_201_CREATED,
    tags=["conversations"],
)
def create_conversation(req: ConversationCreate, store=Depends(get_store)):
    title = req.title or req.messages[0].content[:30]
    conversation_id = store.create_conversation(title, req.messages)
    return store.get_conversation(conversation_id)


@app.get("/api/conversations", response_model=list[ConversationMeta], tags=["conversations"])
def list_conversations(limit: int = Query(50, ge=1, le=100), store=Depends(get_store)):
    """대화 목록. messages는 포함하지 않는다 (불러오기는 GET /api/conversations/{id})."""
    return store.list_conversations(limit)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail, tags=["conversations"])
def get_conversation(conversation_id: str, store=Depends(get_store)):
    return store.get_conversation(conversation_id)


@app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["conversations"])
def delete_conversation(conversation_id: str, store=Depends(get_store)):
    store.delete_conversation(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
