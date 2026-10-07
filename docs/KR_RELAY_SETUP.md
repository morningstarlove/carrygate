# 한국 PC 중계 수집기 설정 — 바이낸스·바이비트 펀딩비

> **2026-10-05 추가: 이 설정은 이제 선택 사항이다.** PC 없이도 매일 자동 실행이 CoinGecko 를 통해 두 거래소의
> 펀딩비를 받는다(`funding.py` 의 CoinGecko 경로). 다만 CoinGecko 는 "현재값"만 주므로 7일 평균은 하루 한 표본씩
> 모아 5일 뒤부터 생긴다. 이 PC 중계기를 켜면 거래소의 실제 정산 이력(하루 3회)으로 7일 평균이 **첫날부터** 나오고,
> 정산주기(8시간/4시간)도 실측된다. 더 정확하고 싶을 때만 설정한다.

> 코딩 지식 없이 따라 할 수 있게 썼다. 전부 합쳐 20~30분. 한 번만 하면 된다.

## 왜 필요한가

매일 아침 자동으로 도는 GitHub Actions 는 **미국 서버**라 바이낸스(451)·바이비트(403)가 막혀 있다.
두 거래소에서 캐리(이자 받기)를 하려면 이자율을 먼저 봐야 하는데, 그 데이터가 지금은 없다.

이 설정을 하면 **집 PC 가 하루 한 번 두 거래소 이자율을 읽어 저장소에 올리고**, 다음 날 아침 Actions 가 그 파일을
바이낸스·바이비트 데이터로 쓴다. 7일이 쌓이면 다른 거래소와 똑같이 "진입 가능/대기/청산" 판정이 나온다.

- 거래소 계정·API 키는 **필요 없다.** 누구나 볼 수 있는 공개 시세만 읽는다.
- 주문은 넣지 않는다.
- 필요한 것은 GitHub 토큰 하나(이 저장소에 파일 하나 올리는 권한만).

---

## 1단계. 파이썬 설치 (5분)

1. https://www.python.org/downloads/ 에서 **Download Python 3.x** 노란 버튼.
2. 받은 파일 실행. 첫 화면 **맨 아래 "Add python.exe to PATH" 체크박스를 반드시 켠다.** 그다음 Install Now.
3. 확인: 시작 메뉴에서 `cmd` 를 쳐서 명령 프롬프트를 열고 `python --version` 입력 → `Python 3.1x.x` 가 나오면 성공.

## 2단계. 수집기 파일 받기 (3분)

1. 문서 폴더 안에 `carrygate` 폴더를 하나 만든다. (예: `C:\Users\이름\Documents\carrygate`)
2. 아래 두 파일을 그 폴더에 저장한다. 링크를 열고 **Ctrl+S** (다른 이름으로 저장). 파일 이름을 바꾸지 않는다.
   - https://raw.githubusercontent.com/morningstarlove/carrygate/main/kr_relay.py
   - https://raw.githubusercontent.com/morningstarlove/carrygate/main/kr_relay.bat
   > 브라우저가 `.txt` 를 붙이면 파일 형식을 "모든 파일"로 바꾸고 이름을 `kr_relay.py` 로 저장한다.
3. 시험: `kr_relay.bat` 을 더블클릭한다. 검은 창에 BTC·ETH 등 6개 코인 × 2개 거래소 이자율이 찍히고
   마지막에 `설정 파일이 없다` 로 끝나면 **수집은 정상**이다. (아직 업로드 설정을 안 했으니 당연하다.)

## 3단계. GitHub 토큰 만들기 (10분)

이 저장소에 파일 하나를 올릴 수 있는 열쇠다. **이 저장소에만, 파일 쓰기만** 되는 가장 좁은 권한으로 만든다.

1. GitHub 로그인 → 오른쪽 위 프로필 사진 → **Settings**.
2. 왼쪽 맨 아래 **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
3. 입력:
   - Token name: `carrygate-kr-relay`
   - Expiration: **1 year** (만료되면 다시 만들어 넣으면 된다)
   - Repository access: **Only select repositories** → `carrygate` 선택
   - Permissions → Repository permissions → **Contents** 를 **Read and write** 로. 나머지는 손대지 않는다.
4. **Generate token** → `github_pat_...` 로 시작하는 긴 글자가 한 번만 보인다. **복사해 둔다.** (창을 닫으면 다시 못 본다. 잃으면 새로 만들면 된다.)

## 4단계. 설정 파일 만들기 (3분)

1. 2단계 폴더에서 마우스 오른쪽 → 새로 만들기 → 텍스트 문서. 이름을 `kr_relay_config.json` 으로 바꾼다.
   (`.txt` 가 붙어 있으면 지운다. 확장명이 안 보이면 폴더 창 위 "보기 → 파일 확장명" 체크.)
2. 메모장으로 열어 아래를 붙여 넣고 `여기에_토큰` 자리에 3단계 토큰을 넣는다. 따옴표는 그대로 둔다.

```json
{"token": "여기에_토큰", "repo": "morningstarlove/carrygate", "branch": "main"}
```

3. 저장. **이 파일은 비밀이다.** 남에게 보내거나 저장소에 올리지 않는다(저장소는 이 이름을 무시하게 돼 있다).
4. `kr_relay.bat` 을 다시 더블클릭. 마지막 줄이 `업로드 완료 → morningstarlove/carrygate/data/kr_funding.json` 이면 끝.
   GitHub 저장소 페이지에서 `data` 폴더에 `kr_funding.json` 이 생긴 것을 확인할 수 있다.

## 5단계. 매일 자동 실행 (5분)

Actions 가 **매일 09:18(한국시간)** 에 돌므로 그 전에 올라가 있어야 한다. 08:50 으로 건다.

1. 시작 메뉴에서 `작업 스케줄러` 검색 → 실행.
2. 오른쪽 **기본 작업 만들기**.
   - 이름: `carrygate 중계` → 다음
   - 트리거: **매일** → 다음 → 시작 시간 **08:50** → 다음
   - 동작: **프로그램 시작** → 다음
   - 프로그램/스크립트: **찾아보기** 로 2단계 폴더의 `kr_relay.bat` 선택
   - **시작 위치(옵션)** 에 그 폴더 경로를 넣는다 (예: `C:\Users\이름\Documents\carrygate`). 이걸 비우면 설정 파일을 못 찾는다.
   - 마침.
3. 만든 작업을 더블클릭 → **일반** 탭에서 "사용자가 로그온할 때만 실행"이 켜져 있으면 PC 가 켜져 있고 로그인돼 있어야 돈다.
   밤새 켜 두지 않는 PC 라면 **조건** 탭 → "이 작업을 실행하기 위해 절전 모드 종료" 를 켠다.
4. 시험: 작업 목록에서 오른쪽 클릭 → **실행**. 몇 초 뒤 GitHub 의 `data/kr_funding.json` 수정 시각이 바뀌면 성공.

> PC 가 꺼져 있어 그날 못 올리면? 다음 날 Actions 는 **36시간보다 오래된 파일은 쓰지 않는다.** 그날 바이낸스·바이비트는
> 빠진 채로 나머지 3곳만 판정한다. 하루 빠져도 7일 평균은 거래소 이력에서 다시 받으므로 기록이 망가지지 않는다.

---

## 결과 보는 곳

- 통합 현황판의 **캐리 이자율** 표에 `binance` / `bybit` 줄이 생긴다. 다음 날 아침 09:18 이후.
- `funding.json` 의 `kr_relay` 항목: `fresh: true`, `age_hours`, `used: ["binance","bybit"]`.
- 7일이 쌓이면 두 거래소도 `7일평균` 기준으로 진입 판정에 들어간다. 그 전에는 비교만 한다.

## 문제가 생기면

| 증상 | 원인 | 조치 |
|---|---|---|
| `'python'은(는) 내부 또는 외부 명령...` | 1단계에서 PATH 체크를 안 했다 | 파이썬 설치 파일 다시 실행 → Modify → "Add to PATH" 체크 |
| `설정 파일이 없다` | 4단계 파일 이름이 다르거나 `.txt` 가 붙었다 | 이름을 정확히 `kr_relay_config.json` 으로 |
| `HTTP 401` / `HTTP 403` | 토큰이 틀렸거나 권한이 부족·만료 | 3단계 다시. Contents: Read and write, 저장소 carrygate 선택 |
| `HTTP 404` | repo 이름 오타 | `morningstarlove/carrygate` 확인 |
| 두 거래소 모두 실패 | 인터넷 또는 거래소 점검 | 잠시 뒤 다시 실행. 한국 IP 인지 확인(VPN 끄기) |
| 스케줄러가 돌았는데 파일이 안 올라감 | 시작 위치를 비웠다 | 5단계 2번 시작 위치 입력 |

## 안전

- 토큰으로 할 수 있는 것은 **이 저장소의 파일 읽기·쓰기뿐**이다. 거래소·돈과는 아무 관계가 없다.
- 그래도 유출되면 저장소 파일을 고칠 수 있으니, 의심되면 GitHub → Settings → Developer settings 에서 토큰을 **Delete** 하고 새로 만든다.
- 수집기는 거래소에 로그인하지 않는다. 거래소 API 키는 어디에도 넣지 않는다.
