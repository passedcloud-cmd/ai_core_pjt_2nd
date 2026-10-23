"""
data_prep/images.py — 화가 작품 이미지 정보를 읽고, 원하는 크기의 이미지 주소를 만든다
====================================================================================
data/painter_images.json (collect_images.py가 만든 파일)을 읽어서 화면에 넘겨준다.
이미지는 RAG 답변에는 쓰이지 않고 '보여주기용'이다.

위키미디어 썸네일 주소 규칙:
  https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/파일.jpg/1280px-파일.jpg
                                                                  ^^^^ 이 숫자만 바꾸면 다른 크기
  단, 정해진 폭(config.THUMB_WIDTHS: 250·330·500·960·1280·1920)만 허용되고 나머지는 400 오류가 난다.
"""

import json
from functools import lru_cache
from urllib.parse import quote

import config


@lru_cache
def load_all_images() -> dict:
    """{화가ID: {"portrait", "works", "article_images"}} — 파일이 없으면 빈 dict"""
    if not config.IMAGES_PATH.exists():
        return {}
    return json.loads(config.IMAGES_PATH.read_text(encoding="utf-8")).get("painters", {})


def painter_images(painter_id: str) -> dict:
    """화가 1명의 이미지 묶음. 없으면 빈 묶음."""
    return load_all_images().get(painter_id) or {"portrait": None, "works": [], "article_images": []}


def thumb(image: dict | None, width: int) -> str | None:
    """
    원하는 폭(width)에 가장 가까운 '허용된 폭'의 썸네일 주소를 돌려준다.
    원본보다 크게는 요청하지 않는다 (원본보다 큰 썸네일은 만들어지지 않음).
    """
    if not image or not image.get("thumb"):
        return None
    url = image["thumb"]
    if "/1280px-" not in url:
        return url  # 원본이 1280px보다 작아서 원본 주소가 저장된 경우 → 그대로 사용

    limit = min(width, image.get("width") or width)
    allowed = [w for w in config.THUMB_WIDTHS if w <= limit] or [config.THUMB_WIDTHS[0]]
    return url.replace("/1280px-", f"/{allowed[-1]}px-")


def work_caption(work: dict) -> str:
    """'별이 빛나는 밤 (1889) · 뉴욕 근대미술관'"""
    title = work.get("title") or work.get("caption") or ""
    if work.get("year"):
        title += f" ({work['year']})"
    if work.get("collection"):
        title += f" · {work['collection']}"
    return title


# ---------------------------------------------------------------------------
# 작품 더 보기: 외부 사이트로 연결하는 링크 (이미지를 가져오지 않고 원본 페이지로 보냄)
# ---------------------------------------------------------------------------
# 현대 화가·한국 화가는 저작권 때문에 공개 이미지가 거의 없어서, 이미지를 가져오는 대신
# 작품을 볼 수 있는 공식 페이지로 연결한다. 주소 형식은 2026-10-23에 직접 열어 보고 확인했다.
#   - 국립현대미술관 소장품 '검색'은 주소에 검색어를 넣어도 적용되지 않아서(POST 방식), 작품별 상세 페이지를 쓴다
#   - WikiArt는 영문 이름으로 주소를 만들어야 해서 한국 화가 등은 깨진 링크가 되므로 넣지 않았다
KOREA = {"조선", "한국", "대한민국", "대한제국", "고려", "일제강점기", "조선민주주의인민공화국"}


@lru_cache
def _mmca_by_painter() -> dict[str, list[dict]]:
    """국립현대미술관 작품 상세 페이지 목록 (index_mmca.py가 화가와 연결해 둔 것)"""
    if not config.MMCA_DOCS_PATH.exists():
        return {}
    result: dict[str, list[dict]] = {}
    for w in json.loads(config.MMCA_DOCS_PATH.read_text(encoding="utf-8")):
        if w.get("painter_id") and w.get("url"):
            result.setdefault(w["painter_id"], []).append(
                {"title": w["title"], "url": quote(w["url"], safe=":/?&=%")}  # 한글 작가명이 든 주소를 안전하게
            )
    return result


def museum_works(painter_id: str) -> list[dict]:
    """국립현대미술관에서 볼 수 있는 이 화가의 작품 [{title, url}] (없으면 빈 목록)"""
    return _mmca_by_painter().get(painter_id, [])


def external_links(painter: dict) -> list[dict]:
    """
    '작품 더 보기' 링크 [{label, url, note}]. 화가 이름으로 검색 페이지를 연다.
    한국 화가에게만 e뮤지엄(국립중앙박물관 등 국공립 박물관 소장품)을 보여준다.
    """
    name = painter["name"].split(" (")[0]  # '정선 (화가)' → '정선'
    links = []
    if KOREA & set(painter.get("nationality", [])):
        links.append({"label": "e뮤지엄 소장품", "url": f"https://www.emuseum.go.kr/headerSearch?keyword={quote(name)}",
                      "note": "국립중앙박물관 등 국공립 박물관 소장품 검색"})
    links.append({"label": "Google Arts & Culture", "url": f"https://artsandculture.google.com/search?q={quote(name)}",
                  "note": "세계 미술관 소장품 · 온라인 전시"})
    links.append({"label": "이미지 검색", "url": f"https://www.google.com/search?tbm=isch&q={quote(name + ' 작품')}",
                  "note": "구글 이미지 검색 결과"})
    return links
