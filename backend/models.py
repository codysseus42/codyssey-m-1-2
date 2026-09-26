"""요청·응답 데이터 모델. 입력 검증은 모두 여기서 Pydantic이 맡는다."""

import math
import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------- 데이터 ----------


class DataBase(BaseModel):
    value: float = Field(description="월평균 불쾌지수", examples=[78.56])
    memo: str | None = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value는 유한한 숫자여야 합니다")
        return v


class DataCreate(DataBase):
    date: dt.date = Field(examples=["2026-08-01"])


class DataUpdate(DataBase):
    pass


class DataItem(DataCreate):
    id: str


# ---------- 요약 ----------


class Point(BaseModel):
    date: dt.date
    value: float


class Summary(BaseModel):
    period: str = Field(examples=["1961-01 ~ 2026-08"])
    count: int
    average: float
    maximum: Point
    minimum: Point
    latest: Point
    trend: Literal["상승", "하락", "유지", "데이터 부족"]
    trend_detail: str
    summer_by_decade: dict[str, float] = Field(
        description="여름(6~8월) 월평균 불쾌지수의 10년 단위 평균"
    )


# ---------- 대화 ----------


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class StoredMessage(Message):
    created_at: dt.datetime


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    messages: list[Message] = Field(min_length=1, max_length=200)


class ConversationMeta(BaseModel):
    id: str
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime
    message_count: int


class ConversationDetail(ConversationMeta):
    messages: list[StoredMessage]


# ---------- 채팅 ----------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("메시지가 비어 있습니다")
        return v.strip()


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
