"""Firebase·OpenAI 없이 전체 API 흐름을 검증한다. 실행: backend/ 에서 `pytest -q`"""

import sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps
import main  # noqa: E402
from errors import AIServiceError, ConflictError, NotFoundError  # noqa: E402
from models import DataItem, today_kst  # noqa: E402
from service import build_system_prompt, compute_summary  # noqa: E402


class FakeStore:
    """FirestoreStore와 같은 메서드를 가진 메모리 구현 (테스트 전용)."""

    def __init__(self):
        self.data, self.convs, self.seq = {}, {}, 0

    def list_data(self):
        return [DataItem(id=k, **v) for k, v in sorted(self.data.items())]

    def create_data(self, item):
        key = item.date.isoformat()
        if key in self.data:
            raise ConflictError()
        self.data[key] = {"date": key, "value": item.value, "memo": item.memo, "starred": item.starred}
        return DataItem(id=key, **self.data[key])

    def update_data(self, key, item):
        if key not in self.data:
            raise NotFoundError()
        self.data[key].update(value=item.value, memo=item.memo)
        if item.starred is not None:
            self.data[key]["starred"] = item.starred
        return DataItem(id=key, **self.data[key])

    def delete_data(self, key):
        if self.data.pop(key, None) is None:
            raise NotFoundError()

    def create_conversation(self, title, messages):
        self.seq += 1
        now = datetime.now(timezone.utc)
        cid = f"c{self.seq}"
        self.convs[cid] = {
            "title": title, "created_at": now, "updated_at": now,
            "messages": [{**m.model_dump(), "created_at": now} for m in messages],
        }
        self.convs[cid]["message_count"] = len(messages)
        return cid

    def append_messages(self, cid, messages):
        conv = self.get_conversation(cid)
        now = datetime.now(timezone.utc)
        conv["messages"] += [{**m.model_dump(), "created_at": now} for m in messages]
        self.convs[cid].update(messages=conv["messages"], message_count=len(conv["messages"]), updated_at=now)

    def list_conversations(self, limit):
        items = sorted(self.convs.items(), key=lambda kv: kv[1]["updated_at"], reverse=True)[:limit]
        return [{"id": k, **{f: v[f] for f in ("title", "created_at", "updated_at", "message_count")}} for k, v in items]

    def get_conversation(self, cid):
        if cid not in self.convs:
            raise NotFoundError()
        return {"id": cid, **self.convs[cid]}

    def delete_conversation(self, cid):
        if self.convs.pop(cid, None) is None:
            raise NotFoundError()


class FakeLLM:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def complete(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise AIServiceError()
        return "테스트 답변"


def seed(store, months=30):
    for i in range(months):
        y, m = 2024 + (i // 12), i % 12 + 1
        store.data[f"{y}-{m:02d}-01"] = {"date": f"{y}-{m:02d}-01", "value": 50.0 + i, "memo": None}


@pytest.fixture
def ctx():
    store, llm = FakeStore(), FakeLLM()
    # 키는 deps의 함수 객체여야 한다 (라우터들이 deps에서 가져다 쓰므로)
    main.app.dependency_overrides[deps.get_store] = lambda: store
    main.app.dependency_overrides[deps.get_llm] = lambda: llm
    yield TestClient(main.app), store, llm
    main.app.dependency_overrides.clear()


# ---------- 데이터 CRUD ----------


def test_data_crud_roundtrip(ctx):
    client, _, _ = ctx
    r = client.post("/api/data", json={"date": "2026-08-01", "value": 78.56, "memo": "폭염"})
    assert r.status_code == 201 and r.json()["id"] == "2026-08-01"

    assert client.post("/api/data", json={"date": "2026-08-01", "value": 1}).status_code == 409

    r = client.put("/api/data/2026-08-01", json={"value": 79.0, "memo": None})
    assert r.status_code == 200 and r.json()["value"] == 79.0

    assert [d["value"] for d in client.get("/api/data").json()] == [79.0]
    assert client.delete("/api/data/2026-08-01").status_code == 204
    assert client.delete("/api/data/2026-08-01").status_code == 404
    assert client.put("/api/data/2026-08-01", json={"value": 1}).status_code == 404


def test_date_rules(ctx, monkeypatch):
    client, _, _ = ctx
    monkeypatch.setattr("models.today_kst", lambda: date(2026, 10, 5))
    post = lambda d: client.post("/api/data", json={"date": d, "value": 70}).status_code
    assert post("2026-09-15") == 422       # 월 첫날이 아님
    assert post("2027-03-01") == 201       # 현재 달 포함 6개월째 → 허용
    assert post("2027-04-01") == 422       # 7개월째 → 거절
    assert post("1961-01-01") == 201       # 과거는 제한 없음


def test_latest_allowed_month_crosses_year():
    from models import latest_allowed_month
    assert latest_allowed_month(date(2026, 10, 5)) == date(2027, 3, 1)
    assert latest_allowed_month(date(2026, 7, 31)) == date(2026, 12, 1)


@pytest.mark.parametrize("body", [
    {"value": 1},                                   # date 누락
    {"date": "2026-13-01", "value": 1},             # 잘못된 날짜
    {"date": "2026-08-01", "value": "abc"},         # 숫자 아님
    {"date": "2026-08-01", "value": 1, "memo": "x" * 501},  # 메모 초과
])
def test_data_validation(ctx, body):
    client, _, _ = ctx
    assert client.post("/api/data", json=body).status_code == 422


# ---------- 요약 ----------


def test_summary_empty_is_404(ctx):
    client, _, _ = ctx
    assert client.get("/api/data/summary").status_code == 404


def test_summary_values(ctx):
    client, store, _ = ctx
    seed(store, 30)  # 2024-01 ~ 2026-06, 값 50~79
    s = client.get("/api/data/summary").json()
    last = (today_kst().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    assert s["period"] == f"1961-01 ~ {last}"   # 기본 범위는 데이터가 아니라 1961-01 ~ 지난달
    assert s["count"] == 30 and s["total_count"] == 30
    assert s["missing_count"] > 0 and s["missing_months"][0] == "1961-01"
    assert s["maximum"] == {"date": "2026-06-01", "value": 79.0}
    assert s["minimum"] == {"date": "2024-01-01", "value": 50.0}
    assert s["trend"] == "상승"
    assert set(s["summer_by_decade"]) == {"2020s"}


def test_trend_needs_24_months():
    items = [DataItem(id=str(i), date=date(2025, 1, 1).replace(month=i + 1), value=60, memo=None) for i in range(12)]
    assert compute_summary(items).trend == "알 수 없음"


def test_trend_flat_is_stable():
    items = [
        DataItem(id=str(i), date=date(2024 + i // 12, i % 12 + 1, 1), value=60 + (i % 12), memo=None)
        for i in range(24)
    ]  # 같은 계절 패턴이 두 해 반복 → 12개월 창 평균이 같다
    assert compute_summary(items).trend == "유지"


# ---------- 채팅 ----------


def test_chat_injects_summary_and_autosaves(ctx):
    client, store, llm = ctx
    seed(store)
    r = client.post("/api/chat", json={"message": "여름이 더 습해졌어?"})
    assert r.status_code == 200
    cid = r.json()["conversation_id"]

    system = llm.calls[0][0]
    assert system["role"] == "system"
    assert "레코드: 30개월" in system["content"]       # 요약이 주입됨
    assert "−1.52%p" in system["content"]            # M1-1 결론 포함

    detail = client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    client.post("/api/chat", json={"message": "그럼 기온은?", "conversation_id": cid})
    assert [m["role"] for m in llm.calls[1]] == ["system", "user", "assistant", "user"]  # 이력 포함
    assert client.get(f"/api/conversations/{cid}").json()["message_count"] == 4


def test_chat_validation_and_errors(ctx):
    client, store, llm = ctx
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert client.post("/api/chat", json={"message": "a", "conversation_id": "없음"}).status_code == 404
    llm.fail = True
    r = client.post("/api/chat", json={"message": "안녕"})
    assert r.status_code == 502 and "AI" in r.json()["detail"]
    assert store.convs == {}  # 실패하면 저장하지 않는다


def test_forecast_split_by_today():
    rows = [DataItem(id=f"2026-{m:02d}-01", date=date(2026, m, 1), value=v, memo=None)
            for m, v in [(7, 77.0), (8, 78.0), (9, 70.0), (10, 90.0), (11, 20.0)]]
    s = compute_summary(rows, today=date(2026, 10, 5))
    assert s.today == date(2026, 10, 5)
    assert s.period == "1961-01 ~ 2026-09" and s.count == 3      # 기본 기록만
    assert s.latest.date == date(2026, 9, 1)
    assert s.maximum.value == 78.0 and s.minimum.value == 70.0    # 예측값 제외
    assert s.maximum_all.value == 90.0                             # 추가 기록 포함
    assert s.minimum_all.value == 20.0
    assert s.total_count == 5 and s.average_all == 67.0
    assert s.forecast_count == 2 and s.forecast_period == "2026-10 ~ 2026-11"
    prompt = build_system_prompt(s)
    assert "오늘 날짜: 2026-10-05" in prompt
    assert "예측 2개월 (2026-10 ~ 2026-11)" in prompt and "추가 포함 최고: 90.0 (2026-10)" in prompt


def test_no_forecast_prompt_says_none():
    rows = [DataItem(id="2026-08-01", date=date(2026, 8, 1), value=78.0, memo=None)]
    s = compute_summary(rows, today=date(2026, 10, 5))
    assert s.forecast_count == 0 and s.maximum == s.maximum_all
    assert "- 없음" in build_system_prompt(s)


def test_star_kept_when_put_omits_it(ctx):
    client, _, _ = ctx
    client.post("/api/data", json={"date": "2024-08-01", "value": 80.75})
    r = client.put("/api/data/2024-08-01", json={"value": 80.75, "memo": "폭염", "starred": True})
    assert r.json()["starred"] is True
    r = client.put("/api/data/2024-08-01", json={"value": 81, "memo": "폭염, 열대야"})  # starred 생략
    assert r.json()["starred"] is True and r.json()["value"] == 81
    r = client.put("/api/data/2024-08-01", json={"value": 81, "memo": None, "starred": False})
    assert r.json()["starred"] is False


def test_highlights_merge_max_min_and_stars():
    def row(y, m, v, memo=None, starred=False):
        return DataItem(id=f"{y}-{m:02d}-01", date=date(y, m, 1), value=v, memo=memo, starred=starred)
    rows = [
        row(1963, 1, 23.3, "역대 최저 추위"),                 # 최저
        row(1994, 8, 79.0, "1994 폭염", starred=True),        # 즐겨찾기만
        row(2024, 8, 80.7, "역대급 폭염", starred=True),      # 최고 + 즐겨찾기 → 한 줄
        row(2026, 9, 70.0),
        row(2026, 11, 85.0, "내 예측", starred=True),         # 추가 포함 최고 + 즐겨찾기 + 예측값
    ]
    s = compute_summary(rows, today=date(2026, 10, 5))
    got = {f"{h.date:%Y-%m}": h.tags for h in s.highlights}
    assert got == {
        "1963-01": ["최저"],
        "1994-08": ["즐겨찾기"],
        "2024-08": ["최고", "즐겨찾기"],
        "2026-11": ["추가 포함 최고", "즐겨찾기", "예측값"],
    }
    assert [f"{h.date:%Y-%m}" for h in s.highlights] == sorted(got)  # 날짜순
    prompt = build_system_prompt(s)
    assert "- 2024-08: 80.7 [최고·즐겨찾기] 메모: 역대급 폭염" in prompt
    assert prompt.count("2024-08: 80.7 [") == 1                       # 중복 없이 한 줄
    assert "즐겨찾기하신 달은 N개예요" in prompt and "그달에는" in prompt and "별표" not in prompt
    assert "즐겨찾기 여부와 유효일수는 말하지 마세요" in prompt and "요청이 없으면 이 안내를 하지 마세요" in prompt
    assert "불쾌지수와 기온을 구분" in prompt and "분류 번호나 판단 과정은 답에 쓰지 말고" in prompt


def test_prompt_classifies_input_types():
    prompt = build_system_prompt(compute_summary(
        [DataItem(id="2024-08-01", date=date(2024, 8, 1), value=80.0, memo=None)], today=date(2026, 10, 5)))
    for phrase in ("인사·잡담", "착각한 경우", "본인의 기록", "이해할 수 없는 입력", "데이터에 대한 질문", '"그달"로 답하세요'):
        assert phrase in prompt


def test_highlight_memo_is_truncated():
    rows = [DataItem(id="2024-08-01", date=date(2024, 8, 1), value=80.0, memo="가" * 300)]
    prompt = build_system_prompt(compute_summary(rows, today=date(2026, 10, 5)))
    assert "가" * 100 + "…" in prompt and "가" * 101 not in prompt


def test_system_prompt_without_data():
    assert "데이터가 없" in build_system_prompt(None)


# ---------- 대화 기록 ----------


def test_conversations_crud(ctx):
    client, _, _ = ctx
    r = client.post("/api/conversations", json={"messages": [{"role": "user", "content": "첫 질문입니다"}]})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["title"] == "첫 질문입니다"

    listed = client.get("/api/conversations").json()
    assert listed[0]["id"] == cid and "messages" not in listed[0]  # 목록엔 본문 없음

    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert client.get("/api/conversations?limit=0").status_code == 422
    assert client.post("/api/conversations", json={"messages": []}).status_code == 422


def _rows(start: date, n: int, value=60.0):
    out, y, m = [], start.year, start.month
    for _ in range(n):
        out.append(DataItem(id=f"{y}-{m:02d}-01", date=date(y, m, 1), value=value, memo=None))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def test_past_records_are_separated_from_base():
    rows = _rows(date(2024, 10, 1), 24) + [
        DataItem(id="1955-07-01", date=date(1955, 7, 1), value=99.0, memo="과거"),
        DataItem(id="2026-11-01", date=date(2026, 11, 1), value=10.0, memo=None),
    ]
    s = compute_summary(rows, today=date(2026, 10, 5))
    assert s.count == 24 and s.past_count == 1 and s.forecast_count == 1 and s.total_count == 26
    assert s.average == 60.0 and s.average_all != 60.0                 # 통산 평균엔 추가 기록 제외
    assert s.maximum.value == 60.0 and s.maximum_all.value == 99.0
    assert "1950s" not in s.summer_by_decade                           # 여름 평균도 기본 기록만
    assert s.trend == "유지" and s.trend_window == "2025-10 ~ 2026-09"
    tags = {f"{h.date:%Y-%m}": h.tags for h in s.highlights}
    assert tags["1955-07"] == ["추가 포함 최고", "과거 추가"]
    assert "과거 1개월 (1955-07 ~ 1955-07)" in build_system_prompt(s)


def test_trend_unknown_when_month_missing():
    rows = [r for r in _rows(date(2024, 9, 1), 24) if r.date != date(2025, 3, 1)]  # 한 달 결측
    s = compute_summary(rows, today=date(2026, 9, 5))
    assert s.trend == "알 수 없음" and s.trend_diff is None
    assert s.missing_count == 788 - 23                                   # 1961-01~2026-08 중 기록 없는 달
    assert "결측: " in build_system_prompt(s)
