#!/usr/bin/env bash
# CARRYGATE 가상서버 중계 — "끌어오기" 설치 (토큰 불필요). 우분투 서버(root)에서 한 번 실행. 제안함 P21, 절차 docs/VPS_SETUP.md
#
#   curl -sS https://raw.githubusercontent.com/morningstarlove/carrygate/main/scripts/vps_relay_install_pull.sh | bash
#
# 하는 일: /root/carrygate-relay 에 vps_relay.py 받기 → 바이낸스 1분봉 4일치를 /var/www/carrygate/binance_1m/ 에 쓰기
#          → Caddy 에 /carrygate/* 정적 경로 추가(백업·검증·실패 시 원복) → 확인 → cron 매일 09:05.
# 거래소 키·GitHub 토큰 없음. 새 포트 없음(기존 Caddy :80 경로 하나 추가). 다른 봇·cron 줄은 건드리지 않는다.
set -u
DIR="${CARRYGATE_RELAY_DIR:-$HOME/carrygate-relay}"
WEB="/var/www/carrygate"
RAW="https://raw.githubusercontent.com/morningstarlove/carrygate/main"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
SNIPPET="/etc/caddy/carrygate.caddy"

echo "== 1/5 파이썬·Caddy 확인"
command -v python3 >/dev/null || { echo "python3 가 없다:  apt update && apt install -y python3"; exit 1; }
python3 --version
command -v caddy >/dev/null || { echo "caddy 명령이 없다 — 이 서버는 Caddy 가 아니다. docs/VPS_SETUP.md 의 토큰 방식(vps_relay_install.sh)으로"; exit 1; }

echo "== 2/5 수집기 받기 → $DIR, 결과 폴더 $WEB/binance_1m"
mkdir -p "$DIR" "$WEB/binance_1m" && cd "$DIR" || exit 1
curl -sS -o vps_relay.py "$RAW/vps_relay.py" || { echo "GitHub 에서 파일을 못 받았다"; exit 1; }
chmod 755 "$WEB" "$WEB/binance_1m"

echo "== 3/5 바이낸스 수집 (4일치, 2~3분)"
python3 vps_relay.py --no-upload --out-dir "$WEB/binance_1m" || { echo "수집 실패 (451 이면 바이낸스 지역 차단)"; exit 1; }
ls -l "$WEB/binance_1m"

echo "== 4/5 Caddy 경로 /carrygate/* 추가"
cat > "$SNIPPET" <<'EOF'
# CARRYGATE 중계 파일 (공개 시세만, 읽기 전용). 추가: scripts/vps_relay_install_pull.sh
handle_path /carrygate/* {
	root * /var/www/carrygate
	file_server
	header Cache-Control "no-store"
}
EOF
if grep -q "carrygate.caddy" "$CADDYFILE"; then
  echo "이미 import 돼 있다"
else
  cp -a "$CADDYFILE" "$CADDYFILE.bak.carrygate.$(date +%Y%m%d%H%M%S)"
  # :80 사이트 블록의 첫 줄 뒤에 import 한 줄을 넣는다
  if ! python3 - "$CADDYFILE" "$SNIPPET" <<'PY'
import re, sys
path, snip = sys.argv[1], sys.argv[2]
src = open(path, encoding="utf-8").read().splitlines(keepends=True)
pat = re.compile(r"^\s*(:80|http://[^\s{]*|[^\s{#]*:80)\s*(,\s*[^\s{]+\s*)*\{\s*$")
for i, line in enumerate(src):
    if pat.match(line):
        src.insert(i + 1, "\timport %s\n" % snip)
        open(path, "w", encoding="utf-8").write("".join(src))
        print("import 삽입: %d행" % (i + 2))
        sys.exit(0)
print(":80 사이트 블록을 못 찾았다 — Caddyfile 을 열어 사이트 블록 안에  import %s  한 줄을 손으로 넣는다" % snip)
sys.exit(1)
PY
  then exit 1; fi
fi
if caddy validate --config "$CADDYFILE" >/dev/null 2>&1; then
  systemctl reload caddy && echo "Caddy 재적재 완료"
else
  echo "Caddy 설정 검증 실패 — 원복한다"; caddy validate --config "$CADDYFILE" 2>&1 | tail -5
  cp -a "$(ls -t "$CADDYFILE".bak.carrygate.* | head -1)" "$CADDYFILE"; exit 1
fi
sleep 1
CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1/carrygate/binance_1m/XRP.json")
[ "$CODE" = "200" ] || { echo "확인 실패: http://127.0.0.1/carrygate/binance_1m/XRP.json → $CODE"; exit 1; }
echo "확인 OK: /carrygate/binance_1m/XRP.json 200"

echo "== 5/5 매일 09:05 자동 실행 (서버 시계: $(date '+%Z %H:%M'))"
LINE="5 9 * * * cd $DIR && /usr/bin/python3 vps_relay.py --no-upload --out-dir $WEB/binance_1m >> relay.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'vps_relay.py' ; echo "$LINE" ) | crontab -
crontab -l | grep vps_relay.py
echo
echo "완료. 밖에서 확인: curl -s http://158.247.206.213/carrygate/binance_1m/XRP.json | head -c 200"
echo "내일 아침 Actions 로그 'Scalp Binance futures' 단계에서 바이낸스 6시장이 수집 실패 없이 평가되면 이 서버 파일이 쓰인 것."
