#!/usr/bin/env bash
# CARRYGATE 가상서버 중계 수집기 설치 — 우분투 서버(root)에서 한 번 실행. 제안함 P21, 절차 docs/VPS_SETUP.md
#
#   curl -sS https://raw.githubusercontent.com/morningstarlove/carrygate/main/scripts/vps_relay_install.sh | bash
#   (토큰은 실행 중에 물어본다. 미리 주려면 ... | bash -s -- github_pat_XXXX)
#
# 하는 일: /root/carrygate-relay 에 vps_relay.py 받기 → 바이낸스 수집 시험(--no-upload) → 설정 파일 → 업로드 시험
#          → cron 매일 09:05(서버 시계 기준) 등록. 거래소 키 없음. 기존 cron 줄은 건드리지 않는다.
set -u
DIR="${CARRYGATE_RELAY_DIR:-$HOME/carrygate-relay}"
RAW="https://raw.githubusercontent.com/morningstarlove/carrygate/main"
REPO="${CARRYGATE_REPO:-morningstarlove/carrygate}"
TOKEN="${1:-}"

echo "== 1/5 파이썬 확인"
command -v python3 >/dev/null || { echo "python3 가 없다. 먼저:  sudo apt update && sudo apt install -y python3"; exit 1; }
python3 --version

echo "== 2/5 수집기 받기 → $DIR"
mkdir -p "$DIR" && cd "$DIR" || exit 1
curl -sS -o vps_relay.py "$RAW/vps_relay.py" || { echo "GitHub 에서 파일을 못 받았다"; exit 1; }

echo "== 3/5 바이낸스 수집 시험 (업로드 없음, 1~2분)"
if ! python3 vps_relay.py --no-upload --days 1; then
  echo "수집 실패. 위 메시지에 451 이 있으면 서버가 미국이라 바이낸스가 막힌 것 — 다른 지역 서버 필요"; exit 1
fi

echo "== 4/5 설정 파일"
if [ -z "$TOKEN" ] && [ -f vps_relay_config.json ]; then
  echo "기존 vps_relay_config.json 을 쓴다"
else
  if [ -z "$TOKEN" ]; then
    printf "GitHub 토큰(github_pat_..., Contents: Read and write, 저장소 %s 만)을 붙여 넣고 Enter: " "$REPO"
    read -r TOKEN < /dev/tty
  fi
  [ -n "$TOKEN" ] || { echo "토큰이 비었다"; exit 1; }
  printf '{"token": "%s", "repo": "%s", "branch": "main"}\n' "$TOKEN" "$REPO" > vps_relay_config.json
  chmod 600 vps_relay_config.json
fi
echo "업로드 시험 (4일치, 2~3분)"
python3 vps_relay.py || { echo "업로드 실패. 401/403 이면 토큰 권한·저장소 확인 (docs/VPS_SETUP.md 3단계)"; exit 1; }

echo "== 5/5 매일 09:05 자동 실행 (서버 시계: $(date '+%Z %H:%M'))"
LINE="5 9 * * * cd $DIR && /usr/bin/python3 vps_relay.py >> relay.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'vps_relay.py' ; echo "$LINE" ) | crontab -
crontab -l | grep vps_relay.py
echo
echo "완료. 내일 아침 확인: tail $DIR/relay.log  /  GitHub 저장소 data/binance_1m/ 에 파일 6개"
