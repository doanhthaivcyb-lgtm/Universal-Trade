from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm_clients import LLMClient
from .market_snapshot import MarketSnapshot

JSON_SCHEMA = """Trả về DUY NHẤT 1 JSON, không thêm chữ ngoài JSON, đúng dạng:
{"direction": "BUY|SELL|HOLD", "confidence": 0.0-1.0, "entry": số hoặc null,
 "stop_loss": số hoặc null, "take_profit": số hoặc null,
 "reasoning": "lý do ngắn gọn", "invalidation": "điều kiện làm phân tích sai",
 "evidence": ["bằng chứng 1", "bằng chứng 2"]}
QUY TẮC ĐƠN VỊ BẮT BUỘC: entry, stop_loss, take_profit đều là GIÁ TUYỆT ĐỐI tính bằng USD/oz.
KHÔNG dùng pips, KHÔNG dùng điểm, KHÔNG trả về khoảng cách. Ví dụ giá 4000 USD thì
stop_loss 3990 USD và take_profit 4020 USD, không phải 100 pips hay 20 điểm.
Chỉ dùng số liệu trong snapshot. Không bịa tin, không bịa chỉ báo. Không chắc thì HOLD."""

SYSTEM_PROMPTS = {
    "technical": (
        "Bạn là AI phân tích kỹ thuật vàng. Chỉ phân tích xu hướng, cấu trúc giá, "
        "hỗ trợ/kháng cự, RSI/MACD/ATR/EMA/Bollinger có trong snapshot. " + JSON_SCHEMA
    ),
    "fundamental": (
        "Bạn là AI phân tích cơ bản vàng: Fed, USD/DXY, lợi suất, CPI, NFP, "
        "địa chính trị. Chỉ dùng tin tức và lịch kinh tế đã lấy được trong snapshot "
        "(phần TIN TỨC VÀ LỊCH KINH TẾ). Không có tin mới thì nói rõ là không có, "
        "không suy đoán tin chưa công bố, không bịa số liệu kinh tế. " + JSON_SCHEMA
    ),
    "sentiment": (
        "Bạn là AI phân tích tâm lý và dòng tiền vàng. Chỉ dùng phần DỮ LIỆU SENTIMENT "
        "trong snapshot: COT (ghi rõ ngày báo cáo, luôn cũ vài ngày) và điểm từ khóa "
        "tiêu đề tin tức (chỉ là đếm từ khóa, không phải Fear & Greed thật), cộng với "
        "hành vi giá trong phiên. Không có dữ liệu sentiment thì hạ độ tin cậy hoặc HOLD, "
        "không suy đoán tâm lý thị trường. " + JSON_SCHEMA
    ),
    "portfolio_manager": (
        "Bạn là AI quản lý danh mục. Đánh giá exposure toàn tài khoản trong snapshot "
        "(tổng giá trị danh mục, vị thế từng symbol, lãi/lỗ đang nổi, equity/margin), "
        "mức độ tập trung vào vàng, và rủi ro nếu cộng thêm 1 lệnh vàng nữa. "
        "Nếu danh mục đã quá tập trung hoặc equity đã chịu nhiều rủi ro thì chọn HOLD "
        "hoặc hạ độ tin cậy. Bạn không quyết lot, code sẽ quyết. " + JSON_SCHEMA
    ),
    "contrarian": (
        "Bạn là AI phản biện. Nhiệm vụ của bạn là tìm điểm yếu nhất của phe đa số, "
        "kịch bản ngược chiều và điểm vô hiệu. Không đồng thuận cho có. " + JSON_SCHEMA
    ),
    "risk_analyst": (
        "Bạn là AI quản trị rủi ro (tư vấn). Đánh giá spread, biến động, giờ tin, "
        "mức lỗ nếu stop bị quét. Bạn không quyết lot, code sẽ quyết. " + JSON_SCHEMA
    ),
    "chief": (
        "Bạn là trưởng phòng giao dịch. Bạn tổng hợp các phân tích dưới đây, cân nhắc "
        "cả ý phản biện và ý quản lý danh mục, và ra 1 quyết định cuối duy nhất. Nếu mâu thuẫn mạnh hoặc "
        "bằng chứng yếu thì chọn HOLD. Stop loss và take profit là giá tuyệt đối bằng USD, "
        "phải hợp lý với giá hiện tại. KHÔNG dùng pips. "
        + JSON_SCHEMA
    ),
}


@dataclass
class AgentView:
    name: str
    provider: str
    model: str
    direction: str = "HOLD"
    confidence: float = 0.0
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reasoning: str = ""
    invalidation: str = ""
    evidence: list = field(default_factory=list)
    raw_text: str = ""
    parse_ok: bool = False


def parse_json_view(text: str) -> dict:
    """Lấy khối JSON đầu tiên trong text, chịu được markdown fence."""
    cleaned = text.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("Không tìm thấy JSON trong câu trả lời của AI")
    data = json.loads(match.group(0))
    if data.get("direction") not in ("BUY", "SELL", "HOLD"):
        raise ValueError(f"direction không hợp lệ: {data.get('direction')}")
    return data


def view_from_data(name: str, provider: str, model: str, data: dict, raw: str) -> AgentView:
    conf = float(data.get("confidence", 0.0))
    return AgentView(
        name=name,
        provider=provider,
        model=model,
        direction=str(data.get("direction")),
        confidence=max(0.0, min(1.0, conf)),
        entry=data.get("entry"),
        stop_loss=data.get("stop_loss"),
        take_profit=data.get("take_profit"),
        reasoning=str(data.get("reasoning", "")),
        invalidation=str(data.get("invalidation", "")),
        evidence=list(data.get("evidence", []) or []),
        raw_text=raw,
        parse_ok=True,
    )


def run_agent(name: str, client: LLMClient, snapshot: MarketSnapshot, extra: str = "") -> AgentView:
    system = SYSTEM_PROMPTS[name]
    user = snapshot.to_prompt_text()
    if extra:
        user += f"\n\n{extra}"
    response = client.complete(system, user)
    try:
        data = parse_json_view(response.text)
        return view_from_data(name, client.provider, client.model, data, response.text)
    except Exception as exc:  # JSON lỗi = không có ý kiến hợp lệ, coi như HOLD
        return AgentView(
            name=name,
            provider=client.provider,
            model=client.model,
            reasoning=f"Lỗi phân tích JSON: {exc}",
            raw_text=response.text,
            parse_ok=False,
        )
