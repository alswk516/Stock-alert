"""보유종목 목표가 알림(카카오톡) + 매일 시황 브리핑(텔레그램).

- 목표가 도달  -> 카카오톡 '나에게 보내기'
- 매일 브리핑  -> 텔레그램 (국제 시황 뉴스 3건 + 보유종목 종가)
"""
import json
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
KIS_TOKEN_PATH = BASE / ".kis_token.json"
KAKAO_TOKEN_PATH = BASE / ".kakao_token.json"
STATE_PATH = BASE / ".alert_state.json"

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(BASE / "stock_alert.log", encoding="utf-8")],
)
log = logging.getLogger("alert")


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- KIS
class KIS:
    def __init__(self, cfg):
        self.key = cfg["app_key"]
        self.secret = cfg["app_secret"]
        self.base = (
            "https://openapivts.koreainvestment.com:29443"
            if cfg.get("mock", False)
            else "https://openapi.koreainvestment.com:9443"
        )
        self.token = None

    def _get_token(self):
        cached = load_json(KIS_TOKEN_PATH)
        if cached and cached.get("expires_at", 0) > time.time() + 300:
            return cached["access_token"]
        r = requests.post(
            f"{self.base}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        token = d["access_token"]
        save_json(KIS_TOKEN_PATH, {"access_token": token, "expires_at": time.time() + int(d.get("expires_in", 86400))})
        log.info("KIS 토큰 발급 완료")
        return token

    def _headers(self, tr_id):
        if not self.token:
            self.token = self._get_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.key,
            "appsecret": self.secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path, tr_id, params):
        for attempt in (1, 2):
            r = requests.get(f"{self.base}{path}", headers=self._headers(tr_id), params=params, timeout=10)
            if r.status_code in (401, 403) and attempt == 1:  # 토큰 만료 -> 재발급
                KIS_TOKEN_PATH.unlink(missing_ok=True)
                self.token = None
                continue
            r.raise_for_status()
            return r.json()

    def price_kr(self, code):
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        return float(d["output"]["stck_prpr"])

    def quote_kr(self, code):
        """(현재가/종가, 전일대비%) - KIS 기준."""
        d = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )["output"]
        return float(d["stck_prpr"]), float(d.get("prdy_ctrt") or 0)

    def price_us(self, symbol, excd):
        """설정한 거래소에서 시세가 비어 있으면 다른 거래소(NYS/AMS/NAS)도 시도."""
        d = {}
        for ex in [excd] + [e for e in ("NYS", "AMS", "NAS") if e != excd]:
            d = self._get(
                "/uapi/overseas-price/v1/quotations/price",
                "HHDFS00000300",
                {"AUTH": "", "EXCD": ex, "SYMB": symbol},
            )
            out = d.get("output") or {}
            last = out.get("last") or out.get("base")
            if last not in (None, ""):
                return float(last)
        raise RuntimeError(f"{symbol} 시세 없음 (거래소/시세 권한 확인): {d.get('msg1', '')}")


# ---------------------------------------------------------------- Kakao (목표가 알림)
class Kakao:
    def __init__(self, cfg):
        self.rest_key = cfg["rest_api_key"]
        self.client_secret = cfg.get("client_secret", "")

    def _refresh(self):
        tok = load_json(KAKAO_TOKEN_PATH)
        if not tok or "refresh_token" not in tok:
            raise RuntimeError("카카오 토큰이 없습니다. 먼저 `py kakao_auth.py` 를 실행하세요.")
        data = {"grant_type": "refresh_token", "client_id": self.rest_key, "refresh_token": tok["refresh_token"]}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        r = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=10)
        r.raise_for_status()
        d = r.json()
        tok["access_token"] = d["access_token"]
        if d.get("refresh_token"):  # 갱신 시 새 refresh_token이 오면 교체
            tok["refresh_token"] = d["refresh_token"]
        save_json(KAKAO_TOKEN_PATH, tok)
        return tok["access_token"]

    def send(self, text):
        tok = load_json(KAKAO_TOKEN_PATH, {})
        access = tok.get("access_token") or self._refresh()
        template = {
            "object_type": "text",
            "text": text[:200],  # 카카오 텍스트 템플릿 200자 제한
            "link": {"web_url": "https://m.stock.naver.com", "mobile_web_url": "https://m.stock.naver.com"},
        }
        for attempt in (1, 2):
            r = requests.post(
                "https://kapi.kakao.com/v2/api/talk/memo/default/send",
                headers={"Authorization": f"Bearer {access}"},
                data={"template_object": json.dumps(template, ensure_ascii=False)},
                timeout=10,
            )
            if r.status_code == 401 and attempt == 1:
                access = self._refresh()
                continue
            r.raise_for_status()
            return


# ---------------------------------------------------------------- Telegram (브리핑)
class Telegram:
    def __init__(self, cfg):
        self.token = cfg["bot_token"]
        self.chat_id = cfg["chat_id"]

    def send(self, text):
        # 텔레그램 한 메시지 최대 4096자 -> 넘으면 줄 단위로 분할
        chunks, cur = [], ""
        for line in text.split("\n"):
            if len(cur) + len(line) + 1 > 3900:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        chunks.append(cur)
        for c in chunks:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": c.strip(), "disable_web_page_preview": True},
                timeout=10,
            )
            r.raise_for_status()


# ---------------------------------------------------------------- 시장 시간
def kr_open(now=None):
    n = (now or datetime.now(KST)).astimezone(KST)
    return n.weekday() < 5 and (9, 0) <= (n.hour, n.minute) <= (15, 30)


def us_open(now=None):
    """프리마켓(04:00)~애프터마켓(20:00) ET, 평일. 서머타임은 zoneinfo가 자동 처리."""
    n = (now or datetime.now(NY)).astimezone(NY)
    return n.weekday() < 5 and 4 <= n.hour < 20


# ---------------------------------------------------------------- 점검 1회
def fmt(price, market):
    return f"{price:,.0f}원" if market == "KR" else f"${price:,.2f}"


def run_cycle(cfg, kis, kakao, state):
    """브리핑(텔레그램) + 목표가 확인(카카오). 발송 실패 횟수를 반환."""
    watch = cfg["watchlist"]
    hh, mm = map(int, cfg.get("briefing_time", "07:00").split(":"))
    errors = 0

    # 매일 브리핑 (설정 시각 이후 하루 1회, 늦게 실행돼도 그날 첫 사이클에 발송)
    now = datetime.now(KST)
    today = f"{now:%Y-%m-%d}"
    if cfg.get("telegram") and state.get("_briefing") != today and (now.hour, now.minute) >= (hh, mm):
        try:
            import briefing
            text = briefing.build_text(cfg, now, kis)
            if text:
                Telegram(cfg["telegram"]).send(text)
                state["_briefing"] = today
                log.info("브리핑 발송 완료")
            else:
                log.warning("브리핑 내용을 가져오지 못함 (다음 주기 재시도)")
        except Exception as e:
            log.error("브리핑 실패(다음 주기 재시도): %s", e)
            errors += 1
        save_json(STATE_PATH, state)

    # 목표가 확인
    for w in watch:
        market = w.get("market", "KR")
        if (market == "KR" and not kr_open()) or (market == "US" and not us_open()):
            continue
        key = f'{market}:{w["symbol"]}'
        try:
            price = kis.price_kr(w["symbol"]) if market == "KR" else kis.price_us(w["symbol"], w.get("exchange", "NAS"))
        except Exception as e:
            log.warning("%s 시세 조회 실패: %s", w["name"], e)
            continue

        st = state.setdefault(key, {})
        checks = [
            ("above", w.get("target_above"), price >= w["target_above"] if w.get("target_above") else False, "목표가(상한) 도달 🔺"),
            ("below", w.get("target_below"), price <= w["target_below"] if w.get("target_below") else False, "하한가 도달 🔻"),
        ]
        for side, target, hit, label in checks:
            if target is None:
                continue
            if hit and not st.get(side):
                msg = f'[{w["name"]}] {label}\n현재가 {fmt(price, market)} / 목표 {fmt(target, market)}\n{datetime.now(KST):%m/%d %H:%M}'
                try:
                    kakao.send(msg)
                    st[side] = True  # 발송 성공 시에만 '알림 완료' 처리
                    log.info("알림 발송: %s", msg.replace("\n", " | "))
                except Exception as e:
                    log.error("카카오 발송 실패(다음 주기에 재시도): %s", e)
                    errors += 1
            elif not hit and st.get(side):
                # 가격이 목표에서 충분히 벗어나면(1%) 재알림 가능하도록 초기화
                far = price < target * 0.99 if side == "above" else price > target * 1.01
                if far:
                    st[side] = False
        save_json(STATE_PATH, state)
        time.sleep(0.3)  # KIS 호출 제한 여유
    return errors


def main():
    cfg = load_json(CONFIG_PATH)
    if not cfg:
        sys.exit("config.json 이 없습니다. config.example.json 을 복사해서 작성하세요.")
    kis, kakao = KIS(cfg["kis"]), Kakao(cfg["kakao"])
    interval = max(int(cfg.get("poll_seconds", 10)), 2)
    state = load_json(STATE_PATH, {})
    log.info("감시 시작 (간격 %ss)", interval)
    while True:
        run_cycle(cfg, kis, kakao, state)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("종료")
