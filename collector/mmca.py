"""
collector/mmca.py — 국립현대미술관 소장작품 API와 통신하는 도구 (F2016 두 번째 출처)
==================================================================================
문화데이터광장(culture.go.kr)의 '국립현대미술관_소장작품' API에서 작품 설명을 받아온다.

API를 직접 써 보고 알게 된 점 (2026-10-22 확인):
  - 전체 112건만 제공된다. (미술관 소장품 전체가 아니라 일부)
  - keyword(검색어)는 무시된다 → 전체를 한 번에 받아서 우리가 거른다.
  - creator(작가)에는 '누리집'만 들어 있다 → 작가 이름은 url 안의 artistnm 값에서 꺼낸다.
  - 이미지 주소(referenceIdentifier)는 비어 있다. 대신 description(학예사 작품 설명)이 평균 600자로 알차다.
"""

import re
import time
from urllib.parse import parse_qs, urlparse

import requests

import config


def fetch_all(page_size: int = 1000) -> list[dict]:
    """전체 소장작품 목록을 받는다. (페이지를 넘겨 가며 totalCount만큼, 실패하면 재시도)"""
    if not config.MMCA_API_KEY:
        raise SystemExit("❌ .env 파일에 MMCA_API_KEY가 없어요. 문화데이터광장에서 발급받은 키를 넣어 주세요.")

    items, page = [], 1
    while True:
        for attempt in range(1, config.WIKI_MAX_RETRIES + 1):
            try:
                response = requests.get(
                    config.MMCA_API_URL,
                    params={"serviceKey": config.MMCA_API_KEY, "numOfRows": page_size, "pageNo": page},
                    headers={"Accept": "application/json"},  # 기본은 XML이라 JSON으로 달라고 요청
                    timeout=60,
                )
                response.raise_for_status()
                body = response.json()["response"]["body"]
                break
            except (requests.RequestException, KeyError, ValueError) as error:
                if attempt == config.WIKI_MAX_RETRIES:
                    raise
                print(f"  ⚠️ 국립현대미술관 API 요청 실패({type(error).__name__}). {5 * attempt}초 후 다시 시도할게요.")
                time.sleep(5 * attempt)

        batch = (body.get("items") or {}).get("item") or []
        items += batch if isinstance(batch, list) else [batch]  # 1건이면 목록이 아니라 dict로 올 수 있음
        if not batch or len(items) >= int(body.get("totalCount") or 0):
            return items
        page += 1
        time.sleep(1.0)  # 외부 API 예절 (NF2006)


def clean_description(text: str | None) -> str:
    """'<br>' 같은 HTML 줄바꿈을 진짜 줄바꿈으로, 나머지 태그는 지우고 공백 정리"""
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def parse_item(item: dict) -> dict:
    """API 원본 1건 → 우리가 쓸 모양 {work_id, title, artist, description, url, reg_date}"""
    query = parse_qs(urlparse(item.get("url") or "").query)
    return {
        "work_id": query.get("wrkMngNo", [""])[0],   # 미술관 작품 관리번호 (청크 ID에 사용)
        "title": (item.get("title") or "").strip(),
        "artist": query.get("artistnm", [""])[0],     # creator가 '누리집'이라 url에서 작가명을 꺼냄
        "description": clean_description(item.get("description")),
        "url": item.get("url") or "",
        "reg_date": item.get("regDate") or "",
    }
