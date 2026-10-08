"""Firestore 저장소. 컬렉션은 두 개다.

- data/{YYYY-MM-DD}        : {date, value, memo}  — 문서 ID가 날짜라 중복이 구조적으로 막힌다.
- conversations/{자동 ID}  : {title, created_at, updated_at, message_count, messages[]}
"""

import json
import os
import time
from datetime import datetime, timezone
from threading import Lock

from errors import ConflictError, NotFoundError, StoreUnavailableError
from models import DataCreate, DataItem, DataUpdate, Message

DATA = "data"
CONVERSATIONS = "conversations"
META_FIELDS = ["title", "created_at", "updated_at", "message_count"]

# data 컬렉션 전체 읽기(788건)를 요청마다 반복하면 Spark 무료 한도(읽기 5만/일)를
# 새로고침 30번 남짓에 다 쓴다. 이 서버를 거친 쓰기는 즉시 캐시를 비우고,
# 콘솔 직접 수정·seed.py 같은 외부 변경은 TTL이 지나면 반영된다.
DATA_CACHE_TTL_S = 600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_firestore_client():
    """환경 변수에서 서비스 계정을 읽어 Firestore 클라이언트를 만든다.

    FIREBASE_SERVICE_ACCOUNT_PATH (파일 경로, Render Secret File 권장) 또는
    FIREBASE_SERVICE_ACCOUNT_JSON (JSON 문자열) 중 하나가 필요하다.
    """
    import firebase_admin
    from firebase_admin import credentials, firestore

    path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if path:
        cred = credentials.Certificate(path)
    elif raw:
        cred = credentials.Certificate(json.loads(raw))
    else:
        raise StoreUnavailableError()

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    return firestore.client()


class FirestoreStore:
    def __init__(self, client, cache_ttl_s: float = DATA_CACHE_TTL_S, clock=time.monotonic):
        self.db = client
        self._cache: list[DataItem] | None = None
        self._cache_expires = 0.0
        self._cache_ttl = cache_ttl_s
        self._clock = clock
        self._lock = Lock()

    # ---------- 데이터 ----------

    def list_data(self) -> list[DataItem]:
        with self._lock:
            if self._cache is None or self._clock() >= self._cache_expires:
                docs = self.db.collection(DATA).order_by("date").stream()
                self._cache = [DataItem(id=d.id, **d.to_dict()) for d in docs]
                self._cache_expires = self._clock() + self._cache_ttl
            return list(self._cache)

    def _invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def create_data(self, item: DataCreate) -> DataItem:
        doc_id = item.date.isoformat()
        ref = self.db.collection(DATA).document(doc_id)
        if ref.get().exists:
            raise ConflictError()
        payload = {"date": doc_id, "value": item.value, "memo": item.memo, "starred": item.starred}
        ref.set(payload)
        self._invalidate()
        return DataItem(id=doc_id, **payload)

    def update_data(self, doc_id: str, item: DataUpdate) -> DataItem:
        ref = self.db.collection(DATA).document(doc_id)
        snap = ref.get()
        if not snap.exists:
            raise NotFoundError("해당 날짜의 데이터가 없습니다.")
        changes = {"value": item.value, "memo": item.memo}
        if item.starred is not None:  # 보내지 않으면 기존 별표 유지
            changes["starred"] = item.starred
        ref.update(changes)
        self._invalidate()
        return DataItem(id=doc_id, **{**snap.to_dict(), **changes})

    def delete_data(self, doc_id: str) -> None:
        ref = self.db.collection(DATA).document(doc_id)
        if not ref.get().exists:
            raise NotFoundError("해당 날짜의 데이터가 없습니다.")
        ref.delete()
        self._invalidate()

    # ---------- 대화 ----------

    def create_conversation(self, title: str, messages: list[Message]) -> str:
        now = _now()
        ref = self.db.collection(CONVERSATIONS).document()
        ref.set({
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": len(messages),
            "messages": [{**m.model_dump(), "created_at": now} for m in messages],
        })
        return ref.id

    def append_messages(self, conversation_id: str, messages: list[Message]) -> None:
        ref = self.db.collection(CONVERSATIONS).document(conversation_id)
        snap = ref.get()
        if not snap.exists:
            raise NotFoundError("대화를 찾을 수 없습니다.")
        now = _now()
        stored = snap.to_dict().get("messages", [])
        stored += [{**m.model_dump(), "created_at": now} for m in messages]
        ref.update({"messages": stored, "message_count": len(stored), "updated_at": now})

    def list_conversations(self, limit: int) -> list[dict]:
        query = (
            self.db.collection(CONVERSATIONS)
            .select(META_FIELDS)  # 목록에서는 messages 본문을 읽지 않는다
            .order_by("updated_at", direction="DESCENDING")
            .limit(limit)
        )
        return [{"id": d.id, **d.to_dict()} for d in query.stream()]

    def get_conversation(self, conversation_id: str) -> dict:
        snap = self.db.collection(CONVERSATIONS).document(conversation_id).get()
        if not snap.exists:
            raise NotFoundError("대화를 찾을 수 없습니다.")
        return {"id": snap.id, **snap.to_dict()}

    def delete_conversation(self, conversation_id: str) -> None:
        ref = self.db.collection(CONVERSATIONS).document(conversation_id)
        if not ref.get().exists:
            raise NotFoundError("대화를 찾을 수 없습니다.")
        ref.delete()
