"""Lấy tin tức và lịch kinh tế từ nguồn miễn phí, không cần key.

Nguồn chính:
1. Lịch kinh tế ForexFactory (file XML tuần này) — sự kiện, quốc gia, mức ảnh hưởng.
   URL đã kiểm chứng qua tìm kiếm web ngày 2026-11-01:
   https://nfs.faireconomy.media/ff_calendar_thisweek.xml
   Định dạng XML có thể đổi, nên parser đọc linh hoạt theo tên thẻ, không cứng
   theo một schema duy nhất. Lấy không được thì trả về "không có dữ liệu",
   không bịa sự kiện.
2. RSS của Cục Dự trữ Liên bang Mỹ (Fed) — thông cáo báo chí, đã kiểm chứng:
   https://www.federalreserve.gov/feeds/press_all.xml

Chỉ lấy tiêu đề + thời gian, KHÔNG tự diễn giải nội dung bài viết.
AI Fundamental chỉ được dùng đúng những gì fetch được; không có tin mới
thì phải nói rõ là không có tin mới, không suy đoán tin chưa công bố.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class CalendarEvent:
    title: str
    country: str
    impact: str  # HIGH / MEDIUM / LOW / UNKNOWN
    when_text: str  # ngày + giờ đúng như nguồn ghi, không tự đổi múi giờ
    forecast: str = ""
    previous: str = ""
    when: datetime | None = None


@dataclass
class NewsBundle:
    headlines: list[str] = field(default_factory=list)
    calendar_events: list[CalendarEvent] = field(default_factory=list)
    sources_tried: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        lines = ["TIN TỨC VÀ LỊCH KINH TẾ (chỉ những gì lấy được từ nguồn, không bịa):"]
        if self.headlines:
            lines.append("Tiêu đề mới nhất:")
            for h in self.headlines[:10]:
                lines.append(f"- {h}")
        else:
            lines.append("Không lấy được tiêu đề tin mới nào.")
        if self.calendar_events:
            lines.append("Sự kiện lịch kinh tế sắp tới / trong tuần (giờ đúng như nguồn ghi):")
            for e in self.calendar_events[:15]:
                extra = f", dự báo {e.forecast}, trước đó {e.previous}" if e.forecast or e.previous else ""
                lines.append(f"- [{e.impact}] {e.country} {e.when_text}: {e.title}{extra}")
        else:
            lines.append("Không lấy được sự kiện lịch kinh tế nào.")
        if self.errors:
            lines.append("Nguồn lỗi: " + "; ".join(self.errors))
        return "\n".join(lines)


def _fetch_text(url: str, timeout: float = 20.0) -> str:
    import httpx  # import lười để --mock chạy được không cần httpx

    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "Universal-Trade AI Trading Room"}) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def _text_of(element, *names: str) -> str:
    for name in names:
        child = element.find(name)
        if child is not None and child.text:
            return child.text.strip()
        # Tìm không phân biệt hoa/thường ở cấp con trực tiếp.
        for child in element:
            if child.tag.lower() == name.lower() and child.text:
                return child.text.strip()
    return ""


def _parse_calendar_xml(text: str) -> list[CalendarEvent]:
    """Đọc XML lịch ForexFactory một cách linh hoạt.

    Các bản XML khác nhau có thể dùng <event>/<weeklyevents> và tên thẻ
    title/country/date/time/impact/forecast/previous khác nhau đôi chút.
    Hàm này duyệt mọi phần tử có tiêu đề, thay vì cứng theo một root duy nhất.
    """
    events: list[CalendarEvent] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return events
    for element in root.iter():
        title = _text_of(element, "title", "event", "name")
        if not title:
            continue
        country = _text_of(element, "country", "currency", "ccy")
        impact_raw = _text_of(element, "impact", "importance")
        impact = impact_raw.upper() if impact_raw else "UNKNOWN"
        if impact not in ("HIGH", "MEDIUM", "LOW"):
            impact = "HIGH" if "high" in impact_raw.lower() else (
                "MEDIUM" if "medium" in impact_raw.lower() else (
                    "LOW" if "low" in impact_raw.lower() else "UNKNOWN"))
        date_text = _text_of(element, "date")
        time_text = _text_of(element, "time")
        when_text = f"{date_text} {time_text}".strip()
        when = _parse_event_time(date_text, time_text)
        events.append(CalendarEvent(
            title=title, country=country or "?", impact=impact, when_text=when_text,
            forecast=_text_of(element, "forecast"), previous=_text_of(element, "previous"),
            when=when,
        ))
    # Bỏ trùng theo (when_text, title).
    seen, unique = set(), []
    for e in events:
        key = (e.when_text, e.title)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _parse_event_time(date_text: str, time_text: str) -> datetime | None:
    if not date_text:
        return None
    cleaned_time = time_text.strip().lower()
    for date_fmt in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        for time_fmt in ("%I:%M%p", "%I:%M %p", "%H:%M", ""):
            try:
                if time_fmt:
                    return datetime.strptime(f"{date_text} {cleaned_time}", f"{date_fmt} {time_fmt}")
                return datetime.strptime(date_text, date_fmt)
            except ValueError:
                continue
    return None


def _parse_rss(text: str, keywords: list[str]) -> list[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    headlines: list[str] = []
    for item in root.iter("item"):
        title = _text_of(item, "title")
        if title:
            headlines.append(title)
    # Ưu tiên tiêu đề liên quan vàng/Fed/USD/CPI/NFP, rồi mới tới tiêu đề khác.
    keys = [k.lower() for k in keywords]
    relevant = [h for h in headlines if any(k in h.lower() for k in keys)]
    others = [h for h in headlines if h not in relevant]
    return relevant + others


def fetch_news(
    calendar_url: str,
    rss_feeds: list[str],
    keywords: list[str],
    *,
    timeout: float = 20.0,
    fetcher=None,
) -> NewsBundle:
    """Lấy tin + lịch. `fetcher` chỉ dùng khi test, để truyền text mẫu."""
    bundle = NewsBundle()
    fetch = fetcher or _fetch_text

    # 1. Lịch kinh tế.
    bundle.sources_tried.append(calendar_url)
    try:
        text = fetch(calendar_url, timeout) if fetcher is None else fetch(calendar_url)
        events = _parse_calendar_xml(text)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Chỉ giữ sự kiện USD/EUR và ảnh hưởng cao/trung bình trong 48 giờ tới
        # (sự kiện không parse được giờ thì vẫn giữ, để AI tự đọc when_text).
        kept = []
        for e in events:
            if e.when is not None and e.when < now - timedelta(hours=12):
                continue
            if e.when is not None and e.when > now + timedelta(hours=48):
                continue
            kept.append(e)
        high_first = sorted(kept, key=lambda e: (0 if e.impact == "HIGH" else 1, e.when_text))
        bundle.calendar_events = high_first
    except Exception as exc:  # Lỗi mạng / nguồn đổi định dạng: ghi rõ, không bịa.
        bundle.errors.append(f"calendar: {exc}")

    # 2. RSS tiêu đề.
    for url in rss_feeds:
        bundle.sources_tried.append(url)
        try:
            text = fetch(url, timeout) if fetcher is None else fetch(url)
            bundle.headlines.extend(_parse_rss(text, keywords))
        except Exception as exc:
            bundle.errors.append(f"rss {url}: {exc}")
    # Bỏ trùng, giữ thứ tự.
    bundle.headlines = list(dict.fromkeys(bundle.headlines))[:20]
    return bundle


# Từ khóa lọc tin liên quan vàng. Dùng để ưu tiên hiển thị, không dùng để bịa tin.
GOLD_NEWS_KEYWORDS = [
    "gold", "fomc", "federal reserve", "fed", "cpi", "inflation", "nfp",
    "non-farm", "nonfarm", "interest rate", "powell", "dollar", "usd",
    "treasury", "yield", "gdp", "unemployment", "pce",
]
