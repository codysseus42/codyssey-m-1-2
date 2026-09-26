"""M1-1 정제 자료에서 M1-2 시드 데이터(서울 월별 불쾌지수)를 만든다.

사용법:
    python scripts/make_seed_data.py <summer-seoul-data 경로>/data/processed/asos_clean.csv

규칙 (M1-1과 동일):
- 불쾌지수는 일 단위로 먼저 계산한 뒤 월평균을 낸다 (식이 비선형이라 순서가 중요하다).
- 유효 관측일이 25일 미만인 달(수집 시점의 미완료 달)은 제외한다.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean

MIN_DAYS = 25
OUT = Path(__file__).resolve().parents[1] / "backend" / "data" / "seoul_di_monthly.csv"


def discomfort_index(t: float, rh: float) -> float:
    return 0.81 * t + 0.01 * rh * (0.99 * t - 14.3) + 46.3


def main(src: str) -> None:
    months: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    with open(src, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["stn"] != "108":
                continue
            t, rh = float(row["tavg"]), float(row["rh"])
            months[row["date"][:7]].append((t, rh, discomfort_index(t, rh)))

    rows = []
    for month in sorted(months):
        days = months[month]
        if len(days) < MIN_DAYS:
            print(f"제외: {month} ({len(days)}일)")
            continue
        rows.append([
            f"{month}-01",
            round(fmean(d[2] for d in days), 2),
            f"평균기온 {fmean(d[0] for d in days):.1f}°C, "
            f"평균습도 {fmean(d[1] for d in days):.1f}%, 유효 {len(days)}일",
        ])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "value", "memo"])
        writer.writerows(rows)
    print(f"{len(rows)}건 → {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
