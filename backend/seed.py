"""data/seoul_di_monthly.csv를 Firestore `data` 컬렉션에 넣는다.

    python seed.py           # 시드 788건을 원래 값으로 덮어쓴다 (즐겨찾기도 해제)
    python seed.py --reset   # 위 + 시드에 없는 달(직접 추가한 예측·과거 기록)을 삭제한다

문서 ID가 날짜라 여러 번 실행해도 같은 문서를 덮어쓸 뿐 중복되지 않는다.
"""

import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

from store import DATA, create_firestore_client

SEED_FILE = Path(__file__).parent / "data" / "seoul_di_monthly.csv"
BATCH_SIZE = 400  # Firestore 배치 한도(500) 아래


def main() -> None:
    load_dotenv()
    db = create_firestore_client()
    with open(SEED_FILE, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for start in range(0, len(rows), BATCH_SIZE):
        batch = db.batch()
        for row in rows[start : start + BATCH_SIZE]:
            ref = db.collection(DATA).document(row["date"])
            batch.set(ref, {"date": row["date"], "value": float(row["value"]), "memo": row["memo"] or None})
        batch.commit()
        print(f"{min(start + BATCH_SIZE, len(rows))}/{len(rows)}")

    if "--reset" in sys.argv:
        seed_ids = {row["date"] for row in rows}
        extra = [doc.reference for doc in db.collection(DATA).select([]).stream() if doc.id not in seed_ids]
        for start in range(0, len(extra), BATCH_SIZE):
            batch = db.batch()
            for ref in extra[start : start + BATCH_SIZE]:
                batch.delete(ref)
            batch.commit()
        print(f"시드에 없는 {len(extra)}건 삭제: {', '.join(r.id for r in extra) or '없음'}")
    print("완료")


if __name__ == "__main__":
    main()
