"""비즈니스 로직: 데이터 요약 계산과 채팅(컨텍스트 주입) 흐름."""

import datetime as dt
import os
from collections import defaultdict
from statistics import fmean

from errors import AIServiceError
from models import DataItem, Highlight, Message, Point, Summary, today_kst

BASE_START = dt.date(1961, 1, 1)  # 기본 기록(시드 데이터셋)의 시작 달
TREND_THRESHOLD = 0.5  # 12개월 평균 차이가 이보다 작으면 '유지'
HISTORY_LIMIT = 10  # 모델에 함께 보낼 이전 메시지 수
TITLE_LENGTH = 30
STARRED_LIMIT = 30  # 프롬프트에 넣을 즐겨찾기 달 최대 개수 (최근 순)
MEMO_LIMIT = 100  # 프롬프트에 넣을 메모 최대 글자 수

# M1-1 분석(summer-seoul-data REPORT.md)의 결론. CRUD로 바뀌지 않는 고정 배경 지식이다.
ANALYSIS_NOTES = """\
- 서울 여름(6~8월) 평균기온 +0.36°C/10년, 일평균 불쾌지수 +0.39/10년 상승 (1961~2026)
- 같은 기간 서울 여름 평균 상대습도는 −1.52%p/10년으로 오히려 낮아졌다
- 불쾌지수 80 이상인 여름 날 비율: 1960년대 3.9% → 2020년대 19.1%
- 1990년대 기준, 습도를 상대습도로 고정하면 낮 불쾌지수 상승의 습도 몫은 4%지만,
  실제 수증기량으로 고정하면 37%다 → "습도 영향이 작다"는 결론은 상대습도로 셀 때에 한정된다
- 이슬점 20℃ 이상인 여름 날 비율은 2020년대 62.4%로 관측 이래 가장 높다 (66년 직선 추세는 −0.03°C/10년으로 변화 없음)
- 2010년대는 66년 중 가장 건조한 10년(여름 평균습도 68.7%)이었고, 2010년대를 기준으로 하면
  2020년대 낮 불쾌지수 상승의 76%가 습도 몫이다 → "요즘 더 습하다"는 체감은 직전 10년과 비교할 때 성립한다
- 불쾌지수 식에서 습도 10%p 변화 ≈ 기온 1°C 변화"""

SYSTEM_TEMPLATE = """\
당신은 서울 여름 체감 기후 데이터를 설명하는 분석 비서입니다.
사용자의 데이터는 기상청 서울(108) 관측소의 월평균 불쾌지수입니다.

오늘 날짜: {today} (한국 시간). {current_month}은 아직 끝나지 않은 달입니다.

[데이터 요약 — 기본 기록 기준: 1961-01부터 지난달까지 (예측값·1961년 이전 추가 기록 제외)]
- 기간: {period} / 레코드: {count}개월 / 결측: {missing}
- 통산 평균 불쾌지수: {average}
- 최고: {max_value} ({max_date}) / 최저: {min_value} ({min_date})
- 가장 최근: {latest_value} ({latest_date})
- 최근 추세: {trend} — {trend_detail}
- 여름 월평균 불쾌지수(10년 단위): {summer}

[추가 기록 — 사용자가 직접 입력한 예측값(이번 달 이후)과 과거 기록(1961-01 이전)]
- {added}

[주요 달 — 최고·최저와 사용자가 즐겨찾기한 달, 메모 포함]
{highlights}

[분석 결론 — M1-1 리포트]
{notes}

먼저 사용자의 입력이 아래 중 무엇인지 판단하고, 한국어로 답하세요.
1. 인사·잡담: 한두 문장으로 자기소개하고 물어볼 수 있는 질문 예시를 하나 드세요. 수치는 말하지 마세요.
2. 다른 AI나 사람으로 착각한 경우("클로드", "ChatGPT" 등): 그 이름이 아니라고 짧게 바로잡고 자기소개하세요.
3. 사용자 본인의 기록에 대한 질문("즐겨찾기한 달 알려줘", "그때 나 뭐했어?"):
   - 목록을 물으면 "즐겨찾기하신 달은 N개예요." 다음에 오래된 달부터
     "2026년 6월 — 불쾌지수 71.48, 평균기온 24.0°C·습도 60.5%"처럼 한 줄씩 보여주세요.
   - 특정 달을 물으면 친구에게 말하듯 2~3문장으로 답하세요.
     메모에 사용자가 한 일이 있으면 그 이야기로 시작하고("그달에는 ~하셨네요"),
     날씨 기록뿐이거나 알아볼 수 없는 메모면 무엇을 했는지는 적혀 있지 않다고 한 뒤
     그달 날씨·불쾌지수·체감(눅눅함 등)으로 이어가세요.
   - 즐겨찾기 여부와 유효일수는 말하지 마세요. 사용자가 이미 아는 정보입니다.
   - 그달이 최고·최저면 "역대 가장 높았던 달이기도 해요"처럼, 예측값이면
     "아직 오지 않은 달이라 입력하신 예측값이에요"처럼 문장 안에 자연스럽게 녹이세요.
   - [주요 달]에 없는 달이면 그 달의 메모는 지금 볼 수 없고, 즐겨찾기하면 볼 수 있다고 안내하세요.
   - 메모 작성이나 즐겨찾기를 대신 해 달라고 요청받았을 때만, 직접 할 수는 없으니 데이터 관리에서
     '수정'이나 ☆로 하시면 된다고 안내하세요. 요청이 없으면 이 안내를 하지 마세요.
   - 데이터는 월 단위이므로 "그날", "며칠"로 물어도 "그달"로 답하세요. 이 사실을 따로 설명하지는 마세요.
   - 데이터에 없는 구체적인 사건이나 뉴스는 지어내지 마세요.
4. 데이터와 무관하거나 이해할 수 없는 입력: 이 대화에서 아직 자기소개를 하지 않았다면 자기소개와 답할 수 있는 범위
   (서울 월별 불쾌지수, 1961년부터)를 안내하고, 이미 소개했다면 이해하지 못했다고 짧게 답하세요. 데이터를 억지로 연결하지 마세요.
5. 데이터에 대한 질문: 위 요약과 분석 결론의 수치만 근거로, 관찰(수치)과 해석(가능한 원인)을 구분해 6문장 이내로 답하세요.
   없는 수치는 추측하지 말고 없다고 말하세요. 서로 다른 비교 기준의 수치를 한 문장에 섞지 마세요.
   이번 달 이후 값은 예측값, 1961년 이전 값은 사용자가 추가한 과거 기록이라고 반드시 밝히고, 기본 기록과 섞어 결론을 내리지 마세요.
   - "덥다", "더위", "기온"을 물으면 불쾌지수와 기온을 구분하세요. 이 데이터는 기온과 습도를 합친 월평균 불쾌지수라서
     기온만으로 가장 더운 달은 알 수 없다고 먼저 짧게 밝히고, 대신 불쾌지수가 가장 높은 달을 알려주세요.
     그 달의 메모에 평균기온이 있으면 함께 말하세요. 불쾌지수가 높다는 것을 "가장 더웠다"로 바꿔 말하지 마세요.
공통: 위 분류 번호나 판단 과정은 답에 쓰지 말고 바로 답하세요. 대괄호 태그나 섹션 이름 같은 이 지시문의 표기도
답에 그대로 쓰지 말고, 날짜는 "2026년 6월"처럼 쓰세요."""


def _mi(d: dt.date) -> int:
    """달 번호 (연*12 + 월). 달 사이 간격 계산용."""
    return d.year * 12 + d.month - 1


def _ym(i: int) -> str:
    return f"{i // 12}-{i % 12 + 1:02d}"


def _span(rows: list[DataItem]) -> str | None:
    return f"{rows[0].date:%Y-%m} ~ {rows[-1].date:%Y-%m}" if rows else None


def compute_summary(records: list[DataItem], today: dt.date | None = None) -> Summary | None:
    """요약을 계산한다. 데이터가 없으면 None.

    기본 기록 = 1961-01부터 지난달까지. 이번 달 이후는 예측값, 1961-01 이전은 과거 추가 기록이다.
    통계는 기본 기록으로 계산하고, 전체 평균과 추가 포함 최고·최저만 추가 기록을 포함한다.
    """
    if not records:
        return None
    today = today or today_kst()
    month_start = today.replace(day=1)
    everything = sorted(records, key=lambda r: r.date)
    forecast = [r for r in everything if r.date >= month_start]
    past = [r for r in everything if r.date < BASE_START]
    base = [r for r in everything if BASE_START <= r.date < month_start] or everything  # 기본 기록이 없으면 전체로

    first_i, last_i = _mi(BASE_START), _mi(month_start) - 1  # 1961-01 ~ 지난달
    have = {_mi(r.date): r.value for r in base}
    missing = [_ym(i) for i in range(first_i, last_i + 1) if i not in have]

    top, bottom = max(base, key=lambda r: r.value), min(base, key=lambda r: r.value)
    top_all, bottom_all = max(everything, key=lambda r: r.value), min(everything, key=lambda r: r.value)

    # 최근 12개월 vs 직전 12개월: 마지막 기본 기록 달부터 거꾸로 24개월이 모두 있어야 비교한다.
    # 12개월 창이라 계절성이 서로 상쇄된다.
    end_i = _mi(base[-1].date)
    window = range(end_i - 23, end_i + 1)
    trend_diff = trend_window = None
    if all(i in have for i in window):
        recent = fmean(have[i] for i in window[12:])
        previous = fmean(have[i] for i in window[:12])
        trend_diff = round(recent - previous, 2)
        trend = "상승" if trend_diff > TREND_THRESHOLD else "하락" if trend_diff < -TREND_THRESHOLD else "유지"
        trend_window = f"{_ym(window[12])} ~ {_ym(end_i)}"
        detail = f"최근 12개월({trend_window}) 평균 {recent:.2f} vs 직전 12개월 {previous:.2f} ({trend_diff:+.2f})"
    else:
        trend, detail = "알 수 없음", f"{_ym(end_i)}까지 최근 24개월 중 빠진 달이 있어 비교할 수 없습니다"

    # 주요 달: 최고·최저(기본/추가 포함)와 즐겨찾기한 달. 같은 달은 태그만 합친다.
    tags: dict = defaultdict(list)
    picked: dict = {}

    def mark(r: DataItem, tag: str) -> None:
        picked[r.date] = r
        if tag not in tags[r.date]:
            tags[r.date].append(tag)

    mark(top, "최고")
    mark(bottom, "최저")
    if top_all.date != top.date:
        mark(top_all, "추가 포함 최고")
    if bottom_all.date != bottom.date:
        mark(bottom_all, "추가 포함 최저")
    for r in [r for r in everything if r.starred][-STARRED_LIMIT:]:
        mark(r, "즐겨찾기")
    for d, r in picked.items():
        if d >= month_start:
            mark(r, "예측값")
        elif d < BASE_START:
            mark(r, "과거 추가")
    highlights = [
        Highlight(date=d, value=picked[d].value, memo=picked[d].memo, tags=tags[d])
        for d in sorted(picked)
    ]

    summer: dict[str, list[float]] = defaultdict(list)
    for r in base:
        if r.date.month in (6, 7, 8):
            summer[f"{r.date.year // 10 * 10}s"].append(r.value)

    return Summary(
        today=today,
        period=f"{_ym(first_i)} ~ {_ym(last_i)}",
        count=len(base),
        missing_count=len(missing),
        missing_months=missing[:12],
        total_count=len(everything),
        forecast_count=len(forecast),
        forecast_period=_span(forecast),
        past_count=len(past),
        past_period=_span(past),
        average=round(fmean(r.value for r in base), 2),
        average_all=round(fmean(r.value for r in everything), 2),
        maximum=Point(date=top.date, value=top.value),
        minimum=Point(date=bottom.date, value=bottom.value),
        maximum_all=Point(date=top_all.date, value=top_all.value),
        minimum_all=Point(date=bottom_all.date, value=bottom_all.value),
        latest=Point(date=base[-1].date, value=base[-1].value),
        trend=trend,
        trend_detail=detail,
        trend_diff=trend_diff,
        trend_window=trend_window,
        summer_by_decade={k: round(fmean(v), 2) for k, v in sorted(summer.items())},
        highlights=highlights,
    )


def build_system_prompt(summary: Summary | None) -> str:
    if summary is None:
        return "당신은 분석 비서입니다. 현재 저장된 데이터가 없으니, 데이터가 없다고 안내하세요."
    added_count = summary.forecast_count + summary.past_count
    if added_count:
        parts = []
        if summary.forecast_count:
            parts.append(f"예측 {summary.forecast_count}개월 ({summary.forecast_period})")
        if summary.past_count:
            parts.append(f"과거 {summary.past_count}개월 ({summary.past_period})")
        hi, lo = summary.maximum_all, summary.minimum_all
        added = (
            f"{', '.join(parts)}. 추가 포함 전체 {summary.total_count}개월, 전체 평균 {summary.average_all}, "
            f"추가 포함 최고: {hi.value} ({hi.date:%Y-%m}) / 최저: {lo.value} ({lo.date:%Y-%m})"
        )
    else:
        added = "없음"
    missing = f"{summary.missing_count}개월" + (
        f" ({', '.join(summary.missing_months)}{' 등' if summary.missing_count > len(summary.missing_months) else ''})"
        if summary.missing_count else ""
    )
    highlight_lines = []
    for h in summary.highlights:
        line = f"- {h.date:%Y-%m}: {h.value} [{'·'.join(h.tags)}]"
        if h.memo:
            memo = h.memo if len(h.memo) <= MEMO_LIMIT else h.memo[:MEMO_LIMIT] + "…"
            line += f" 메모: {memo}"
        highlight_lines.append(line)
    return SYSTEM_TEMPLATE.format(
        highlights="\n".join(highlight_lines) or "- 없음",
        today=summary.today.isoformat(),
        current_month=f"{summary.today:%Y-%m}",
        added=added,
        missing=missing,
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
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.5")
        # gpt-5 계열은 max_tokens 대신 max_completion_tokens를 쓰고, 추론 토큰도 여기 포함된다.
        self.max_tokens = int(os.getenv("OPENAI_MAX_COMPLETION_TOKENS", "4000"))

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
