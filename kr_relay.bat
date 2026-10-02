@echo off
rem CARRYGATE 한국 중계 수집기 — 더블클릭하면 바이낸스·바이비트 펀딩비를 읽어 저장소에 올린다.
cd /d "%~dp0"
python kr_relay.py
if errorlevel 1 (
  echo.
  echo [실패] 위 메시지를 확인하세요. docs/KR_RELAY_SETUP.md 의 "문제가 생기면" 표 참고.
)
