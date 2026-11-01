"""Dữ liệu sentiment cho vàng từ nguồn công khai, có ghi rõ nguồn và độ cũ.

1. COT (Commitments of Traders) vàng — file CSV công khai của FuturesBench,
   lấy từ dữ liệu CFTC (public domain), đã kiểm chứng qua tìm kiếm web
   ngày 2026-11-01: https://futuresbench.com/data/cot/gold.csv
   Cột dùng: report_date, open_interest, net_noncommercial (vị thế ròng
   của nhóm phi thương mại / large speculators).
   Tính: giá trị mới nhất, thay đổi so với tuần trước, và vị trí phần trăm
   trong 52 tuần gần nhất (0 = thấp nhất, 100 = cao nhất). Đây là mô tả
   vị thế, KHÔNG phải dự báo giá.
   COT công bố thứ Sáu, dữ liệu chốt thứ Ba tuần trước, nên luôn cũ vài ngày.
   Code ghi rõ report_date để AI không tưởng là dữ liệu hôm nay.

2. Điểm tiêu đề tin tức — đếm từ khóa tăng/giảm trong tiêu đề lấy được
   từ news_fetcher. Đây CHỈ là điểm đếm từ khóa, không phải Fear & Greed
   Index thật, không phải khảo sát tâm lý, và không thay thế được dữ liệu
   định vị thị trường. Ghi rõ phương pháp trong text trả về.

Không có nguồn nào khác được bịa thêm. Nguồn lỗi thì trả về "không có
dữ liệu", AI Sentiment phải hạ độ tin cậy hoặc HOLD, không suy đoán.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field


@dataclass
class SentimentBundle:
    cot_text: str = "Không có dữ liệu COT."
    headline_score_text: str = "Không có điểm tiêu đề tin tức."
    errors: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        lines = ["DỮ LIỆU SENTIMENT (chỉ những gì lấy được, có ghi nguồn và độ cũ):", self.cot_text, self.headline_score_text]
        if self.errors:
            lines.append("Nguồn lỗi: " + "; ".join(self.errors))
        return "\n".join(lines)


BULLISH_WORDS = ["rally", "surge", "soar", "record high", "bullish", "gains", "climbs", "rises", "jump", " haven", "safe haven"]
BEARISH_WORDS = ["fall", "falls", "drop", "drops", "slide", "slides", "bearish", "decline", "plunge", "tumble", "sell-off", "selloff"]


def headline_keyword_score(headlines: list[str]) -> str:
    if not headlines:
        return "Không có tiêu đề nào để tính điểm từ khóa."
    bull = bear = 0
    for h in headlines:
        low = h.lower()
        bull += sum(1 for w in BULLISH_WORDS if w in low)
        bear += sum(1 for w in BEARISH_WORDS if w in low)
    total = bull + bear
    if total == 0:
        return (
            f"Điểm từ khóa tiêu đề: trung tính (0 từ tăng / 0 từ giảm trong {len(headlines)} tiêu đề). "
            "Phương pháp: chỉ đếm từ khóa trong tiêu đề, không phải chỉ số tâm lý thật."
        )
    score = (bull - bear) / total  # -1 .. +1
    label = "nghiêng tăng" if score > 0.2 else ("nghiêng giảm" if score < -0.2 else "trung tính")
    return (
        f"Điểm từ khóa tiêu đề: {label} (điểm {score:.2f}, {bull} từ tăng / {bear} từ giảm "
        f"trong {len(headlines)} tiêu đề). Phương pháp: chỉ đếm từ khóa trong tiêu đề, "
        "không phải Fear & Greed Index thật."
    )


def _parse_cot_csv(text: str, lookback_weeks: int = 52) -> str:
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        try:
            rows.append({
                "date": row["report_date"].strip(),
                "open_interest": int(float(row["open_interest"])),
                "net_noncommercial": int(float(row["net_noncommercial"])),
            })
        except (KeyError, ValueError, TypeError):
            continue
    if len(rows) < 2:
        raise ValueError("File COT không đủ dòng dữ liệu.")
    rows = rows[-lookback_weeks:] if len(rows) >= lookback_weeks else rows
    latest, previous = rows[-1], rows[-2]
    values = [r["net_noncommercial"] for r in rows]
    low, high = min(values), max(values)
    percentile = 50.0 if high == low else (latest["net_noncommercial"] - low) / (high - low) * 100
    change = latest["net_noncommercial"] - previous["net_noncommercial"]
    return (
        f"COT vàng (nguồn: FuturesBench, dữ liệu CFTC public domain; báo cáo ngày {latest['date']}, "
        f"dữ liệu chốt thứ Ba trước đó, công bố thứ Sáu — luôn cũ vài ngày, không phải hôm nay): "
        f"nhóm phi thương mại (large speculators) ròng {latest['net_noncommercial']:+,} hợp đồng, "
        f"thay đổi {change:+,} so với tuần trước, open interest {latest['open_interest']:,} hợp đồng, "
        f"vị trí {percentile:.0f}/100 trong {len(rows)} tuần gần nhất "
        f"(0 = ròng thấp nhất, 100 = ròng cao nhất). Đây là mô tả vị thế, không phải dự báo giá."
    )


def fetch_sentiment(cot_csv_url: str, headlines: list[str], *, timeout: float = 25.0, fetcher=None) -> SentimentBundle:
    bundle = SentimentBundle(headline_score_text=headline_keyword_score(headlines))
    fetch = fetcher or _fetch_text
    try:
        text = fetch(cot_csv_url, timeout) if fetcher is None else fetch(cot_csv_url)
        bundle.cot_text = _parse_cot_csv(text)
    except Exception as exc:
        bundle.errors.append(f"COT: {exc}")
    return bundle


def _fetch_text(url: str, timeout: float = 25.0) -> str:
    import httpx  # import lười để --mock chạy được không cần httpx

    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "Universal-Trade AI Trading Room"}) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
