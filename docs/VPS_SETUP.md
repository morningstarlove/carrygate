# 가상서버(VPS) 중계 수집기 설정 — 바이낸스 선물 1분봉

> 2026-10-21, 제안함 P20·P21. 코딩 지식 없이 따라 할 수 있게 썼다. 전부 합쳐 20~30분. 한 번만 하면 된다.

## 왜 필요한가

매일 아침 자동으로 도는 GitHub Actions 는 **미국 서버**라 바이낸스 API 가 막혀 있다(451).
그래서 바이낸스는 하루 늦게 올라오는 공개 파일(data.binance.vision)로 받는데, 이 설정을 하면
**사용자의 가상서버가 하루 한 번 바이낸스 1분봉(최근 4일)을 읽어 저장소에 올리고**, 다음 날 아침 Actions 가 그 파일을 먼저 쓴다.
파일이 없거나 36시간 넘게 오래되면 Actions 는 예전처럼 공개 파일로 돌아간다 — 서버가 꺼져도 기록은 끊기지 않는다.

- 거래소 계정·API 키는 **필요 없다.** 누구나 볼 수 있는 공개 시세만 읽는다. 주문은 넣지 않는다.
- 필요한 것은 GitHub 토큰 하나(이 저장소에 파일을 올리는 권한만).
- **서버가 미국에 있으면 안 된다.** 바이낸스가 미국 IP 를 막는다. 1단계에서 바로 확인된다.

---

## 1단계. 서버에 들어가서 파이썬 확인 (5분)

**리눅스(우분투 등) 서버** — 검은 글자 화면(터미널)으로 들어가는 경우:
```
python3 --version
```
`Python 3.8` 이상이 나오면 다음 단계. `command not found` 면:
```
sudo apt update && sudo apt install -y python3
```

**윈도우 서버** — 원격 데스크톱으로 바탕화면이 보이는 경우: `docs/KR_RELAY_SETUP.md` 1단계(파이썬 설치)와 같다.

## 2단계. 수집기 파일 받기 (3분)

**리눅스:**
```
mkdir -p ~/carrygate-relay && cd ~/carrygate-relay
curl -sSO https://raw.githubusercontent.com/morningstarlove/carrygate/main/vps_relay.py
python3 vps_relay.py --no-upload
```
6개 코인이 `1분봉 5760개` 안팎으로 찍히면 성공. **`451`** 이 찍히면 서버가 미국이다 — 다른 지역 서버가 필요하다.

**윈도우:** 브라우저에서 위 주소의 `vps_relay.py` 를 받아 `C:\carrygate-relay\` 에 둔다. 명령 프롬프트에서
`cd C:\carrygate-relay` → `python vps_relay.py --no-upload`.

## 3단계. GitHub 토큰 만들기 (5분)

`docs/KR_RELAY_SETUP.md` 3단계와 같다. 요점:
1. GitHub → 오른쪽 위 프로필 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
2. Repository access: **Only select repositories** → `carrygate`. Permissions → Repository permissions → **Contents: Read and write**. 그 외는 건드리지 않는다.
3. 만든 토큰(`github_pat_...`)을 복사한다. 한 번만 보여 준다.

이미 `kr_relay_config.json` 용 토큰이 있으면 **그대로 써도 된다**(같은 권한).

## 4단계. 설정 파일 (2분)

`vps_relay.py` 와 **같은 폴더**에 `vps_relay_config.json` 을 만든다. 내용:
```
{"token": "github_pat_여기에_붙여넣기", "repo": "morningstarlove/carrygate", "branch": "main"}
```
리눅스에서는 `nano vps_relay_config.json` 로 열어 붙여 넣고 Ctrl+O, Enter, Ctrl+X.

시험:
```
python3 vps_relay.py
```
마지막 줄들이 `업로드 완료 → morningstarlove/carrygate/data/binance_1m/XRP.json` 처럼 6줄 나오면 끝.
GitHub 저장소의 `data/binance_1m/` 폴더에 파일 6개가 보인다.

## 5단계. 매일 자동 실행 (5분)

**리눅스 (cron):**
```
(crontab -l 2>/dev/null; echo "5 0 * * * cd ~/carrygate-relay && /usr/bin/python3 vps_relay.py >> relay.log 2>&1") | crontab -
```
서버 시계가 UTC 면 00:05 UTC(한국 09:05) — Actions(09:18 KST)보다 13분 먼저 돈다. 서버 시계가 한국시간이면 `5 0` 을 `5 9` 로.
확인: `crontab -l`. 다음 날 `tail relay.log`.

**윈도우 (작업 스케줄러):** `docs/KR_RELAY_SETUP.md` 5단계와 같다. 프로그램은 `python`, 인수는 `vps_relay.py`,
시작 위치는 `C:\carrygate-relay`, 매일 **09:05**.

## 잘 되는지 보는 법

- 다음 날 아침 Actions 로그 "Scalp Binance futures" 단계: 바이낸스 6개 시장이 수집 실패 없이 평가되면 중계 파일이 쓰인 것이다.
- `scalp_binance.json` 의 `markets` 에 `binance:XRP` 등 6개.

## 문제가 생기면

| 메시지 | 뜻 | 할 일 |
|---|---|---|
| `451` | 서버가 미국 | 다른 지역 서버 |
| `설정 파일이 없다` | 4단계 파일 이름·위치 | `vps_relay.py` 와 같은 폴더, 이름 정확히 |
| `HTTP 401` / `403` (api.github.com) | 토큰 권한·만료 | 3단계 다시. Contents: Read and write, 저장소 `carrygate` |
| `봉 N개뿐` | 바이낸스 응답 일부만 | 잠시 뒤 다시. 반복되면 그대로 보고 |

이 서버에는 거래소 키를 두지 않는다. 나중에 소액 체결 시험(`docs/SCALP_LIVE_TEST_PLAN.md`)을 하면 그 주문기는 **별도 저장소** `carrygate-exec` 로 이 서버에 둔다.
