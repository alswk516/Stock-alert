# Stock-alert

보유종목이 목표가에 도달하면 **카카오톡 + 텔레그램**으로 알려 주고, 매일 아침 **텔레그램으로 시황 브리핑**을 보내는 프로그램입니다.
서버 없이 **GitHub Actions**에서 5분마다 한 번씩 실행됩니다.

## 하는 일

| 기능 | 언제 | 어디로 |
|---|---|---|
| 목표가 알림 | 장중에 현재가가 설정한 목표가(상한/하한)에 닿았을 때 | 카카오톡 '나에게 보내기' + 텔레그램 |
| 시황 브리핑 | 하루 한 번, `briefing_time`(기본 07:00) 이후 첫 실행 때 | 텔레그램 |

## 작동 방식

```
cron-job.org (5분마다)  ──┐
                          ├─▶ GitHub Actions (alert.yml)
GitHub 예약 실행 (백업)  ──┘        │
                                   ├─ 1. 지난 실행의 상태 복원 (토큰, 알림 기록)
                                   ├─ 2. Secrets → config.json, watchlist.json 생성 (.kakao_token.json은 캐시에 없을 때만)
                                   ├─ 3. python run_once.py  ── 한 번 점검하고 종료
                                   │       ├─ 브리핑 (하루 1회)       → 텔레그램
                                   │       └─ 종목별 목표가 확인      → 카카오톡 + 텔레그램
                                   └─ 4. 상태 저장 (실패해도 저장)
```

### 실행 주기
- 워크플로에는 `*/5 * * * *`(5분마다) 예약이 걸려 있지만, GitHub가 예약 실행을 제한해서 **실제로는 몇 시간에 한 번**만 돕니다.
- 그래서 **cron-job.org**가 5분마다 GitHub API(`workflow_dispatch`)를 호출해서 워크플로를 실행합니다. GitHub 자체 예약 실행은 백업으로 남겨 두었습니다.
- 앞의 실행이 아직 돌고 있으면 다음 실행은 기다립니다(`concurrency`). 그래서 같은 알림이 동시에 두 번 가지 않습니다.

### 한 번 실행될 때 하는 일 (`stock_alert.run_cycle`)
1. **브리핑:** 아래 조건을 모두 만족하면 브리핑을 만들어 텔레그램으로 보냅니다. 실패하면 다음 실행 때 다시 시도합니다.
   - `telegram` 설정이 있음
   - 오늘 아직 보내지 않음
   - 지금 시각이 `briefing_time` 이후
2. **카카오 토큰 갱신 (하루 1회):** 카카오 refresh token은 2개월 동안 한 번도 갱신하지 않으면 만료됩니다. 그래서 알림이 없어도 매일 미리 갱신합니다. 만료까지 1개월이 안 남았을 때 갱신하면 새 refresh token(다시 2개월)을 받으므로 계속 연장됩니다. 실패하면 그날은 다시 시도하지 않고 다음 날 시도합니다.
3. **목표가 확인:** `watchlist`의 종목마다 아래 순서로 처리합니다.
   1. 장 시간이 아니면 건너뜁니다.
   2. 한국투자증권(KIS) API로 현재가를 조회합니다.
   3. `target_above` / `target_below`와 비교합니다.
   4. 도달했으면 아직 보내지 않은 채널로 알림을 보냅니다.
4. 발송이나 카카오 토큰 갱신에 하나라도 실패하면 실행을 **실패(exit 1)**로 끝냅니다. 그러면 GitHub가 실패 알림 이메일을 보냅니다.

## 목표가 알림 규칙

- **장 시간에만 확인합니다.** 휴장일은 따로 반영하지 않습니다.
  - 국내(`KR`): 평일 09:00~15:30 (KST)
  - 미국(`US`): 평일 04:00~20:00 (미국 동부시간, 프리·애프터마켓 포함, 서머타임 자동 반영)
- **상한:** 현재가 ≥ `target_above` → `목표가(상한) 도달 🔺`
- **하한:** 현재가 ≤ `target_below` → `하한가 도달 🔻`. 일일 하한가가 아니라 직접 설정한 하한 목표가입니다.
- **같은 목표가로는 한 번만** 알립니다. 가격이 목표가에서 **1% 이상 벗어났다가** 다시 닿으면 다시 알립니다.
- **채널별로 따로 기록합니다.** 예를 들어 카카오가 실패하고 텔레그램만 성공했으면, 다음 실행 때 카카오로만 다시 보냅니다.
- 카카오 메시지는 200자 제한이 있어서 그 이상은 잘립니다.

메시지 예시:
```
[삼성전자] 목표가(상한) 도달 🔺
현재가 80,500원 / 목표 80,000원
11/07 10:15
```

## 브리핑 내용 (`briefing.py`)

1. **주요 지수:** 코스피, 코스닥, S&P500, 나스닥, 원/달러, WTI. 야후 파이낸스 데이터로, 전일 종가 대비 등락률을 보여 줍니다.
2. **보유종목 종가**
   - 국내 종목: KIS 일별 시세를 씁니다. 15:30 전이면 '오늘' 행을 건너뛰고 **장이 끝난 최근 거래일** 종가와 등락률을 씁니다. 그래서 장 시작 전에 보내도 0.0%로 나오지 않습니다. KIS 조회에 실패하면 야후 데이터(`.KS` → `.KQ` 순)로 대신합니다.
   - 해외 종목: 야후 데이터
3. **국제 시황 뉴스 3건:** 한국경제·파이낸셜뉴스·구글 뉴스 RSS에서 가져옵니다. '뉴욕, 연준, 나스닥, 유가…' 같은 키워드가 든 기사를 먼저 고르고, 중복은 뺍니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `stock_alert.py` | 핵심 로직: KIS 시세 조회, 카카오·텔레그램 발송, 장 시간 판단, `run_cycle()`(한 번 점검). 직접 실행하면 로컬에서 계속 감시하는 모드로 돕니다. |
| `briefing.py` | 브리핑 문구 생성: 지수, 보유종목 종가, 뉴스 |
| `run_once.py` | GitHub Actions용 진입점. `run_cycle()`을 한 번 돌리고 종료합니다. |
| `.github/workflows/alert.yml` | 실행 일정, 상태 복원·저장, Secrets를 파일로 만드는 단계 |
| `.gitignore` | 키·토큰·로그 파일이 실수로 커밋되지 않게 막는 목록 |

실행 중에 생기는 파일입니다. 모두 **커밋하면 안 됩니다.** `.gitignore`에 등록돼 있어서 `git add`를 해도 올라가지 않습니다.

| 파일 | 내용 |
|---|---|
| `config.json` | 설정 전체. API 키가 들어 있습니다. Actions에서는 `CONFIG_JSON` 시크릿으로 만듭니다. |
| `watchlist.json` | 목표가 목록. Actions에서는 `WATCHLIST_JSON` 시크릿으로 만들고, 있으면 `config.json`의 `watchlist` 대신 씁니다. |
| `.kis_token.json` | KIS 접근 토큰. 약 24시간 동안 재사용합니다. |
| `.kakao_token.json` | 카카오 access/refresh 토큰. 하루 한 번 갱신해서 저장합니다. |
| `.alert_state.json` | 알림 기록: 오늘 브리핑을 보냈는지, 카카오 토큰을 갱신했는지, 종목별로 어느 채널에 알림을 보냈는지 |
| `stock_alert.log` | 실행 로그 |

`.kis_token.json`, `.kakao_token.json`, `.alert_state.json`은 매 실행이 끝날 때 **GitHub Actions 캐시**에 저장되고, 다음 실행 때 가장 최근 것으로 복원됩니다. 실행이 실패해도 저장합니다.

## 설정

### GitHub Secrets
저장소 **Settings → Secrets and variables → Actions**에 등록합니다.

| 이름 | 내용 |
|---|---|
| `CONFIG_JSON` | 아래 `config.json` 내용 전체 |
| `KAKAO_TOKEN_JSON` | 처음 한 번 쓸 카카오 토큰: `{"access_token": "...", "refresh_token": "..."}`. 캐시에 토큰이 없을 때만 씁니다. |
| `WATCHLIST_JSON` | (선택) 목표가 목록. 아래 '목표가 바꾸기'를 참고하세요. 없으면 `config.json`의 `watchlist`를 씁니다. |

### `config.json` 형식
```json
{
  "kis": { "app_key": "KIS 앱키", "app_secret": "KIS 앱시크릿", "mock": false },
  "kakao": { "rest_api_key": "카카오 REST API 키", "client_secret": "" },
  "telegram": { "bot_token": "텔레그램 봇 토큰", "chat_id": 123456789 },
  "briefing_time": "07:00",
  "watchlist": [
    { "name": "삼성전자", "symbol": "005930", "market": "KR", "target_above": 80000, "target_below": 60000 },
    { "name": "애플", "symbol": "AAPL", "market": "US", "exchange": "NAS", "target_above": 250 }
  ]
}
```

| 항목 | 설명 |
|---|---|
| `kis.mock` | `true`면 KIS 모의투자 서버를 씁니다. |
| `kakao.client_secret` | 카카오 앱에서 Client Secret을 켰을 때만 넣습니다. |
| `telegram` | 없으면 브리핑을 보내지 않고, 목표가 알림은 카카오로만 갑니다. |
| `briefing_time` | 브리핑 시각(KST, `HH:MM`). 이 시각 이후 첫 실행 때 보냅니다. |
| `watchlist[].market` | `KR`(기본값) 또는 `US` |
| `watchlist[].exchange` | 미국 종목 거래소: `NAS`(기본값) / `NYS` / `AMS`. 시세가 비어 있으면 다른 거래소도 시도합니다. |
| `watchlist[].target_above` / `target_below` | 상한·하한 목표가. 둘 중 하나만 넣어도 됩니다. 원화 또는 달러입니다. |
| `watchlist[].yahoo` | (선택) 브리핑에서 쓸 야후 심볼을 직접 지정할 때 넣습니다. |
| `news_rss` | (선택) 뉴스 RSS 주소 목록. 없으면 기본 목록을 씁니다. |
| `poll_seconds` | (선택) 로컬 감시 모드의 확인 간격(초, 기본 10). Actions에서는 쓰지 않습니다. |

### 목표가 바꾸기 (`WATCHLIST_JSON` 시크릿)
목표가 목록은 API 키가 든 `CONFIG_JSON`과 따로 **`WATCHLIST_JSON` 시크릿**에 둡니다. 그래서 목표가를 바꿀 때 키를 건드리지 않아도 됩니다. 시크릿이라 로그에서는 `***`로 가려집니다.
`WATCHLIST_JSON`이 없으면 예전처럼 `config.json`의 `watchlist`를 쓰고, 있으면 `config.json`의 `watchlist`는 무시합니다.

**처음 만들 때**
1. 저장소 **Settings → Secrets and variables → Actions → New repository secret**을 누릅니다.
2. Name에 `WATCHLIST_JSON`을 입력합니다.
3. Secret에 목표가 목록을 붙여 넣고 저장합니다. 지금 `config.json`의 `watchlist` 부분을 그대로 쓰면 됩니다.
   ```json
   [
     { "name": "삼성전자", "symbol": "005930", "market": "KR", "target_above": 80000, "target_below": 60000 },
     { "name": "애플", "symbol": "AAPL", "market": "US", "exchange": "NAS", "target_above": 250 }
   ]
   ```
   `{"watchlist": [...]}` 형태로 붙여 넣어도 됩니다. 각 항목에 쓰는 값은 위 `config.json` 형식의 `watchlist[]` 설명과 같습니다.

**바꿀 때**
1. **Settings → Secrets and variables → Actions → `WATCHLIST_JSON` → Update secret**을 누릅니다.
2. 고친 목록 **전체**를 붙여 넣어 저장합니다. 시크릿은 저장한 뒤 내용을 다시 볼 수 없으니, 목표가 목록은 따로 메모해 두고 그걸 고쳐서 붙여 넣으세요.
3. 다음 실행부터 바뀐 목표가로 확인합니다.

- JSON 문법이 틀리면 실행이 실패하고, 로그에 `WATCHLIST_JSON 형식 오류: ... (2번째 줄 39번째 글자)`처럼 오류 위치가 찍힙니다. 목표가 내용은 로그에 나오지 않습니다.
- 이미 지난 가격으로 목표가를 바꾸면 다음 실행에서 바로 알림이 옵니다. 예를 들어 현재가가 6,000원인데 `target_above`를 5,800원으로 바꾸는 경우입니다.

### cron-job.org (5분마다 실행)
1. GitHub에서 fine-grained 토큰을 발급합니다. 대상은 이 저장소만, 권한은 **Actions: Read and write**만 줍니다.
2. cron-job.org에서 cronjob을 만듭니다.
   - URL: `https://api.github.com/repos/alswk516/Stock-alert/actions/workflows/alert.yml/dispatches`
   - 실행 주기: 5분마다
   - Method: `POST`
   - Body: `{"ref":"main"}`
   - Headers:
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer <토큰>`
     - `X-GitHub-Api-Version: 2022-11-28`
3. TEST RUN 결과가 `204`면 성공입니다. Actions 탭에 `workflow_dispatch` 실행이 생기는지 확인합니다.

## 문제가 생겼을 때

| 증상 | 확인할 것 |
|---|---|
| GitHub에서 실행 실패 이메일이 옴 | Actions 탭에서 해당 실행의 `python run_once.py` 로그를 봅니다. 브리핑 실패 또는 카카오·텔레그램 발송 실패입니다. 실패한 것은 다음 실행 때 다시 시도합니다. |
| 카카오 알림만 안 옴, 또는 로그에 `카카오 토큰 갱신 실패` | 로그에 `invalid_grant`(KOE322 등)가 있으면 카카오 refresh 토큰이 만료된 것입니다. 새 토큰을 받아 `KAKAO_TOKEN_JSON`을 갱신합니다. 캐시에 예전 토큰이 남아 있으면 그걸 먼저 쓰므로, Actions → Caches에서 `state-` 캐시도 지워야 합니다. 이때 알림 기록도 같이 초기화됩니다. |
| 로그에 `시세 조회 실패` | 그 종목만 이번 실행에서 건너뜁니다. 종목코드, 거래소, KIS 시세 권한을 확인합니다. |
| 알림이 늦게 옴 | Actions 탭에서 실행 간격을 확인합니다. 몇 시간 간격이면 cron-job.org가 동작하지 않는 것입니다. |
| 브리핑이 안 옴 | `telegram` 설정과 `briefing_time`을 확인합니다. 오늘 이미 보냈으면 다시 보내지 않습니다. |

## 로컬에서 실행
```bash
pip install requests
```
```bash
python stock_alert.py
```
같은 폴더에 `config.json`과 `.kakao_token.json`이 있어야 합니다. `watchlist.json`을 두면 목표가 목록은 그 파일을 씁니다. 이 모드는 종료하지 않고 `poll_seconds`마다 계속 확인합니다.

## 알아둘 점
- **공개 저장소입니다.** Actions 로그는 누구나 볼 수 있고, 알림을 보내면 로그에 종목명·현재가·목표가가 남습니다.
- 키와 토큰은 Secrets와 캐시에 보관되고 로그에는 찍히지 않습니다. 텔레그램 API 주소에는 봇 토큰이 들어가므로(`api.telegram.org/bot<토큰>/...`), 텔레그램 발송이 실패하면 오류 메시지에서 토큰을 `bot***`으로 가린 뒤 로그에 남깁니다. GitHub는 JSON 시크릿 안의 일부 값을 자동으로 가리지 못하기 때문입니다.
- 코드에서 언급하는 `kakao_auth.py`(카카오 첫 토큰 발급)와 `config.example.json`은 이 저장소에 없습니다.
