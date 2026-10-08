"""요청·응답 데이터 모델. 입력 검증은 모두 여기서 Pydantic이 맡는다."""

import math
import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator


# ---------- 데이터 ----------

FORECAST_MONTHS = 6  # 현재 달을 포함해 앞으로 6개월까지 (예측값) 입력 허용


def today_kst() -> dt.date:
    return dt.datetime.now(ZoneInfo("Asia/Seoul")).date()


def latest_allowed_month(today: dt.date) -> dt.date:
    """현재 달을 포함해 FORECAST_MONTHS번째 달의 1일."""
    index = today.year * 12 + today.month - 1 + FORECAST_MONTHS - 1
    return dt.date(index // 12, index % 12 + 1, 1)


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
    date: dt.date = Field(examples=["2026-08-01"], description="매달 1일 (YYYY-MM-01)")
    starred: bool = Field(default=False, description="즐겨찾기. 즐겨찾기한 달은 메모와 함께 AI 프롬프트에 들어간다")

    @field_validator("date")
    @classmethod
    def date_must_be_valid_month(cls, v: dt.date) -> dt.date:
        if v.day != 1:
            raise ValueError("date는 매달 1일(YYYY-MM-01)이어야 합니다")
        limit = latest_allowed_month(today_kst())
        if v > limit:
            raise ValueError(f"현재 달을 포함해 6개월({limit:%Y-%m})까지만 입력할 수 있습니다")
        return v


class DataUpdate(DataBase):
    starred: bool | None = Field(default=None, description="보내지 않으면(null) 기존 즐겨찾기를 유지한다")


class DataItem(DataCreate):
    id: str


# ---------- 요약 ----------


class Point(BaseModel):
    date: dt.date
    value: float


class Highlight(BaseModel):
    """프롬프트에 메모와 함께 넣는 주요 달. 같은 달이 여러 이유로 뽑히면 tags에 모두 붙는다."""

    date: dt.date
    value: float
    memo: str | None = None
    tags: list[str] = Field(examples=[["최고", "즐겨찾기"]])


class Summary(BaseModel):
    """기본 기록 = 1961-01부터 지난달까지. 예측값(이번 달 이후)과 과거 추가 기록(1961-01 이전)은
    사용자가 직접 넣은 '추가 기록'으로 따로 센다. 통계는 기본 기록 기준이고, *_all만 추가 기록을 포함한다."""

    today: dt.date = Field(description="요약 기준일 (한국 시간)")
    period: str = Field(description="기본 기록 범위", examples=["1961-01 ~ 2026-09"])
    count: int = Field(description="기본 기록 개수")
    missing_count: int = Field(description="기본 범위 안에서 기록이 없는 달 수")
    missing_months: list[str] = Field(default_factory=list, description="결측 달 (앞에서 12개까지)")
    total_count: int = Field(description="추가 기록 포함 전체 개수")
    forecast_count: int = Field(description="예측값 개수 (이번 달 이후)")
    forecast_period: str | None = Field(default=None, examples=["2026-10 ~ 2026-12"])
    past_count: int = Field(description="과거 추가 기록 개수 (1961-01 이전)")
    past_period: str | None = Field(default=None, examples=["1955-07 ~ 1955-08"])
    average: float = Field(description="통산 평균 (기본 기록)")
    average_all: float = Field(description="전체 평균 (추가 기록 포함)")
    maximum: Point = Field(description="최고 (기본 기록)")
    minimum: Point = Field(description="최저 (기본 기록)")
    maximum_all: Point = Field(description="최고 (추가 기록 포함)")
    minimum_all: Point = Field(description="최저 (추가 기록 포함)")
    latest: Point = Field(description="가장 최근 기본 기록")
    trend: Literal["상승", "하락", "유지", "알 수 없음"]
    trend_detail: str
    trend_diff: float | None = Field(default=None, description="최근 12개월 평균 − 직전 12개월 평균")
    trend_window: str | None = Field(default=None, examples=["2025-09 ~ 2026-08"])
    summer_by_decade: dict[str, float] = Field(
        description="여름(6~8월) 월평균 불쾌지수의 10년 단위 평균 (기본 기록)"
    )
    highlights: list[Highlight] = Field(default_factory=list, description="최고·최저·즐겨찾기한 달 (날짜순, 중복은 한 줄)")


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
