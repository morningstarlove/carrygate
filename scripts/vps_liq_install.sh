#!/usr/bin/env bash
# CARRYGATE 가상서버 청산 수신기 설치 (제안함 P40). 우분투 서버(root)에서 한 번 실행. 키·토큰 없음, 읽기 전용.
#
#   curl -sS https://raw.githubusercontent.com/morningstarlove/carrygate/main/scripts/vps_liq_install.sh | bash
#
# 하는 일: /root/carrygate-relay 에 vps_liq_collector.py 받기 → 바이낸스 주소 시험(1~2분)
#          → systemd 서비스 carrygate-liq 로 상시 실행(꺼지면 자동 재시작, 재부팅 뒤에도) → 파일이 쌓이는지 확인.
# 결과 파일은 /var/www/carrygate/liq/ — 이미 있는 Caddy 경로 /carrygate/* 로 나간다(1분봉 중계 설치 때 만든 것). 새 포트 없음.
# 끄기:  systemctl disable --now carrygate-liq        로그:  journalctl -u carrygate-liq -n 50
set -u
DIR="${CARRYGATE_RELAY_DIR:-$HOME/carrygate-relay}"
OUT="/var/www/carrygate/liq"
RAW="${CARRYGATE_RAW:-https://raw.githubusercontent.com/morningstarlove/carrygate/main}"
UNIT="/etc/systemd/system/carrygate-liq.service"

echo "== 1/4 파이썬 확인"
command -v python3 >/dev/null || { echo "python3 가 없다:  apt update && apt install -y python3"; exit 1; }
python3 --version

echo "== 2/4 수신기 받기 → $DIR, 결과 폴더 $OUT"
mkdir -p "$DIR" "$OUT" && cd "$DIR" || exit 1
curl -fsS -o vps_liq_collector.py "$RAW/vps_liq_collector.py" || { echo "GitHub 에서 파일을 못 받았다"; exit 1; }
chmod 755 /var/www/carrygate "$OUT"

echo "== 3/4 바이낸스 주소 시험 (후보 7개 × 15초)"
python3 vps_liq_collector.py --probe || { echo "가격 흐름을 하나도 못 받았다 — 위 출력을 그대로 Claude 에게 붙여 달라"; exit 1; }

echo "== 4/4 상시 실행 등록 (systemd carrygate-liq)"
cat > "$UNIT" <<EOF
[Unit]
Description=CARRYGATE liquidation collector (read-only, no keys)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $DIR/vps_liq_collector.py --out-dir $OUT
Restart=always
RestartSec=5
MemoryMax=200M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable carrygate-liq >/dev/null 2>&1 && systemctl restart carrygate-liq || { echo "서비스 등록 실패"; exit 1; }
echo "70초 기다린다 (첫 상태 파일)…"; sleep 70
systemctl is-active carrygate-liq || { journalctl -u carrygate-liq -n 20 --no-pager; exit 1; }
ls -l "$OUT"
echo "--- status.json"; cat "$OUT/status.json"; echo
CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1/carrygate/liq/status.json")
[ "$CODE" = "200" ] && echo "확인 OK: /carrygate/liq/status.json 200" || echo "주의: http://127.0.0.1/carrygate/liq/status.json → $CODE (Caddy 경로 확인 필요 — 1분봉 중계 설치가 먼저다)"
echo
echo "완료. 밖에서 확인: curl -s http://158.247.206.213/carrygate/liq/status.json"
echo "모레 아침 Actions 로그 'Liquidation cascade' 단계에 첫 하루치가 찍힌다(UTC 하루가 온전히 찬 뒤)."
