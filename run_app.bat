@echo off
REM Bấm đúp file này để mở app Phòng trade AI (không cần gõ lệnh, không cần trình duyệt).
REM App chạy trên máy local, chỉ dùng Python 3.13 đã cài MetaTrader5.
cd /d "%~dp0"
py -3.13 -m trading_room.desktop_app
if errorlevel 1 (
  echo.
  echo App không mở được. Xem lỗi ở trên. Thường do thiếu thư viện giao diện:
  echo py -3.13 -m pip install -r requirements.txt
  echo Nếu vẫn lỗi Tkinter: cài lại Python từ python.org, tick "Add python.exe to PATH", rồi chạy lại file này.
  pause
)
