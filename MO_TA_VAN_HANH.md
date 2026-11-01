# Phòng trade AI — Mô tả vận hành, giao diện demo và các chức năng chính

- **Phiên bản:** v1.2 theo plan `ai_trading_room_plan.md` (thêm News, Sentiment, Portfolio Manager; TP chỉ tính bằng USD, không dùng pips), làm ở local ngày 2026-11-01, chưa đẩy lên repo trong lần sửa này.
- **Thị trường:** XAUUSD, khung chính tạm là M15 trong `config.yaml`, chờ Thái chốt M15 hay H1.
- **Nhà cung cấp AI đã chốt:** OpenAI + Gemini + Claude (Thái nói có đủ key ngày 2026-11-01). Phân vai và model theo plan, xem mục 3.
- **Đơn vị giá bắt buộc:** mọi entry, stop loss, take profit đều là giá tuyệt đối tính bằng USD/oz. KHÔNG dùng pips, KHÔNG dùng điểm.
- **Nguyên tắc an toàn:** mặc định chỉ phân tích và chạy thử (`dry_run`). AI chỉ tư vấn;
  lot, stop loss, điều kiện vào lệnh cuối cùng do **code** quyết định qua cửa rủi ro cứng.
  Không có key, mật khẩu hay thông tin tài khoản nào trong code hay trong file này.

---

## 1. Mục đích và nguyên tắc

1. Nhiều AI thảo luận với nhau như một phòng giao dịch thật, mỗi AI phụ trách một
   chức năng riêng, rồi một AI trưởng phòng tổng hợp ra **một** kết luận cuối.
2. Hệ thống có thể tự động vào lệnh qua MT5, nhưng chỉ khi kết luận đó qua được
   cửa rủi ro cứng viết bằng code, và chỉ ở chế độ Thái đã bật rõ ràng.
3. Mọi phiên đều lưu lại toàn bộ ý kiến, kết luận và lý do chặn/cho phép, để
   kiểm tra lại sau. Không có phiên nào là "hộp đen".
4. Khi bằng chứng yếu, các AI mâu thuẫn, JSON lỗi, spread giãn, hoặc đã chạm
   giới hạn lỗ trong ngày thì kết quả mặc định là **HOLD — không vào lệnh**.

---

## 2. Tổng quan kiến trúc

```
Snapshot thị trường (1 bản duy nhất)
        |
        v
Vòng 1: 6 AI phân tích ĐỘC LẬP, chạy song song
  technical (Gemini) | fundamental (GPT-4o) | sentiment (GPT-4o)
  contrarian (OpenAI) | risk_analyst (Claude) | portfolio_manager (Gemini)
        |
        v
Vòng 2: 6 AI đọc tóm tắt của nhau, phản biện và chốt lại
        |
        v
Trưởng phòng (Claude) tổng hợp -> 1 JSON quyết định cuối
        |
        v
Cửa rủi ro CỨNG bằng code (không phải AI) -> CHO PHÉP / CHẶN
        |
        v
Executor: dry_run (ghi nhật ký) | demo (MT5 demo) | live (MT5 thật, 2 lớp khóa)
        |
        v
Nhật ký phiên: output/session_<thời gian>.json + .md
```

Ba lớp tách biệt rõ ràng:

| Lớp | Nằm ở | AI có quyết được không? |
|---|---|---|
| Thảo luận | `trading_room/agents.py`, `orchestrator.py` | AI chỉ đưa ý kiến dạng JSON |
| Rủi ro cứng | `trading_room/risk.py` | Không. Code kiểm tra, AI không vượt qua được |
| Vào lệnh | `trading_room/executor.py` | Không. Chỉ đặt lệnh khi cửa rủi ro cho phép |

---

## 3. Các AI trong phòng

Tất cả các AI đều nhận **cùng một snapshot** (mục 6). Không AI nào được tự bịa
giá, chỉ báo hay tin tức ngoài snapshot.

| # | AI | Nhà cung cấp / model mặc định | Nhiệm vụ chính | Không làm |
|---|---|---|---|---|
| 1 | `technical` | Gemini / `gemini-2.0-flash` | Xu hướng, cấu trúc giá, hỗ trợ/kháng cự, RSI, MACD, ATR, EMA, Bollinger | Không đọc tin vĩ mô |
| 2 | `fundamental` | OpenAI / `gpt-4o` | Fed, USD/DXY, lợi suất, CPI, NFP, địa chính trị; chỉ dùng tin và lịch đã lấy được | Không suy đoán tin chưa công bố, không bịa số liệu |
| 3 | `sentiment` | OpenAI / `gpt-4o` | COT vàng và điểm từ khóa tiêu đề tin tức, cộng hành vi giá trong phiên | Không tưởng điểm từ khóa là Fear & Greed thật |
| 4 | `contrarian` | OpenAI / `gpt-4o` | Tìm điểm yếu của phe đa số, kịch bản ngược chiều, điểm vô hiệu (giữ thêm ngoài plan) | Không đồng thuận cho có |
| 5 | `risk_analyst` | Claude / `claude-sonnet-4-5` | Tư vấn rủi ro: spread, biến động, giờ tin, hậu quả nếu stop bị quét | **Không quyết lot** — code quyết |
| 6 | `portfolio_manager` | Gemini / `gemini-2.0-flash` | Exposure toàn tài khoản, tập trung vào vàng, rủi ro cộng thêm 1 lệnh | **Không quyết lot** — code quyết |
| 7 | `chief` (trưởng phòng) | Claude Opus / `claude-opus-4-8` | Tổng hợp 6 ý kiến, ra **1 quyết định cuối duy nhất**, TP/SL bằng USD | Không bỏ qua ý phản biện và ý danh mục |

Cấu hình provider/model/temperature nằm ở `config.yaml`, mục `providers` và
`agents`. Đổi model chỉ cần sửa `config.yaml`, không cần sửa code agent.
Temperature mặc định `0.2` và `max_tokens` mặc định `800` cho mọi AI để câu
trả lời ổn định và ngắn gọn, đúng dạng JSON.

---

## 4. Cách vận hành từng bước

### Bước 0 — Lấy dữ liệu realtime trực tiếp từ MT5 (đầu vào duy nhất)

"Snapshot" ở đây **không phải ảnh chụp màn hình**. Nó là một bản ghi dữ liệu
thị trường tại một thời điểm, được tạo mới trực tiếp từ terminal MT5 ngay
trước mỗi phiên (xem mục 6 và `trading_room/mt5_data.py`): tick bid/ask mới
nhất, nến gần nhất, chỉ báo tính từ nến đó, số vị thế đang mở, lỗ trong ngày
và số dư tài khoản.

- Không nhập tay giá, không đọc file JSON thủ công khi chạy thật.
- Mọi AI trong cùng một phiên đều đọc đúng bản ghi này, nên không có chuyện
  AI này thấy giá khác AI kia.
- File JSON (`data/snapshot_example.json`) chỉ còn để chạy thử khi không có MT5.

### Bước 1 — Vòng 1: phân tích độc lập

- Hàm `run_room()` trong `orchestrator.py` gọi 5 analyst cùng lúc bằng
  `ThreadPoolExecutor`, mỗi AI một client riêng (OpenAI/Gemini/Claude).
- Mỗi AI nhận system prompt riêng theo vai trò (trong `SYSTEM_PROMPTS`,
  `agents.py`) và cùng một user prompt là nội dung snapshot.
- Mỗi AI phải trả về **duy nhất 1 JSON** (định dạng ở mục 5). Không giải thích
  ngoài JSON.
- Nếu một AI trả về JSON lỗi hoặc thiếu trường bắt buộc, hàm `parse_json_view()`
  coi ý kiến đó là không hợp lệ: `parse_ok=False`, hướng mặc định `HOLD`,
  và lý do lỗi được ghi vào nhật ký.

### Bước 2 — Vòng 2: tranh luận và phản biện

- Hệ thống tóm tắt 5 ý kiến vòng 1 thành danh sách ngắn (hướng, độ tin cậy,
  SL/TP, lý do, điểm vô hiệu) và gửi lại cho cả 5 AI.
- Mỗi AI trả lời: giữ hay đổi hướng, và điểm nào của AI khác khiến mình nghi
  ngờ nhất. Vẫn trả về JSON như vòng 1.
- Vòng 2 luôn chạy, không chỉ chạy khi bất đồng, để phản biện được ghi lại
  thành văn bản trong nhật ký.

### Bước 3 — Trưởng phòng tổng hợp

- AI `chief` (Claude) nhận snapshot + toàn bộ ý kiến vòng 2, rồi trả về 1 JSON
  quyết định cuối: `BUY`, `SELL` hoặc `HOLD`, kèm độ tin cậy, entry, stop loss,
  take profit, lý do, điểm vô hiệu và bằng chứng.
- Quy tắc bắt buộc trong prompt của trưởng phòng: nếu mâu thuẫn mạnh hoặc bằng
  chứng yếu thì chọn `HOLD`.
- Nếu JSON của trưởng phòng lỗi thì toàn bộ phiên bị coi là không có quyết định
  hợp lệ và **không vào lệnh** (cửa rủi ro chặn ở bước 4).

### Bước 4 — Cửa rủi ro cứng bằng code

- Hàm `check_risk()` trong `risk.py` kiểm tra 9 điều kiện theo thứ tự (mục 7).
  Chỉ cần 1 điều kiện trượt là chặn, không có ngoại lệ, không có "AI xin phép".
- Nếu qua được, code tính lot từ mức lỗ tối đa cho phép và khoảng cách stop loss
  của trưởng phòng (công thức ở mục 7). AI không được tự chọn lot.
- Kết quả bước này là `RiskDecision(allowed, reason, lot, sl_distance)`.

### Bước 5 — Vào lệnh (hoặc không vào lệnh)

- Nếu cửa rủi ro chặn: in lý do, lưu nhật ký, dừng. Không gọi executor.
- Nếu cho phép: `get_executor()` chọn executor theo `mode` trong `config.yaml`
  (mục 8), đặt 1 lệnh market duy nhất, luôn kèm stop loss, take profit và
  `magic_number` để nhận diện lệnh của phòng trade.
- Tối đa 1 vị thế mở cùng lúc theo cấu hình mặc định (`max_open_positions: 1`).
  Không có DCA, không cộng thêm lệnh, không gỡ lệnh trong v1.

### Bước 6 — Lưu nhật ký và kết thúc phiên

- Hàm `save_session()` trong `journal.py` lưu 2 file cho mỗi phiên vào `output/`:
  `session_<YYYYMMDD_HHMMSS>.json` (đầy đủ, kể cả câu trả lời thô của từng AI)
  và `session_<...>.md` (bản đọc nhanh cho người).
- Mặc định chạy **1 phiên rồi dừng** (`python main.py`). Dùng `python main.py --loop`
  để chạy lặp lại theo `loop_interval_seconds` trong `config.yaml` (mặc định
  900 giây = M15); mỗi vòng lặp lấy lại dữ liệu MT5 mới hoàn toàn, không dùng
  lại giá của phiên trước. Dừng bằng Ctrl+C hoặc tạo file `STOP`.

---

## 5. Định dạng JSON bắt buộc của mọi AI

```json
{
  "direction": "BUY",
  "confidence": 0.72,
  "entry": 4000.2,
  "stop_loss": 3992.0,
  "take_profit": 4016.0,
  "reasoning": "Lý do ngắn gọn, dựa trên snapshot.",
  "invalidation": "Điều kiện làm phân tích này sai.",
  "evidence": ["Bằng chứng 1 trong snapshot", "Bằng chứng 2 trong snapshot"]
}
```

- `direction` chỉ nhận `BUY`, `SELL`, `HOLD`. Giá trị khác là JSON không hợp lệ.
- `confidence` là số từ `0.0` đến `1.0`.
- `entry`, `stop_loss`, `take_profit` là giá tuyệt đối bằng USD/oz, hoặc `null` khi chọn `HOLD`. KHÔNG dùng pips, theo yêu cầu của Thái ngày 2026-11-01. Code chặn thêm trường hợp SL/TP quá xa giá hiện tại, vì đó là dấu hiệu AI đã trả về khoảng cách/pips thay vì giá USD.
- AI được dặn trong system prompt: "Chỉ dùng số liệu trong snapshot. Không bịa
  tin, không bịa chỉ báo. Không chắc thì HOLD."

---

## 6. Nguồn dữ liệu: kết nối trực tiếp MT5 realtime

### 6.1. "Snapshot" nghĩa là gì — và không phải là gì

Snapshot **không phải ảnh chụp màn hình**, AI cũng không nhìn màn hình MT5.
Snapshot là một bản ghi dữ liệu (object `MarketSnapshot`) được tạo ra trong
code tại thời điểm bắt đầu phiên, gồm các trường dưới đây. Bản ghi này là
thứ duy nhất các AI được đọc.

### 6.2. Cách lấy realtime từ MT5

`trading_room/mt5_data.py`, lớp `MT5DataProvider`:

1. Kết nối tới terminal MT5 đang chạy trên cùng máy Windows bằng thư viện
   `MetaTrader5` (Python). Nếu đã đăng nhập sẵn trong terminal thì không cần
   điền login; nếu chưa thì điền `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
   trong `.env`.
2. Chọn đúng symbol trong `config.yaml` (phải khớp đúng tên trên MT5, ví dụ
   `XAUUSD.vx` ở Valetax) bằng `symbol_select()`.
3. Lấy tick mới nhất bằng `symbol_info_tick()` → `bid`, `ask`, spread.
4. Lấy 200 nến gần nhất của khung trong config bằng `copy_rates_from_pos()`,
   rồi tính chỉ báo trong code: RSI14, ATR14, EMA20, EMA50, MACD(12,26,9),
   hỗ trợ/kháng cự là đỉnh/đáy cao nhất/thấp nhất 100 nến và 20 nến gần nhất.
5. Lấy vị thế đang mở bằng `positions_get()`, số dư bằng `account_info()`,
   lỗ trong ngày bằng `history_deals_get()` từ 0 giờ hôm nay (lỗ đã chốt +
   lỗ đang nổi; trả về số dương khi đang lỗ, 0 khi đang lãi).
6. Ghi lại `source="mt5"` và `captured_at` (thời điểm lấy dữ liệu) vào bản ghi,
   để nhật ký cho thấy dữ liệu cũ hay mới.

Mỗi phiên trong chế độ `--loop` đều gọi lại bước này từ đầu, nên không bao
giờ dùng lại giá cũ.

### 6.3. Các trường của bản ghi dữ liệu

| Trường | Lấy từ đâu (chế độ MT5) |
|---|---|
| `symbol`, `timeframe` | `config.yaml` |
| `bid`, `ask` | Tick realtime `symbol_info_tick()` |
| `indicators` | Tính từ nến MT5: RSI, MACD, ATR, EMA, hỗ trợ/kháng cự |
| `macro_news` | `config.yaml` — MT5 không có tin văn bản, điền tay nếu muốn AI vĩ mô đọc |
| `open_positions` | `positions_get()` trên MT5 |
| `daily_loss_usd` | `history_deals_get()` hôm nay + lỗ đang nổi |
| `account_balance` | `account_info()` trên MT5 |

### 6.4. Chế độ JSON chỉ để chạy thử

`data/snapshot_example.json` dùng số liệu minh họa, **không phải giá thật**,
chỉ để chạy `python main.py --mock --source json`. Khi chạy thật, luôn dùng
nguồn MT5 mặc định.

### 6.5. Hạn chế, nói thẳng

- Thư viện `MetaTrader5` chỉ chạy trên Windows và cần terminal MT5 đang mở,
  đã đăng nhập. Trên Linux/Mac, code báo lỗi rõ ràng và không chạy.
- Dữ liệu là realtime tại thời điểm bắt đầu phiên. Trong lúc AI thảo luận
  (hàng chục giây), giá có thể chạy. Executor kiểm tra lại tick mới nhất
  trước khi đặt lệnh và hủy lệnh nếu giá chạy quá `max_price_move_usd`
  (mặc định 5 USD) so với giá lúc thảo luận — xem mục 8.
- MT5 không cung cấp tin tức văn bản, lịch kinh tế hay DXY/lợi suất. Phần tin
  tức và sentiment lấy tự động được mô tả ở mục 6.6; nếu nguồn lỗi thì AI đó
  phải trả về HOLD hoặc phân tích dựa trên giá, không được bịa tin.

### 6.6. Tin tức và sentiment lấy tự động (v1.2, theo plan)

Tin tức và sentiment không lấy từ MT5, mà lấy từ nguồn công khai qua
`trading_room/news_fetcher.py` và `trading_room/sentiment_data.py`, ngay trước
mỗi phiên (trừ chế độ `--mock`), rồi gắn vào snapshot:

- **Lịch kinh tế:** file XML tuần này của ForexFactory
  (`https://nfs.faireconomy.media/ff_calendar_thisweek.xml`). Chỉ giữ sự kiện
  trong 48 giờ tới, ưu tiên USD và ảnh hưởng HIGH. Giờ hiển thị đúng như nguồn
  ghi, không tự đổi múi giờ.
- **Tiêu đề tin:** RSS của Fed (`https://www.federalreserve.gov/feeds/press_all.xml`),
  ưu tiên tiêu đề có từ khóa vàng/Fed/CPI/NFP/USD. Chỉ lấy tiêu đề, không tự
  diễn giải nội dung bài viết.
- **COT vàng:** file CSV của FuturesBench (dữ liệu CFTC public domain,
  `https://futuresbench.com/data/cot/gold.csv`): vị thế ròng nhóm phi thương mại,
  thay đổi tuần trước, vị trí phần trăm trong 52 tuần. Báo cáo chốt thứ Ba,
  công bố thứ Sáu, nên luôn cũ vài ngày — code ghi rõ ngày báo cáo.
- **Điểm từ khóa tiêu đề:** chỉ đếm từ tăng/giảm trong tiêu đề đã lấy được.
  Đây KHÔNG phải Fear & Greed Index thật, không phải khảo sát tâm lý.

Nguồn nào lỗi hoặc đổi định dạng thì phần đó ghi rõ "không lấy được", phiên
vẫn chạy tiếp, AI liên quan phải hạ độ tin cậy hoặc HOLD. Không bịa tin,
không bịa COT, không bịa sentiment để lấp chỗ trống. Không cần key API cho
3 nguồn mặc định này.

---

## 7. Cửa rủi ro cứng — 10 kiểm tra theo thứ tự

Hàm `check_risk(cfg, snapshot, chief, views)` trong `risk.py`. Trượt 1 là chặn.

| # | Kiểm tra | Giá trị mặc định (`config.yaml`) |
|---|---|---|
| 1 | Có file công tắc dừng `STOP` trong thư mục dự án không | `kill_switch_file: "STOP"` |
| 2 | Chế độ chạy hợp lệ (`dry_run` / `demo` / `live`) | `mode: "dry_run"` |
| 3 | Spread không vượt giới hạn | `max_spread_usd: 0.50` |
| 4 | Số vị thế đang mở dưới giới hạn | `max_open_positions: 1` |
| 5 | Lỗ trong ngày chưa chạm giới hạn | `max_daily_loss_usd: 2000.0` |
| 6 | Trưởng phòng không HOLD, JSON hợp lệ, độ tin cậy đủ | `min_confidence: 0.65` |
| 7 | Đồng thuận: nếu đa số analyst ngược hướng trưởng phòng và tin cậy < 0.8 thì chặn | Ngưỡng 0.8 viết trong code |
| 8 | SL/TP hợp lệ: khoảng cách SL trong giới hạn, tỉ lệ lãi/lỗ tối thiểu đạt | SL 3–30 USD, `min_reward_risk: 1.0` |
| 9 | Lỗ ngày + mức lỗ tối đa của lệnh này không vượt giới hạn ngày | Cùng giới hạn ở #5 |
| 10 | Exposure danh mục: danh mục hiện tại + lệnh mới không vượt giới hạn | `max_portfolio_notional_usd: 1000000.0` |

### Công thức tính lot

```
lot_thô = max_risk_usd_per_trade / (khoảng_cách_SL_USD × contract_size)
lot     = làm tròn xuống theo lot_step, kẹp trong [min_lot, max_lot_per_order]
```

- `contract_size` mặc định `100.0` (1 lot XAUUSD = 100 oz, 1 USD biến động = 100 USD/lot).
- Ví dụ: mức lỗ tối đa 500 USD, SL cách giá 10 USD thì
  `lot = 500 / (10 × 100) = 0.50` lot. Nếu tính ra 2.0 lot thì bị kẹp về
  trần `1.5` lot. Nếu tính ra dưới `0.01` lot thì chặn, không đặt lệnh.

---

## 8. Chế độ chạy và khóa an toàn

| Chế độ (`mode`) | Executor | Lệnh thật? | Điều kiện thêm |
|---|---|---|---|
| `dry_run` (mặc định) | `DryRunExecutor` | Không bao giờ | Không cần MT5 |
| `demo` | `MT5Executor` | Chỉ tài khoản demo | Code kiểm tra `account_info().trade_mode` phải là demo, nếu không thì chặn |
| `live` | `MT5Executor` | Có | **Cần cả 2 lớp:** `allow_live: true` trong `config.yaml` **và** `LIVE_TRADING_ENABLED=true` trong `.env`. Thiếu 1 lớp là chặn |

Các khóa khác:

- **File `STOP`:** tạo file tên `STOP` trong thư mục dự án là mọi lệnh dừng ngay
  ở kiểm tra #1, không cần sửa code hay config. Xóa file là chạy lại.
- **Key và mật khẩu:** chỉ đọc từ biến môi trường / file `.env`. File `.env`
  đã có trong `.gitignore` và không được đẩy lên repo. File mẫu là `.env.example`.
- **MT5 chỉ chạy trên Windows:** thư viện `MetaTrader5` chỉ cài được trên
  Windows. Trên Linux/Mac, `MT5Executor` tự trả về lỗi rõ ràng và không đặt lệnh.
  Thông tin đăng nhập MT5 (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`) điền
  trong `.env` trên máy chạy.
- **Kiểm tra giá mới trước khi đặt lệnh:** executor lấy lại tick realtime và hủy
  lệnh nếu giá chạy quá `max_price_move_usd` (mặc định 5 USD) so với giá lúc
  AI thảo luận.

---

## 9. Giao diện demo

### 9.1. Giao diện demo v1: dòng lệnh (CLI) — đã code xong

Đây là giao diện demo thật sự của v1. Không có giao diện web trong v1.

**Chạy thử không cần MT5, không tốn phí API, không đặt lệnh:**

```bash
pip install -r requirements.txt
python main.py --mock --source json --snapshot data/snapshot_example.json
```

**Chạy với dữ liệu realtime từ MT5, AI giả (trên Windows, MT5 đang mở):**

```bash
python main.py --mock
```

**Chạy với key AI thật và dữ liệu MT5 thật (vẫn mặc định `dry_run`, không đặt lệnh thật):**

```bash
cp .env.example .env   # điền OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY
python main.py
```

**Chạy lặp lại theo chu kỳ:**

```bash
python main.py --loop
```

**Luồng thao tác demo:**

1. Mở terminal MT5 trên Windows, đăng nhập tài khoản, để symbol trong config
   hiện giá. Không cần nhập tay giá hay ảnh chụp màn hình.
2. Gõ lệnh trên. Màn hình in lần lượt: nguồn dữ liệu và thời điểm lấy dữ liệu,
   giá và spread, từng ý kiến AI, kết luận trưởng phòng, kết quả cửa rủi ro,
   và thông báo lệnh (hoặc lý do không vào lệnh).
3. Mở file nhật ký trong `output/` để đọc toàn bộ cuộc thảo luận (mục 4, bước 6).

**Ví dụ đầu ra trên màn hình (dạng minh họa, số liệu tùy phiên):**

```
Giá: 4000.22  Spread: 0.25
- technical (openai): BUY tin cậy 0.7 — ...
- macro (anthropic): HOLD tin cậy 0.55 — ...
- sentiment (gemini): BUY tin cậy 0.68 — ...
- contrarian (openai): SELL tin cậy 0.6 — ...
- risk_analyst (anthropic): HOLD tin cậy 0.5 — ...
Trưởng phòng: BUY tin cậy 0.7
Cửa rủi ro: CHO PHÉP — Qua cửa rủi ro. (lot 0.5)
[DRY-RUN] BUY 0.5 lot XAUUSD SL=3992.0 TP=4016.0 — không đặt lệnh thật.
Đã lưu nhật ký phiên: output/session_20261101_040000.json
```

### 9.2. Giao diện demo web — bản thiết kế, chưa code

Phần này mô tả chính xác giao diện sẽ làm tiếp nếu Thái muốn xem trực quan thay
vì dòng lệnh. **Chưa có code web trong v1**, tránh hiểu nhầm là đã chạy được.

Bố cục 1 màn hình, chia 4 vùng:

1. **Vùng trên cùng — Trạng thái phiên:** symbol, khung, giá bid/ask, spread,
   chế độ chạy (`DRY-RUN` / `DEMO` / `LIVE`), nút `Chạy 1 phiên`, nút
   `DỪNG KHẨN CẤP` (tạo file `STOP`), và đồng hồ đếm thời gian gọi AI.
2. **Vùng trái — Dữ liệu MT5 realtime:** bảng chỉ báo (RSI, MACD, ATR, EMA,
   hỗ trợ/kháng cự) cập nhật mỗi phiên, ô tin vĩ mô (điền tay trong config),
   số vị thế mở, lỗ trong ngày, và thời điểm lấy dữ liệu gần nhất.
3. **Vùng giữa — Phòng thảo luận:** 5 thẻ AI xếp dọc, mỗi thẻ hiện tên, provider,
   hướng, độ tin cậy, lý do ngắn. Bên dưới là khung chat tranh luận vòng 2.
   Khi đang chạy, thẻ nào đang gọi API thì hiện trạng thái "đang phân tích...".
4. **Vùng phải — Kết luận và lệnh:** thẻ trưởng phòng (hướng, tin cậy, entry,
   SL, TP, điểm vô hiệu), bảng 9 kiểm tra rủi ro (từng dòng ĐẠT/CHẶN kèm lý do),
   lot tính ra, nút `Xác nhận đặt lệnh` chỉ hiện khi cửa rủi ro cho phép và
   chế độ không phải `dry_run`. Bên dưới là lịch sử các phiên đã chạy.

Trạng thái hiển thị bắt buộc: đang lấy dữ liệu MT5, đang gọi AI, đang kiểm tra rủi
ro, đã xong (vào lệnh / HOLD / bị chặn kèm lý do), và lỗi (mất kết nối MT5, mất mạng, hết key,
JSON lỗi). Không có trạng thái nào tự động đặt lệnh mà không hiện rõ lý do.

---

## 10. Các chức năng chính

| Chức năng | Làm gì | Nằm ở |
|---|---|---|
| Gọi nhiều AI | Gọi OpenAI, Gemini, Claude qua HTTP, mỗi provider một client riêng, không dùng SDK nặng | `llm_clients.py`: `OpenAIClient`, `GeminiClient`, `ClaudeClient`, `build_client()` |
| Chạy thử không tốn phí | `MockClient` trả về JSON mẫu, dùng để test cấu trúc và cửa rủi ro | `llm_clients.py`: `MockClient` |
| Phân tích theo vai trò | Prompt riêng cho từng vai trò, bắt buộc trả JSON đúng dạng | `agents.py`: `SYSTEM_PROMPTS`, `run_agent()` |
| Đọc JSON an toàn | Lấy khối JSON đầu tiên, kiểm tra `direction` hợp lệ, lỗi thì coi là HOLD | `agents.py`: `parse_json_view()`, `view_from_data()` |
| Điều phối phòng trade | Vòng 1 song song, vòng 2 phản biện, trưởng phòng chốt | `orchestrator.py`: `run_room()`, `build_clients()` |
| Lấy dữ liệu realtime từ MT5 | Tick, nến, chỉ báo, vị thế, lỗ ngày, số dư trực tiếp từ MT5 | `mt5_data.py`: `MT5DataProvider.build_snapshot()`, `connect()`, `close()` |
| Tính chỉ báo cơ bản | EMA, RSI, ATR, MACD từ nến MT5 | `market_snapshot.py`: `ema()`, `rsi()`, `atr()`, `macd()` |
| Đọc file thử | Đọc file JSON thành `MarketSnapshot`, tính giá giữa và spread, chỉ để chạy thử | `market_snapshot.py`: `load_snapshot()` |
| Lấy tin tức và lịch | RSS Fed + XML lịch ForexFactory, lọc USD/HIGH, không bịa khi lỗi | `news_fetcher.py`: `fetch_news()` |
| Lấy sentiment | COT vàng (CSV FuturesBench/CFTC) + điểm từ khóa tiêu đề | `sentiment_data.py`: `fetch_sentiment()` |
| Tính exposure danh mục | Tổng giá trị danh nghĩa mọi vị thế đang mở từ MT5 | `mt5_data.py`: `_portfolio()`, `risk.py`: `portfolio_notional_usd()` |
| Cửa rủi ro cứng | 10 kiểm tra theo thứ tự, tính lot | `risk.py`: `check_risk()`, `calculate_lot()` |
| Đặt lệnh MT5 | Đặt lệnh market có SL/TP/magic, chặn nếu tài khoản không phải demo ở chế độ demo, chặn live nếu thiếu khóa | `executor.py`: `MT5Executor.place()` |
| Chạy thử không lệnh | Ghi lại lệnh đáng lẽ đặt, không chạm MT5 | `executor.py`: `DryRunExecutor.place()` |
| Lưu nhật ký phiên | Lưu JSON đầy đủ + Markdown đọc nhanh cho mỗi phiên | `journal.py`: `save_session()` |
| Nạp cấu hình | Đọc `config.yaml` + `.env`, xác định có được phép live không | `config.py`: `load_config()`, `Config.live_enabled` |
| Chạy 1 phiên từ CLI | Ghép tất cả các bước: đọc config, snapshot, chạy phòng, kiểm tra rủi ro, đặt lệnh, lưu nhật ký | `main.py`: `main()` |

---

## 11. Cấu trúc thư mục

```
ai_trading_room/
  MO_TA_VAN_HANH.md          # file này
  README.md                  # hướng dẫn ngắn, trỏ tới file mô tả vận hành này
  config.yaml                # cấu hình: symbol, mode, provider, rủi ro
  .env.example               # mẫu biến môi trường, không chứa key thật
  .gitignore                 # chặn .env, output/, STOP, __pycache__
  requirements.txt           # httpx, pyyaml (MetaTrader5 chỉ Windows, tùy chọn)
  main.py                    # CLI chạy 1 phiên
  data/
    snapshot_example.json    # snapshot ví dụ, số liệu minh họa
  output/                    # nhật ký từng phiên, tự tạo khi chạy
  trading_room/
    __init__.py
    config.py
    llm_clients.py
    market_snapshot.py
    mt5_data.py              # kết nối MT5 realtime, tạo MarketSnapshot
    news_fetcher.py          # lịch ForexFactory + RSS Fed
    sentiment_data.py        # COT vàng + điểm từ khóa tiêu đề
    agents.py
    orchestrator.py
    risk.py
    executor.py
    journal.py
```

---

## 12. Cấu hình quan trọng

`config.yaml` (giá trị hiện tại là mặc định chạy thử):

- `symbol: "XAUUSD"`, `timeframe: "M15"`, `data_source: "mt5"`, `mode: "dry_run"`, `allow_live: false`.
- `providers`: model, temperature, max_tokens cho từng nhà cung cấp.
- `agents`: agent nào dùng provider nào (bảng ở mục 3).
- `risk`: mức lỗ tối đa 500 USD/lệnh, lỗ ngày tối đa 2.000 USD, lot tối thiểu
  0.01, tối đa 1.5, SL 3–30 USD, tỉ lệ lãi/lỗ tối thiểu 1.0, spread tối đa 0.50 USD.

`.env` (người chạy tự tạo từ `.env.example`, không đẩy lên repo):

- `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`.
- `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` — chỉ khi chạy MT5.
- `LIVE_TRADING_ENABLED=false` — mặc định tắt.

---

## 13. Giới hạn và rủi ro đã biết

1. AI có thể sai, bịa luận điểm, hoặc hiểu sai dữ liệu. Vì vậy dữ liệu MT5 phải
   đúng, cửa rủi ro cứng phải luôn bật, và v1 chỉ nên chạy demo trước.
2. Gọi 3 nhà cung cấp cùng lúc tốn phí API và mất hàng chục giây mỗi phiên.
   Không phù hợp lướt sóng M1, chỉ phù hợp khung M15/H1 trở lên.
3. v1 lấy giá live từ MT5 nhưng không theo dõi lệnh sau khi vào, không dời stop,
   không đóng lệnh sớm, không quản lý lệnh đang mở ngoài việc đếm số vị thế
   trong mỗi phiên mới.
4. Nếu key của một provider hết hạn hoặc lỗi mạng, AI đó trả về lỗi, ý kiến đó
   bị coi là HOLD, và phiên vẫn chạy tiếp với các AI còn lại — nhưng nếu
   trưởng phòng lỗi thì không vào lệnh.
5. Chế độ `live` là rủi ro tiền thật. Chỉ bật sau khi đã chạy demo đủ lâu,
   đã kiểm tra nhật ký, và Thái xác nhận riêng bằng văn bản trong cuộc trò
   chuyện này, không bật chỉ bằng cách sửa một mình file config.

---

## 14. Việc chưa chốt — cần Thái quyết trước khi chạy thật

1. Khung chính: M15 hay H1 (hiện tạm để M15).
2. Tài khoản chạy: demo hay thật, trên broker/symbol nào (hiện symbol để
   `XAUUSD`, nếu broker là Valetax thì có thể phải đổi thành `XAUUSD.vx`).
3. Mức lỗ tối đa mỗi lệnh và mỗi ngày (hiện tạm 500 USD / 2.000 USD trên vốn
   100.000 USD).
4. Có làm giao diện web ở mục 9.2 không, hay chỉ dùng CLI ở mục 9.1.
