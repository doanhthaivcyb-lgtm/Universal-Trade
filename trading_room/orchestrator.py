from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .agents import AgentView, run_agent
from .config import Config
from .llm_clients import LLMClient, build_client
from .market_snapshot import MarketSnapshot
from .risk import RiskDecision, check_risk

ANALYST_NAMES = ["technical", "macro", "sentiment", "contrarian", "risk_analyst"]


def build_clients(cfg: Config, mock: bool = False, canned: dict | None = None) -> dict[str, LLMClient]:
    clients: dict[str, LLMClient] = {}
    for name, agent_cfg in cfg.agents.items():
        provider = agent_cfg["provider"]
        settings = cfg.providers.get(provider, {})
        clients[name] = build_client(provider, settings, mock=mock, canned=canned)
    return clients


def _summary(view: AgentView) -> str:
    return (
        f"- {view.name} ({view.provider}/{view.model}): {view.direction} "
        f"tin cậy {view.confidence}, SL={view.stop_loss}, TP={view.take_profit}. "
        f"Lý do: {view.reasoning} Điểm vô hiệu: {view.invalidation}"
    )


def run_room(cfg: Config, snapshot: MarketSnapshot, mock: bool = False, canned: dict | None = None):
    """Chạy 1 phiên: vòng 1 độc lập, vòng 2 phản biện, trưởng phòng chốt, cửa rủi ro."""
    clients = build_clients(cfg, mock=mock, canned=canned)

    # Vòng 1: mỗi analyst phân tích độc lập, chạy song song.
    with ThreadPoolExecutor(max_workers=len(ANALYST_NAMES)) as pool:
        views = list(pool.map(lambda n: run_agent(n, clients[n], snapshot), ANALYST_NAMES))

    # Vòng 2: mỗi analyst đọc tóm tắt của các analyst khác và chốt lại ngắn gọn.
    debate_text = "Ý kiến vòng 1 của các AI khác:\n" + "\n".join(_summary(v) for v in views)
    extra = (
        debate_text
        + "\n\nHãy phản hồi ngắn: bạn giữ hay đổi hướng, điểm nào của AI khác khiến bạn nghi ngờ nhất? "
        "Trả về JSON như vòng 1."
    )
    with ThreadPoolExecutor(max_workers=len(ANALYST_NAMES)) as pool:
        views = list(pool.map(lambda n: run_agent(n, clients[n], snapshot, extra=extra), ANALYST_NAMES))

    # Trưởng phòng tổng hợp.
    chief_input = (
        snapshot.to_prompt_text()
        + "\n\nKết quả vòng 2:\n"
        + "\n".join(_summary(v) for v in views)
    )
    chief_client = clients["chief"]
    chief_response = chief_client.complete(
        "Bạn là trưởng phòng giao dịch. Tổng hợp các phân tích dưới đây và ra 1 quyết định cuối duy nhất. "
        "Nếu mâu thuẫn mạnh hoặc bằng chứng yếu thì chọn HOLD. "
        'Trả về DUY NHẤT 1 JSON: {"direction": "BUY|SELL|HOLD", "confidence": 0.0-1.0, '
        '"entry": số hoặc null, "stop_loss": số hoặc null, "take_profit": số hoặc null, '
        '"reasoning": "...", "invalidation": "...", "evidence": [...]}',
        chief_input,
    )
    from .agents import parse_json_view, view_from_data

    try:
        chief_data = parse_json_view(chief_response.text)
        chief = view_from_data("chief", chief_client.provider, chief_client.model, chief_data, chief_response.text)
    except Exception as exc:
        chief = AgentView(name="chief", provider=chief_client.provider, model=chief_client.model,
                          reasoning=f"Lỗi JSON trưởng phòng: {exc}", raw_text=chief_response.text, parse_ok=False)

    all_views = views + [chief]
    risk_decision: RiskDecision = check_risk(cfg, snapshot, chief, views)
    return chief, all_views, risk_decision
