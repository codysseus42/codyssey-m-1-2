"""비즈니스 로직: 데이터 요약 계산과 채팅(컨텍스트 주입) 흐름."""

import os
from collections import defaultdict
from statistics import fmean

from errors import AIServiceError
from models import DataItem, Message, Point, Summary

TREND_THRESHOLD = 0.5  # 12개월 평균 차이가 이보다 작으면 '유지'
HISTORY_LIMIT = 10  # 모델에 함께 보낼 이전 메시지 수
TITLE_LENGTH = 30

# M1-1 분석(summer-seoul-data REPORT.md)의 결론. CRUD로 바뀌지 않는 고정 배경 지식이다.
ANALYSIS_NOTES = """\
- 서울 여름(6~8월) 평균기온 +0.36°C/10년, 일평균 불쾌지수 +0.39/10년 상승 (1961~2026)
- 같은 기간 서울 여름 평균 상대습도는 −1.52%p/10년으로 오히려 낮아졌다
- 불쾌지수 80 이상인 여름 날 비율: 1960년대 3.9% → 2020년대 19.1%
- 1990년대 기준으로는 불쾌지수 상승이 대부분 기온 몫이다 (습도 몫 거의 없음)
- 2010년대는 66년 중 가장 건조한 10년(여름 평균습도 68.7%)이었고, 2010년대를 기준으로 하면
  2020년대 낮 불쾌지수 상승의 76%가 습도 몫이다 → "요즘 더 습하다"는 체감은 직전 10년과 비교할 때 성립한다
- 불쾌지수 식에서 습도 10%p 변화 ≈ 기온 1°C 변화"""

SYSTEM_TEMPLATE = """\
당신은 서울 여름 체감 기후 데이터를 설명하는 분석 비서입니다.
사용자의 데이터는 기상청 서울(108) 관측소의 월평균 불쾌지수입니다.

[데이터 요약 — 현재 DB 기준]
- 기간: {period}
- 레코드: {count}개 (월 단위)
- 평균 불쾌지수: {average}
- 최고: {max_value} ({max_date}) / 최저: {min_value} ({min_date})
- 가장 최근: {latest_value} ({latest_date})
- 최근 추세: {trend} — {trend_detail}
- 여름 월평균 불쾌지수(10년 단위): {summer}

[분석 결론 — M1-1 리포트]
{notes}

규칙:
- 위 요약과 분석 결론에 있는 수치만 근거로 답하고, 없는 수치는 추측하지 말고 없다고 말하세요.
- 관찰(수치)과 해석(가능한 원인)을 구분해서 말하세요.
- 한국어로 3~6문장 이내로 답하세요."""


def compute_summary(records: list[DataItem]) -> Summary | None:
    """요약을 계산한다. 데이터가 없으면 None."""
    if not records:
        return None
    records = sorted(records, key=lambda r: r.date)
    values = [r.value for r in records]
    top = max(records, key=lambda r: r.value)
    bottom = min(records, key=lambda r: r.value)

    # 최근 12개월 vs 직전 12개월: 12개월 창이라 계절성이 서로 상쇄된다.
    if len(records) >= 24:
        recent = fmean(values[-12:])
        previous = fmean(values[-24:-12])
        diff = recent - previous
        trend = "상승" if diff > TREND_THRESHOLD else "하락" if diff < -TREND_THRESHOLD else "유지"
        detail = f"최근 12개월 평균 {recent:.2f} vs 직전 12개월 {previous:.2f} ({diff:+.2f})"
    else:
        trend, detail = "데이터 부족", "비교에 24개월 이상이 필요합니다"

    summer: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.date.month in (6, 7, 8):
            summer[f"{r.date.year // 10 * 10}s"].append(r.value)

    return Summary(
        period=f"{records[0].date:%Y-%m} ~ {records[-1].date:%Y-%m}",
        count=len(records),
        average=round(fmean(values), 2),
        maximum=Point(date=top.date, value=top.value),
        minimum=Point(date=bottom.date, value=bottom.value),
        latest=Point(date=records[-1].date, value=records[-1].value),
        trend=trend,
        trend_detail=detail,
        summer_by_decade={k: round(fmean(v), 2) for k, v in sorted(summer.items())},
    )


def build_system_prompt(summary: Summary | None) -> str:
    if summary is None:
        return "당신은 분석 비서입니다. 현재 저장된 데이터가 없으니, 데이터가 없다고 안내하세요."
    return SYSTEM_TEMPLATE.format(
        period=summary.period,
        count=summary.count,
        average=summary.average,
        max_value=summary.maximum.value,
        max_date=f"{summary.maximum.date:%Y-%m}",
        min_value=summary.minimum.value,
        min_date=f"{summary.minimum.date:%Y-%m}",
        latest_value=summary.latest.value,
        latest_date=f"{summary.latest.date:%Y-%m}",
        trend=summary.trend,
        trend_detail=summary.trend_detail,
        summer=", ".join(f"{k} {v}" for k, v in summary.summer_by_decade.items()),
        notes=ANALYSIS_NOTES,
    )


class OpenAIChatClient:
    """OpenAI SDK 호출. OPENAI_BASE_URL을 주면 OpenAI 호환 프록시(copa)로 보낸다."""

    def __init__(self):
        from openai import OpenAI

        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            timeout=60,
        )
        self.model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
        # gpt-5 계열은 max_tokens 대신 max_completion_tokens를 쓰고, 추론 토큰도 여기 포함된다.
        self.max_tokens = int(os.getenv("OPENAI_MAX_COMPLETION_TOKENS", "2000"))

    def complete(self, messages: list[dict]) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_completion_tokens=self.max_tokens,
            )
        except Exception as exc:
            raise AIServiceError() from exc
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise AIServiceError("AI가 빈 응답을 반환했습니다. 잠시 후 다시 시도해 주세요.")
        return content


class ChatService:
    def __init__(self, store, llm):
        self.store = store
        self.llm = llm

    def chat(self, message: str, conversation_id: str | None) -> tuple[str, str]:
        # 1. 대화 이력 (이어서 묻는 경우)
        history = []
        if conversation_id:
            conv = self.store.get_conversation(conversation_id)
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in conv["messages"][-HISTORY_LIMIT:]
            ]

        # 2. 데이터 요약 조회 → 3. 시스템 프롬프트에 주입
        summary = compute_summary(self.store.list_data())
        messages = [
            {"role": "system", "content": build_system_prompt(summary)},
            *history,
            {"role": "user", "content": message},
        ]

        # 4. GPT 호출
        reply = self.llm.complete(messages)

        # 5. 대화 자동 저장
        pair = [Message(role="user", content=message), Message(role="assistant", content=reply)]
        if conversation_id:
            self.store.append_messages(conversation_id, pair)
        else:
            conversation_id = self.store.create_conversation(message[:TITLE_LENGTH], pair)
        return conversation_id, reply
