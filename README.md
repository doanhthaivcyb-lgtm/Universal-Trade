# Universal-Trade — Phòng trade AI

Phòng giao dịch AI cho XAUUSD: nhiều AI thảo luận với nhau, mỗi AI phụ trách
một chức năng riêng, một AI trưởng phòng tổng hợp ra một kết luận cuối, rồi
code kiểm tra rủi ro cứng trước khi tự động vào lệnh qua MT5.

Mô tả chính xác cách vận hành, giao diện demo và các chức năng chính xem tại
[MO_TA_VAN_HANH.md](MO_TA_VAN_HANH.md).

## AI trong phòng

- Kỹ thuật: OpenAI
- Vĩ mô / tin: Claude
- Tâm lý / dòng tiền: Gemini
- Phản biện: OpenAI
- Quản trị rủi ro (tư vấn): Claude
- Trưởng phòng tổng hợp: Claude

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
