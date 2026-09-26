"""Firestore 저장소. 컬렉션은 두 개다.

- data/{YYYY-MM-DD}        : {date, value, memo}  — 문서 ID가 날짜라 중복이 구조적으로 막힌다.
- conversations/{자동 ID}  : {title, created_at, updated_at, message_count, messages[]}
"""

import json
import os
from datetime import datetime, timezone

from errors import ConflictError, NotFoundError, StoreUnavailableError
from models import DataCreate, DataItem, DataUpdate, Message

DATA = "data"
CONVERSATIONS = "conversations"
META_FIELDS = ["title", "created_at", "updated_at", "message_count"]


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
    def __init__(self, client):
        self.db = client

    # ---------- 데이터 ----------

    def list_data(self) -> list[DataItem]:
        docs = self.db.collection(DATA).order_by("date").stream()
        return [DataItem(id=d.id, **d.to_dict()) for d in docs]

    def create_data(self, item: DataCreate) -> DataItem:
        doc_id = item.date.isoformat()
        ref = self.db.collection(DATA).document(doc_id)
        if ref.get().exists:
            raise ConflictError()
        payload = {"date": doc_id, "value": item.value, "memo": item.memo}
        ref.set(payload)
        return DataItem(id=doc_id, **payload)

    def update_data(self, doc_id: str, item: DataUpdate) -> DataItem:
        ref = self.db.collection(DATA).document(doc_id)
        snap = ref.get()
        if not snap.exists:
            raise NotFoundError("해당 날짜의 데이터가 없습니다.")
        ref.update({"value": item.value, "memo": item.memo})
        return DataItem(id=doc_id, **{**snap.to_dict(), "value": item.value, "memo": item.memo})

    def delete_data(self, doc_id: str) -> None:
        ref = self.db.collection(DATA).document(doc_id)
        if not ref.get().exists:
            raise NotFoundError("해당 날짜의 데이터가 없습니다.")
        ref.delete()

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
