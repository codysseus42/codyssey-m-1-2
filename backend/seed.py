"""data/seoul_di_monthly.csv를 Firestore `data` 컬렉션에 넣는다.

    python seed.py

문서 ID가 날짜라 여러 번 실행해도 같은 문서를 덮어쓸 뿐 중복되지 않는다.
"""

import csv
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
    print("완료")


if __name__ == "__main__":
    main()
