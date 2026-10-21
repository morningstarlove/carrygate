# 맥의 Claude Code 세션에 붙여 넣을 지시문 — 가상서버 중계 설치

> 클라우드 세션(carrygate 작업 세션)은 서버에 SSH 로 못 들어간다(네트워크 정책·키 없음). 맥에서 Claude Code 를 열고 아래 상자를 **통째로** 붙여 넣는다. 토큰 없이 끝난다.

```
Vultr 서버(ssh root@158.247.206.213, 맥북에 키 등록됨)에 CARRYGATE 바이낸스 1분봉 중계를 설치해 줘.
저장소 morningstarlove/carrygate 의 docs/VPS_SETUP.md "끌어오기 방식" 절차다. 거래소 키·GitHub 토큰은 필요 없다.

1) ssh 로 들어가 이 한 줄을 실행하고 출력을 전부 보여 줘:
   curl -sS https://raw.githubusercontent.com/morningstarlove/carrygate/main/scripts/vps_relay_install_pull.sh | bash
2) 스크립트는 /root/carrygate-relay 에 vps_relay.py 를 받고, /var/www/carrygate/binance_1m/ 에 코인 6개 JSON 을 쓰고,
   /etc/caddy/Caddyfile 의 :80 사이트 블록에 `import /etc/caddy/carrygate.caddy` 한 줄을 넣어(백업 자동) /carrygate/* 를 정적 파일로 내보내고,
   caddy validate → systemctl reload caddy → curl 127.0.0.1 확인 → cron 09:05 등록까지 한다.
3) ":80 사이트 블록을 못 찾았다" 가 나오면 Caddyfile 을 보고 :80 (또는 http://) 사이트 블록 안에 그 import 한 줄을 손으로 넣고
   caddy validate 와 systemctl reload caddy 를 한 뒤, 스크립트를 다시 실행해 줘.
4) 끝나면 맥에서  curl -s http://158.247.206.213/carrygate/binance_1m/XRP.json | head -c 300  결과와
   서버의  crontab -l | grep vps_relay  결과를 보여 줘.
규칙: 다른 봇 폴더·서비스·cron 줄·Caddy 의 기존 경로는 건드리지 않는다. 새 포트를 열지 않는다. 키·토큰을 출력하지 않는다.
```

끝나면 결과를 carrygate 클라우드 세션에 알려 준다. 다음 날 아침(09:18 KST) Actions 가 서버 파일을 먼저 읽는다.
