"""
collect_images.py — 화가별 작품 이미지 정보 수집
=================================================
화면에 화가의 작품을 보여주기 위해, 이미지 '주소'와 작품 정보를 모아 data/painter_images.json 에 저장한다.
이미지 파일 자체는 내려받지 않는다. (화면이 위키미디어 썸네일 주소에서 바로 불러옴)

[출처 두 가지]
  🎨 작품 (works)           : 위키데이터에서 '작가(P170) = 이 화가'이고 이미지(P18)가 있는 작품.
                              제목·제작 연도·소장처가 붙어 있는 '확실한 작품'이다. 유명한 순(sitelinks)으로 저장.
  🖼️ 관련 이미지 (article)  : 그 화가의 한국어·영어 위키백과 문서 본문에 실린 이미지.
                              한국 화가처럼 위키데이터에 작품이 거의 없는 경우를 채워 준다.
                              단, 화가 사진·미술관 건물 같은 것도 섞일 수 있어서 '작품'과 따로 둔다.
  + 화가 초상 (portrait)    : 위키데이터 화가 항목의 대표 이미지

[실행 방법]
  python collect_images.py --top 20     # 유명한 화가 20명만 (시험용)
  python collect_images.py              # 전체 1,343명 (위키미디어 예절 때문에 10~20분 걸림)

[출처와 이용 조건 기록 (NF2007)]
  작품 정보: Wikidata (CC0 1.0) / 이미지: Wikimedia Commons·위키백과 — 이미지마다 원본 페이지 주소(page)를 저장한다.
"""

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import unquote

import config
from collect_painters import qid, run_sparql
from collector.wikipedia import _get
from data_prep.painters import load_painters

SPARQL_BATCH = 40      # 위키데이터에 한 번에 물어볼 화가 수
TITLE_BATCH = 20       # 위키백과에 한 번에 물어볼 문서 수
FILE_BATCH = 50        # 이미지 정보를 한 번에 물어볼 파일 수 (위키미디어 API 최대 50)
API = {
    "commons": "https://commons.wikimedia.org/w/api.php",
    "ko": "https://ko.wikipedia.org/w/api.php",
    "en": "https://en.wikipedia.org/w/api.php",
}

# 위키백과 문서 속 이미지 중 작품이 아닐 게 뻔한 파일 이름 (아이콘·국기·서명·지도 등)
NOISE = re.compile(
    r"picto|icon|logo|flag|signature|서명|symbol|commons-|wiki|map|locator|arrow|question|edit|stub|disambig|국기|문장",
    re.IGNORECASE,
)


FAILED = []  # 건너뛴 묶음 기록 (끝에 몇 개였는지 알려줌)


def skip(stage: str, error: Exception) -> None:
    """한 묶음이 실패해도 전체를 멈추지 않고 건너뛴다. (오늘 414 오류로 전체가 날아간 문제 대응)"""
    FAILED.append(stage)
    print(f"  ⚠️ {stage} 실패 → 이 묶음만 건너뛰고 계속해요 ({type(error).__name__}: {str(error)[:80]})")


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def value(row: dict, key: str) -> str:
    return row.get(key, {}).get("value", "")


def file_name(uri: str) -> str:
    """
    위키데이터 이미지 주소 → 파일 이름.
    '.../Special:FilePath/Monet%20-%20Impression%2C%20Sunrise.jpg' → 'Monet - Impression, Sunrise.jpg'
    (주소에는 띄어쓰기가 %20처럼 바뀌어 있어서 원래 글자로 되돌려야 Commons에서 찾을 수 있다)
    """
    return unquote(uri.rsplit("/", 1)[-1]) if uri else ""


# ---------------------------------------------------------------------------
# ① 위키데이터: 작품 (작가 = 이 화가, 이미지 있음)
# ---------------------------------------------------------------------------
WORKS_QUERY = """
SELECT ?creator ?work ?sitelinks
       (SAMPLE(?img) AS ?image) (SAMPLE(?ko) AS ?titleKo) (SAMPLE(?en) AS ?titleEn)
       (MIN(YEAR(?date)) AS ?year) (SAMPLE(?colLabel) AS ?collection) (SAMPLE(?typeLabel) AS ?type)
WHERE {
  VALUES ?creator { %s }
  ?work wdt:P170 ?creator ; wdt:P18 ?img ; wikibase:sitelinks ?sitelinks .
  OPTIONAL { ?work rdfs:label ?ko . FILTER(LANG(?ko) = "ko") }
  OPTIONAL { ?work rdfs:label ?en . FILTER(LANG(?en) = "en") }
  OPTIONAL { ?work wdt:P571 ?date . }                                   # 제작 시기
  OPTIONAL { ?work wdt:P195 ?col . ?col rdfs:label ?colLabel .          # 소장처
             FILTER(LANG(?colLabel) IN ("ko", "en")) }
  OPTIONAL { ?work wdt:P31 ?t . ?t rdfs:label ?typeLabel . FILTER(LANG(?typeLabel) = "ko") }  # 회화·소묘 등
}
GROUP BY ?creator ?work ?sitelinks
"""


def fetch_works(painter_ids: list[str]) -> dict[str, list[dict]]:
    works = defaultdict(list)
    for n, batch in enumerate(chunks(painter_ids, SPARQL_BATCH), 1):
        try:
            rows = run_sparql(WORKS_QUERY % " ".join(f"wd:{i}" for i in batch))
        except Exception as error:
            skip(f"① 작품 조회 {n}번째 묶음", error)
            continue
        for r in rows:
            works[qid(value(r, "creator"))].append(
                {
                    "id": qid(value(r, "work")),
                    "title": value(r, "titleKo") or value(r, "titleEn") or qid(value(r, "work")),
                    "title_en": value(r, "titleEn"),
                    "file": file_name(value(r, "image")),
                    "year": int(value(r, "year")) if value(r, "year").lstrip("-").isdigit() else None,
                    "collection": value(r, "collection"),
                    "type": value(r, "type"),
                    "sitelinks": int(value(r, "sitelinks") or 0),
                }
            )
        print(f"  ① 작품 조회 {min(n * SPARQL_BATCH, len(painter_ids))}/{len(painter_ids)}명 (이번 묶음 {len(rows)}건)")

    # 화가마다 '유명한 순 → 한국어 제목 있는 것 → 회화 우선'으로 줄 세워 상위 N개만
    result = {}
    for pid, items in works.items():
        items.sort(key=lambda w: (-w["sitelinks"], w["title"] == w["title_en"], w["type"] != "회화"))
        seen, kept = set(), []
        for w in items:
            if w["file"] not in seen:  # 같은 이미지를 쓰는 작품(연작 항목 등)은 한 번만
                seen.add(w["file"])
                kept.append(w)
        result[pid] = kept[: config.MAX_WORKS_PER_PAINTER]
    return result


# ---------------------------------------------------------------------------
# ② 위키데이터: 화가 초상 + 영어 위키백과 문서 제목
# ---------------------------------------------------------------------------
PAINTER_QUERY = """
SELECT ?p (SAMPLE(?img) AS ?image) (SAMPLE(?enTitle) AS ?en) WHERE {
  VALUES ?p { %s }
  OPTIONAL { ?p wdt:P18 ?img . }
  OPTIONAL { ?a schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?enTitle . }
}
GROUP BY ?p
"""


def fetch_painter_info(painter_ids: list[str]) -> dict[str, dict]:
    info = {}
    for batch in chunks(painter_ids, 200):
        try:
            rows = run_sparql(PAINTER_QUERY % " ".join(f"wd:{i}" for i in batch))
        except Exception as error:
            skip("② 화가 초상 조회", error)
            continue
        for r in rows:
            info[qid(value(r, "p"))] = {
                "portrait": file_name(value(r, "image")) or None,
                "en_title": value(r, "en") or None,
            }
    print(f"  ② 화가 초상·영어 문서 제목 조회 완료 ({len(info)}명)")
    return info


# ---------------------------------------------------------------------------
# ③ 위키백과: 문서 본문에 실린 이미지 목록
# ---------------------------------------------------------------------------
def fetch_article_images(lang: str, titles: dict[str, str]) -> dict[str, list[str]]:
    """{화가ID: 문서 제목} → {화가ID: [파일명, ...]}"""
    by_title = {t: pid for pid, t in titles.items()}
    found = defaultdict(list)
    for n, batch in enumerate(chunks(list(by_title), TITLE_BATCH), 1):
        params = {"action": "query", "titles": "|".join(batch), "prop": "images", "imlimit": "max", "redirects": 1}
        while True:  # 결과가 많으면 '이어서 받기(continue)'로 끝까지 받는다
            try:
                d = _get(params, API[lang])
            except Exception as error:
                skip(f"③ {lang} 문서 이미지 {n}번째 묶음", error)
                break
            # 넘겨주기(redirect)로 제목이 바뀐 경우 원래 제목으로 되돌려 화가를 찾는다
            back = {x["to"]: x["from"] for x in d.get("query", {}).get("redirects", [])}
            back.update({x["to"]: x["from"] for x in d.get("query", {}).get("normalized", [])})
            for page in d.get("query", {}).get("pages", []):
                title = back.get(page["title"], page["title"])
                title = back.get(title, title)
                pid = by_title.get(title)
                if pid:
                    found[pid] += [i["title"].split(":", 1)[1] for i in page.get("images", [])]
            if "continue" not in d:
                break
            params = {**params, **d["continue"]}
        if n % 10 == 0 or n * TITLE_BATCH >= len(by_title):
            print(f"  ③ {lang} 위키백과 문서 이미지 {min(n * TITLE_BATCH, len(by_title))}/{len(by_title)}개 문서")
    return found


# ---------------------------------------------------------------------------
# ④ 이미지 정보: 크기·1280px 썸네일 주소·원본 페이지
# ---------------------------------------------------------------------------
def fetch_file_info(lang: str, files: set[str]) -> dict[str, dict]:
    """
    파일마다 크기와 썸네일 주소를 받는다. 파일이 실제로 있는 위키(lang)에 물어봐야 한다.
    (영어 위키백과에만 따로 올라간 파일은 Commons에 없기 때문)
    """
    info = {}
    files = sorted(files)
    for n, batch in enumerate(chunks(files, FILE_BATCH), 1):
        try:
            d = _get(
            {
                "action": "query",
                "titles": "|".join(f"File:{f}" for f in batch),
                "prop": "imageinfo",
                "iiprop": "url|size",
                "iiurlwidth": 1280,
            },
            API[lang],
            )
        except Exception as error:
            skip(f"④ {lang} 이미지 정보 {n}번째 묶음", error)
            continue
        norm = {x["to"]: x["from"] for x in d.get("query", {}).get("normalized", [])}
        for page in d.get("query", {}).get("pages", []):
            if not page.get("imageinfo"):
                continue
            ii = page["imageinfo"][0]
            name = norm.get(page["title"], page["title"]).split(":", 1)[1]
            info[name] = {
                "width": ii.get("width", 0),
                "height": ii.get("height", 0),
                "thumb": ii.get("thumburl") or ii.get("url"),  # 1280px (원본이 더 작으면 원본)
                "page": ii.get("descriptionurl", ""),           # 원본 파일 페이지 (출처 표시용, NF2007)
            }
        if n % 20 == 0 or n * FILE_BATCH >= len(files):
            print(f"  ④ {lang} 이미지 정보 {min(n * FILE_BATCH, len(files))}/{len(files)}개")
    return info


def is_big_enough(meta: dict | None) -> bool:
    return bool(meta) and min(meta["width"], meta["height"]) >= config.MIN_IMAGE_SIDE


def caption_from_file(name: str) -> str:
    """'White_Ox_(1954)_-_Lee_Jung_Seob.jpg' → 'White Ox (1954) - Lee Jung Seob'"""
    return re.sub(r"\.[a-zA-Z0-9]+$", "", name).replace("_", " ").strip()


# ---------------------------------------------------------------------------
# 전체 흐름
# ---------------------------------------------------------------------------
def main(top: int | None) -> None:
    painters = load_painters(config.PAINTERS_PATH)
    if top:
        painters = painters[:top]
    ids = [p["id"] for p in painters]
    print(f"🎨 대상 화가 {len(ids)}명\n")

    works = fetch_works(ids)
    info = fetch_painter_info(ids)

    # ③ 문서 속 이미지 (한국어 문서는 painters.json의 wiki_title, 영어 문서는 위키데이터에서 받은 제목)
    article = {"ko": fetch_article_images("ko", {p["id"]: p["wiki_title"] for p in painters if p.get("wiki_title")})}
    article["en"] = fetch_article_images("en", {pid: v["en_title"] for pid, v in info.items() if v["en_title"]})

    # ④ 이미지 정보: 작품·초상은 Commons, 문서 속 이미지는 그 문서가 있는 위키에 묻는다
    commons_files = {w["file"] for ws in works.values() for w in ws} | {v["portrait"] for v in info.values() if v["portrait"]}
    meta = {"commons": fetch_file_info("commons", commons_files)}
    for lang in ("ko", "en"):
        files = {f for fs in article[lang].values() for f in fs if not NOISE.search(f) and not f.lower().endswith((".svg", ".gif"))}
        meta[lang] = fetch_file_info(lang, files)

    # ⑤ 화가별로 정리
    result, stats = {}, defaultdict(int)
    for p in painters:
        pid = p["id"]
        portrait_file = info.get(pid, {}).get("portrait")
        portrait = None
        if portrait_file and meta["commons"].get(portrait_file):
            portrait = {"file": portrait_file, **meta["commons"][portrait_file]}

        kept_works = []
        for w in works.get(pid, []):
            m = meta["commons"].get(w["file"])
            if m:  # 이미지 정보가 확인된 작품만
                kept_works.append({**w, **m, "source": "Wikidata · Wikimedia Commons"})

        used = {w["file"] for w in kept_works} | ({portrait_file} if portrait_file else set())
        extra = []
        for lang in ("ko", "en"):
            for f in article[lang].get(pid, []):
                m = meta[lang].get(f)
                if f in used or not is_big_enough(m):
                    continue
                used.add(f)
                extra.append({"file": f, "caption": caption_from_file(f), **m,
                              "source": f"{'한국어' if lang == 'ko' else '영어'} 위키백과 문서"})
        extra = extra[: config.MAX_ARTICLE_IMAGES]

        result[pid] = {"name": p["name"], "portrait": portrait, "works": kept_works, "article_images": extra}
        stats["works"] += len(kept_works)
        stats["extra"] += len(extra)
        stats["portrait"] += bool(portrait)
        stats["has_any"] += bool(kept_works or extra)
        stats["has_works"] += bool(kept_works)

    out = {"collected_at": datetime.now().isoformat(timespec="seconds"), "painters": result}
    config.IMAGES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    n = len(painters)
    print(f"\n✅ 저장: {config.IMAGES_PATH}")
    print(f"  - 작품 이미지 {stats['works']:,}개 (작품이 1개 이상인 화가 {stats['has_works']}/{n}명)")
    print(f"  - 문서 속 관련 이미지 {stats['extra']:,}개")
    print(f"  - 화가 초상 {stats['portrait']}/{n}명")
    print(f"  - 이미지가 하나라도 있는 화가 {stats['has_any']}/{n}명 ({stats['has_any'] / n:.0%})")
    print(f"  - 건너뛴 묶음 {len(FAILED)}개" + (f": {', '.join(FAILED)}" if FAILED else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="화가별 작품 이미지 정보 수집")
    parser.add_argument("--top", type=int, help="유명한 상위 N명만 수집 (시험용)")
    main(parser.parse_args().top)
