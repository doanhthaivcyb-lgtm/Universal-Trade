# Universal-Trade — Phòng trade AI

Phòng giao dịch AI cho XAUUSD: nhiều AI thảo luận với nhau, mỗi AI phụ trách
một chức năng riêng, một AI trưởng phòng tổng hợp ra một kết luận cuối, rồi
code kiểm tra rủi ro cứng trước khi tự động vào lệnh qua MT5.

Mô tả chính xác cách vận hành, giao diện demo và các chức năng chính xem tại
[MO_TA_VAN_HANH.md](MO_TA_VAN_HANH.md).

## AI trong phòng

- Kỹ thuật: Gemini
- Cơ bản / tin tức: GPT-4o (lịch ForexFactory + RSS Fed)
- Tâm lý: GPT-4o (COT vàng + điểm từ khóa tiêu đề)
- Phản biện: OpenAI
- Quản trị rủi ro (tư vấn): Claude
- Quản lý danh mục: Gemini
- Trưởng phòng tổng hợp: Claude Opus

Mọi entry, stop loss, take profit đều tính bằng USD/oz, không dùng pips.

## App desktop (cách dùng dễ nhất: bấm đúp là mở)

Bấm đúp file `run_app.bat` trong thư mục dự án. App mở thành 1 cửa sổ hiện đầy đủ dữ liệu MT5, tin tức, sentiment, danh mục, từng AI đang làm gì ở từng bước, nút xem cách mỗi AI làm việc, kết luận trưởng phòng, 10 kiểm tra cửa rủi ro, lịch sử phiên và cấu hình an toàn. Muốn có biểu tượng trên màn hình desktop: bấm chuột phải vào `run_app.bat` > Send to > Desktop (create shortcut). Lệnh tương đương: `py -3.13 main.py --app`. Chi tiết xem [MO_TA_VAN_HANH.md](MO_TA_VAN_HANH.md) mục 9.3.

## Giao diện web local (dễ dùng hơn dòng lệnh)

```bash
py -3.13 main.py --web
```

Rồi mở http://127.0.0.1:8000 trong trình duyệt: bấm nút chạy 1 phiên, xem giá và chỉ báo MT5, xem thảo luận và cửa rủi ro, xem lịch sử phiên, sửa giới hạn rủi ro, bấm dừng khẩn cấp. Chỉ chạy trên máy local, không hiện giá trị key, không cho bật live trên web. Chi tiết xem [MO_TA_VAN_HANH.md](MO_TA_VAN_HANH.md) mục 9.2.

## An toàn mặc định

- Chế độ mặc định là `dry_run`: chỉ ghi nhật ký, không đặt lệnh thật.
- Mọi lệnh đều phải qua cửa rủi ro cứng bằng code, luôn có stop loss và
  take profit, tối đa 1 vị thế, lot tính từ mức lỗ tối đa cho phép.
- Key API và mật khẩu MT5 chỉ để trong file `.env` trên máy chạy, không có
  trong repo. Xem `.env.example`.
- Chế độ live cần cả hai lớp: `allow_live: true` trong `config.yaml` và
  `LIVE_TRADING_ENABLED=true` trong `.env`.

## Dữ liệu realtime từ MT5

Dữ liệu lấy trực tiếp từ terminal MT5 đang chạy trên cùng máy Windows, mỗi
phiên lấy mới: tick bid/ask, nến, chỉ báo, vị thế mở, lỗ trong ngày, số dư.
"Snapshot" trong code là bản ghi dữ liệu đó, không phải ảnh chụp màn hình.
File JSON chỉ để chạy thử khi không có MT5.

## Chạy thử không cần MT5 (không tốn phí API, không đặt lệnh)

```bash
pip install -r requirements.txt
python main.py --mock --source json --snapshot data/snapshot_example.json
```

## Chạy với MT5 realtime và key thật (vẫn mặc định dry_run)

```bash
cp .env.example .env
# điền OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY vào .env
# mở terminal MT5 và đăng nhập trước, rồi chạy:
python main.py
# chạy lặp lại theo chu kỳ: python main.py --loop
```

Lưu ý: MT5 chỉ chạy trên Windows. File snapshot ví dụ dùng số liệu minh họa,
không phải giá thật. Chi tiết cấu hình, công thức lot và các việc chưa chốt
xem [MO_TA_VAN_HANH.md](MO_TA_VAN_HANH.md).
