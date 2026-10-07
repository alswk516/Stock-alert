"""GitHub Actions용: 한 번 점검하고 종료. (5분마다 워크플로가 호출)"""
import sys

import stock_alert as sa

cfg = sa.load_json(sa.CONFIG_PATH)
if not cfg:
    sys.exit("config.json 없음 (워크플로가 CONFIG_JSON 시크릿으로 생성해야 함)")

kis, kakao = sa.KIS(cfg["kis"]), sa.Kakao(cfg["kakao"])
state = sa.load_json(sa.STATE_PATH, {})
errors = sa.run_cycle(cfg, kis, kakao, state)
sys.exit(1 if errors else 0)  # 카카오 발송 실패 시 Actions 실패 -> GitHub 이메일로 알림
