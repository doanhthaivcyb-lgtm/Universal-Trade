from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .agents import AgentView, run_agent
from .config import Config
from .llm_clients import LLMClient, build_client
from .market_snapshot import MarketSnapshot
from .news_fetcher import GOLD_NEWS_KEYWORDS, fetch_news
from .risk import RiskDecision, check_risk
from .sentiment_data import fetch_sentiment

# Theo plan: technical, fundamental, sentiment, risk, portfolio + contrarian
# giữ lại từ bản trước để có phản biện. Chief không nằm trong danh sách này.
ANALYST_NAMES = ["technical", "fundamental", "sentiment", "contrarian", "risk_analyst", "portfolio_manager"]


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


def enrich_snapshot(cfg: Config, snapshot: MarketSnapshot, mock: bool = False) -> MarketSnapshot:
    """Lấy tin tức + sentiment từ nguồn công khai và gắn vào snapshot.

    Chế độ mock thì bỏ qua để không gọi mạng. Lỗi nguồn nào thì ghi rõ
    trong text, không bịa dữ liệu thay thế, và phiên vẫn chạy tiếp.
    """
    if mock:
        return snapshot
    if getattr(cfg, "news_enabled", True):
        try:
            bundle = fetch_news(
                cfg.news_calendar_url, cfg.news_rss_feeds, GOLD_NEWS_KEYWORDS,
                timeout=float(cfg.news_timeout_seconds),
            )
            snapshot.news_text = bundle.to_prompt_text()
            headlines = bundle.headlines
        except Exception as exc:
            snapshot.news_text = f"TIN TỨC: không lấy được tin tức/lịch kinh tế: {exc}"
            headlines = []
    else:
        headlines = []
    if getattr(cfg, "sentiment_enabled", True):
        try:
            sentiment = fetch_sentiment(cfg.sentiment_cot_csv_url, headlines,
                                        timeout=float(cfg.news_timeout_seconds))
            snapshot.sentiment_text = sentiment.to_prompt_text()
        except Exception as exc:
            snapshot.sentiment_text = f"DỮ LIỆU SENTIMENT: không lấy được: {exc}"
    # Tin điền tay trong config vẫn giữ, để trong macro_news riêng.
    return snapshot


def _emit(on_progress, event: dict) -> None:
    """Gửi sự kiện tiến độ cho giao diện, không làm hỏng phiên nếu callback lỗi."""
    if on_progress is None:
        return
    try:
        on_progress(event)
    except Exception:
        pass


def run_room(cfg: Config, snapshot: MarketSnapshot, mock: bool = False, canned: dict | None = None,
             on_progress=None):
    """Chạy 1 phiên: lấy tin/sentiment, vòng 1 độc lập, vòng 2 phản biện, trưởng phòng chốt, cửa rủi ro.

    `on_progress` (tùy chọn) nhận dict sự kiện để giao diện hiện trực tiếp
    từng AI đang làm gì: {"phase": ..., "name": ..., "status": ..., "view": AgentView|None}.
    Callback có thể chạy từ luồng worker, nên giao diện phải tự đưa về luồng UI.
    """
    _emit(on_progress, {"phase": "news", "name": "", "status": "Đang lấy tin tức và sentiment..."})
    snapshot = enrich_snapshot(cfg, snapshot, mock=mock)
    _emit(on_progress, {"phase": "snapshot", "name": "", "status": "Đã có dữ liệu MT5, tin tức và sentiment."})
    clients = build_clients(cfg, mock=mock, canned=canned)

    def _run_one(name: str, extra: str = ""):
        _emit(on_progress, {"phase": "agent_start", "name": name, "status": "Đang phân tích..."})
        view = run_agent(name, clients[name], snapshot, extra=extra)
        _emit(on_progress, {"phase": "agent_done", "name": name, "status": "Đã xong.", "view": view})
        return view

    # Vòng 1: mỗi analyst phân tích độc lập, chạy song song.
    _emit(on_progress, {"phase": "round1", "name": "", "status": "Vòng 1: phân tích độc lập."})
    with ThreadPoolExecutor(max_workers=len(ANALYST_NAMES)) as pool:
        views = list(pool.map(lambda n: _run_one(n), ANALYST_NAMES))

    # Vòng 2: mỗi analyst đọc tóm tắt của các analyst khác và chốt lại ngắn gọn.
    debate_text = "Ý kiến vòng 1 của các AI khác:\n" + "\n".join(_summary(v) for v in views)
    extra = (
        debate_text
        + "\n\nHãy phản hồi ngắn: bạn giữ hay đổi hướng, điểm nào của AI khác khiến bạn nghi ngờ nhất? "
        "Trả về JSON như vòng 1."
    )
    _emit(on_progress, {"phase": "round2", "name": "", "status": "Vòng 2: đọc ý kiến của nhau và phản biện."})
    with ThreadPoolExecutor(max_workers=len(ANALYST_NAMES)) as pool:
        views = list(pool.map(lambda n: _run_one(n, extra), ANALYST_NAMES))

    # Trưởng phòng tổng hợp.
    _emit(on_progress, {"phase": "chief_start", "name": "chief", "status": "Trưởng phòng đang tổng hợp..."})
    chief_input = (
        snapshot.to_prompt_text()
        + "\n\nKết quả vòng 2:\n"
        + "\n".join(_summary(v) for v in views)
    )
    chief_client = clients["chief"]
    chief_response = chief_client.complete(
        "Bạn là trưởng phòng giao dịch. Tổng hợp các phân tích dưới đây và ra 1 quyết định cuối duy nhất. "
        "Nếu mâu thuẫn mạnh hoặc bằng chứng yếu thì chọn HOLD. "
        "entry, stop_loss, take_profit là giá tuyệt đối bằng USD/oz, KHÔNG dùng pips. "
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

    _emit(on_progress, {"phase": "chief_done", "name": "chief", "status": "Trưởng phòng đã chốt.", "view": chief})
    _emit(on_progress, {"phase": "risk", "name": "", "status": "Đang kiểm tra cửa rủi ro cứng..."})
    all_views = views + [chief]
    risk_decision: RiskDecision = check_risk(cfg, snapshot, chief, views)
    _emit(on_progress, {"phase": "done", "name": "", "status": "Đã kiểm tra cửa rủi ro xong."})
    return chief, all_views, risk_decision
