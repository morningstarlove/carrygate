# trading-loop — 알파카 모의 계좌용 연구 루프 (실제 돈 없음, 주문 없음)

| 폴더/파일 | 하는 일 |
|---|---|
| `research/` | 아이디어 메모. 첫 메모는 `first-idea.md` |
| `strategies/` | 전략 코드 (예: `spy_ma20.py`) |
| `backtests/` | 과거 데이터로 돌린 결과 |
| `journal/` | 날짜별 매매 일지 |
| `memory/` | 가설 장부 `ledger.db` (SQLite). 없으면 `.venv/bin/python memory/init_ledger.py` |
| `config/` | `.env` 에 알파카 모의 계좌 키(직접 입력), `risk.json` 에 손실 한도 다섯 개. **git 에 올라가지 않음** |
| `data/` | 내려받은 시세. git 에 올라가지 않음 |
| `risk.py` | `check_order(order, account)` — 주문 전 다섯 한도 검사 |
| `.venv/` | 파이썬 가상환경 + alpaca-py |

다시 설치: `python3 -m venv .venv && .venv/bin/pip install alpaca-py`
