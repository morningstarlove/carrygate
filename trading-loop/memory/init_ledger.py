"""memory/init_ledger.py — 가설 장부(SQLite) memory/ledger.db 를 만든다. 이미 있으면 그대로 둔다.

실행: .venv/bin/python memory/init_ledger.py
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    idea                TEXT NOT NULL,   -- 아이디어 한 문장
    proposed_by         TEXT,            -- 제안한 에이전트(사람/모델 이름)
    market              TEXT,            -- 시장 (예: US-ETF, SPY)
    tested_at           TEXT,            -- 테스트한 날짜 (YYYY-MM-DD)
    strategy_file       TEXT,            -- 전략 파일 경로
    strategy_version    TEXT,            -- 전략 버전
    variants_tried      INTEGER DEFAULT 0, -- 시도한 변형 수
    market_regime       TEXT,            -- 시장 국면 (상승/횡보/하락 등)
    backtest_result     TEXT,            -- 백테스트 결과 (요약 또는 JSON)
    holdout_result      TEXT,            -- 봉인 구간(아웃오브샘플) 결과
    paper_result        TEXT,            -- 모의 매매 결과
    status              TEXT DEFAULT 'proposed', -- proposed / testing / passed / failed / abandoned
    lessons             TEXT             -- 배운 점
);
"""


def init(path=DB_PATH):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return path


if __name__ == "__main__":
    print("ledger ready:", init())
