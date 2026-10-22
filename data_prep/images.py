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
