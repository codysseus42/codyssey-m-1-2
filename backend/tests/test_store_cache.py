"""FirestoreStore가 data 컬렉션 전체 읽기를 캐시하는지 검증한다 (Spark 무료 한도 보호)."""

import datetime as dt
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import DataCreate, DataUpdate  # noqa: E402
from store import FirestoreStore  # noqa: E402


def make_store(now):
    client = MagicMock()
    doc = MagicMock(id="2026-08-01")
    doc.to_dict.return_value = {"date": "2026-08-01", "value": 78.5, "memo": None}
    scan = client.collection.return_value.order_by.return_value.stream
    scan.return_value = [doc]
    client.collection.return_value.document.return_value.get.return_value.exists = False
    return FirestoreStore(client, cache_ttl_s=600, clock=lambda: now[0]), scan


def test_repeated_reads_hit_firestore_once():
    now = [0.0]
    store, scan = make_store(now)
    for _ in range(20):  # 새로고침·채팅 20번
        assert store.list_data()[0].value == 78.5
    assert scan.call_count == 1


def test_writes_invalidate_cache():
    now = [0.0]
    store, scan = make_store(now)
    store.list_data()
    store.create_data(DataCreate(date=dt.date(2026, 9, 1), value=70))
    store.list_data()
    assert scan.call_count == 2

    snap = store.db.collection.return_value.document.return_value.get.return_value
    snap.exists = True
    snap.to_dict.return_value = {"date": "2026-08-01", "value": 1, "memo": None}
    store.update_data("2026-08-01", DataUpdate(value=2))
    store.list_data()
    store.delete_data("2026-08-01")
    store.list_data()
    assert scan.call_count == 4


def test_ttl_picks_up_external_changes():
    now = [0.0]
    store, scan = make_store(now)
    store.list_data()
    now[0] = 599
    store.list_data()
    assert scan.call_count == 1
    now[0] = 600
    store.list_data()
    assert scan.call_count == 2
