"""risk.py — 주문 하나를 넣기 전에 다섯 가지 손실 한도를 검사한다.

check_order(order, account) -> (True, 'OK') 또는 (False, '막은 이유')

order   예: {"value": 50, "stop_loss_pct": 3.0}      value = 주문 금액(달러), stop_loss_pct = 손절 폭(%)
account 예: {"daily_loss_pct": 2.5, "drawdown_pct": 1.0, "equity": 1000, "open_positions": 0}
         daily_loss_pct = 오늘 잃은 비율(%), drawdown_pct = 고점 대비 낙폭(%),
         equity = 총 자산(달러), open_positions = 지금 열린 포지션 수

한도 다섯 개는 config/risk.json 에서 읽는다. 이 파일은 주문을 넣지 않는다. 판단만 한다.
"""
import json
import os

DEFAULTS = {
    "daily_loss_limit_pct": 2.0,     # 하루 손실 이 % 이상이면 매매 중단
    "max_drawdown_pct": 8.0,         # 고점 대비 낙폭 이 % 이상이면 매매 중단
    "max_stop_loss_pct": 5.0,        # 거래 하나의 손절 폭은 이 % 를 넘지 못함
    "max_order_pct_of_equity": 10.0, # 주문 금액은 자산의 이 % 를 넘지 못함
    "max_open_positions": 5,         # 열린 포지션이 이 개수 이상이면 새 주문 금지
}

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config", "risk.json")


def load_limits(path=CONFIG_PATH):
    """config/risk.json 을 읽는다. 파일이 없거나 항목이 빠지면 DEFAULTS 로 채운다."""
    limits = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        for k in DEFAULTS:
            if k in loaded:
                limits[k] = loaded[k]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return limits


def check_order(order, account, limits=None):
    """주문을 허용할지 판단한다. 가장 먼저 걸리는 한도의 이유를 돌려준다."""
    lim = limits or load_limits()

    # 1. 하루 손실 한도
    if float(account.get("daily_loss_pct", 0.0)) >= lim["daily_loss_limit_pct"]:
        return (False, "Daily loss limit hit. Trading stopped.")

    # 2. 최대 낙폭 한도
    if float(account.get("drawdown_pct", 0.0)) >= lim["max_drawdown_pct"]:
        return (False, "Max drawdown limit hit. Trading stopped.")

    # 3. 손절선: 없으면 거부, 너무 넓어도 거부
    stop = order.get("stop_loss_pct")
    if stop is None:
        return (False, "Order has no stop_loss_pct. Rejected.")
    if float(stop) > lim["max_stop_loss_pct"]:
        return (False, "Stop loss %.1f%% is wider than the %.1f%% limit. Rejected."
                % (float(stop), lim["max_stop_loss_pct"]))

    # 4. 주문 금액이 자산의 일정 비율 초과
    equity = float(account.get("equity", 0.0))
    value = float(order.get("value", 0.0))
    if equity <= 0:
        return (False, "Account equity is zero or unknown. Rejected.")
    if value > equity * lim["max_order_pct_of_equity"] / 100.0:
        return (False, "Order size exceeds %.1f%% of equity. Rejected."
                % lim["max_order_pct_of_equity"])

    # 5. 열린 포지션 수
    if int(account.get("open_positions", 0)) >= lim["max_open_positions"]:
        return (False, "Too many open positions (max %d). Rejected."
                % lim["max_open_positions"])

    return (True, "OK")


if __name__ == "__main__":
    print("limits:", load_limits())
    print(check_order({"value": 50, "stop_loss_pct": 3.0},
                      {"daily_loss_pct": 0.0, "drawdown_pct": 0.0, "equity": 1000, "open_positions": 0}))
