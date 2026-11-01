@echo off
REM Bấm đúp file này để mở app Phòng trade AI (không cần gõ lệnh, không cần trình duyệt).
REM App chạy trên máy local, chỉ dùng Python 3.13 đã cài MetaTrader5.
cd /d "%~dp0"
py -3.13 -m trading_room.desktop_app
if errorlevel 1 (
  echo.
  echo App không mở được. Xem lỗi ở trên. Thường là do Python chưa kèm Tkinter:
  echo Cài lại Python từ python.org, tick "Add python.exe to PATH", rồi chạy lại file này.
  pause
)
