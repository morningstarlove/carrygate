# 가상서버(VPS) 중계 수집기 설정 — 바이낸스 선물 1분봉

> 2026-10-21, 제안함 P21. 사용자 서버 = **Vultr 우분투 서버**(다른 세션 기록: root 로 SSH, 서버 시계 한국시간, 메모리 1GB, 이미 봇 여러 개가 cron·systemd 로 돈다). 이 수집기는 하루 한 번 2~3분, 메모리 수십 MB 라 그 서버에 부담이 없다.
> 코딩 지식 없이 따라 할 수 있게 썼다. **명령 두 개**면 끝난다(5분).

## 왜 필요한가

매일 아침 자동으로 도는 GitHub Actions 는 **미국 서버**라 바이낸스 API 가 막혀 있다(451).
그래서 바이낸스는 하루 늦게 올라오는 공개 파일(data.binance.vision)로 받는데, 이 설정을 하면
**사용자의 Vultr 서버가 매일 09:05 바이낸스 1분봉(최근 4일)을 읽어 저장소 `data/binance_1m/` 에 올리고**, 09:18 Actions 가 그 파일을 먼저 쓴다.
파일이 없거나 36시간 넘게 오래되면 Actions 는 공개 파일로 돌아간다 — 서버가 꺼져도 기록은 끊기지 않는다.

- 거래소 계정·API 키는 **필요 없다.** 누구나 볼 수 있는 공개 시세만 읽는다. 주문은 넣지 않는다.
- 필요한 것은 GitHub 토큰 하나(이 저장소에 파일을 올리는 권한만).
- 다른 봇의 파일·cron 줄은 건드리지 않는다. 폴더 `/root/carrygate-relay` 하나만 새로 만든다.

## 1단계. GitHub 토큰 (3분) — 이미 있으면 건너뛴다

한국 PC 중계기(`kr_relay_config.json`)용 토큰을 만든 적이 있으면 **그 토큰을 그대로 쓴다**(같은 권한). 없으면:
1. GitHub → 오른쪽 위 프로필 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
2. Repository access: **Only select repositories** → `carrygate`. Permissions → Repository permissions → **Contents: Read and write**. 그 외는 건드리지 않는다.
3. 만든 토큰(`github_pat_...`)을 복사해 둔다. 한 번만 보여 준다.

## 2단계. 서버에서 설치 명령 (2분)

맥 터미널에서 평소처럼 서버에 들어간다(`ssh root@서버주소`). 그다음 **이 한 줄**을 붙여 넣는다:
```
curl -sS https://raw.githubusercontent.com/morningstarlove/carrygate/main/scripts/vps_relay_install.sh | bash
```
스크립트가 차례로 한다: 파이썬 확인 → 수집기 받기 → 바이낸스 수집 시험 → **토큰을 물어보면 붙여 넣고 Enter** → 업로드 시험 → 매일 09:05 cron 등록.

마지막에 이렇게 나오면 끝:
```
  업로드 완료 → morningstarlove/carrygate/data/binance_1m/XRP.json (xxxxxxx)   ← 6줄
5 9 * * * cd /root/carrygate-relay && /usr/bin/python3 vps_relay.py >> relay.log 2>&1
완료. 내일 아침 확인: ...
```

## 잘 되는지 보는 법

- GitHub 저장소 `data/binance_1m/` 폴더에 `BTC.json` 등 6개 파일, 커밋 메시지 `vps-relay binance ...`.
- 다음 날: 서버에서 `tail /root/carrygate-relay/relay.log`. Actions 로그 "Scalp Binance futures" 단계에 바이낸스 6개 시장이 수집 실패 없이 평가되면 중계 파일이 쓰인 것이다. `scalp_binance.json` 의 `markets` 에 `binance:XRP` 등 6개.

## 문제가 생기면

| 메시지 | 뜻 | 할 일 |
|---|---|---|
| `451` | 서버 위치를 바이낸스가 막음 | 그대로 보고. Vultr 다른 지역으로 옮기거나 공개 파일 방식(지금 기본)으로 둔다 |
| `HTTP 401` / `403` (api.github.com) | 토큰 권한·만료 | 1단계 다시. Contents: Read and write, 저장소 `carrygate` |
| `봉 N개뿐` | 바이낸스 응답 일부만 | 잠시 뒤 `cd /root/carrygate-relay && python3 vps_relay.py` 다시. 반복되면 보고 |
| 다시 설치하고 싶다 | | 2단계 한 줄을 다시 붙여 넣으면 된다(기존 설정 파일이 있으면 토큰을 다시 묻지 않는다) |

## 손으로 돌리기 / 끄기

```
cd /root/carrygate-relay && python3 vps_relay.py              # 지금 한 번
cd /root/carrygate-relay && python3 vps_relay.py --no-upload  # 수집만 시험
crontab -l | grep -v vps_relay.py | crontab -                 # 매일 실행 끄기 (다른 줄은 그대로)
```

이 서버에는 거래소 키를 두지 않는다. 나중에 소액 체결 시험(`docs/SCALP_LIVE_TEST_PLAN.md`)을 하면 그 주문기는 **별도 저장소** `carrygate-exec` 로 이 서버에 둔다 — 그때 이 서버가 설계서의 "24시간 컴퓨터"가 된다.
