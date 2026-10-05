"""Firebase·OpenAI 없이 전체 API 흐름을 검증한다. 실행: backend/ 에서 `pytest -q`"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from errors import AIServiceError, ConflictError, NotFoundError  # noqa: E402
from models import DataItem  # noqa: E402
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
        self.data[key] = {"date": key, "value": item.value, "memo": item.memo}
        return DataItem(id=key, **self.data[key])

    def update_data(self, key, item):
        if key not in self.data:
            raise NotFoundError()
        self.data[key].update(value=item.value, memo=item.memo)
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
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_llm] = lambda: llm
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
    assert s["period"] == "2024-01 ~ 2026-06"
    assert s["count"] == 30
    assert s["maximum"] == {"date": "2026-06-01", "value": 79.0}
    assert s["minimum"] == {"date": "2024-01-01", "value": 50.0}
    assert s["trend"] == "상승"
    assert set(s["summer_by_decade"]) == {"2020s"}


def test_trend_needs_24_months():
    items = [DataItem(id=str(i), date=date(2025, 1, 1).replace(month=i + 1), value=60, memo=None) for i in range(12)]
    assert compute_summary(items).trend == "데이터 부족"


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
    assert "2024-01 ~ 2026-06" in system["content"]  # 요약이 주입됨
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
    assert s.period == "2026-07 ~ 2026-09" and s.count == 3      # 관측값만
    assert s.latest.date == date(2026, 9, 1)
    assert s.maximum.value == 78.0 and s.minimum.value == 70.0    # 예측값 제외
    assert s.maximum_with_forecast.value == 90.0                   # 예측값 포함
    assert s.minimum_with_forecast.value == 20.0
    assert s.forecast_count == 2 and s.forecast_period == "2026-10 ~ 2026-11"
    prompt = build_system_prompt(s)
    assert "오늘 날짜: 2026-10-05" in prompt
    assert "2개 (2026-10 ~ 2026-11)" in prompt and "최고: 90.0 (2026-10)" in prompt


def test_no_forecast_prompt_says_none():
    rows = [DataItem(id="2026-08-01", date=date(2026, 8, 1), value=78.0, memo=None)]
    s = compute_summary(rows, today=date(2026, 10, 5))
    assert s.forecast_count == 0 and s.maximum == s.maximum_with_forecast
    assert "- 없음" in build_system_prompt(s)


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
