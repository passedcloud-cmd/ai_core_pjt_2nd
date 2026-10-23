"""
collect_painters.py
====================
위키데이터(Wikidata)에서 화가 메타데이터를 수집해 data/painters.json 으로 저장하는 스크립트.

[하는 일]
  1단계) "직업이 화가이고 + 한국어 위키백과 문서가 있는 사람"을 전부 가져온다. (모든 국적)
  1.5단계) 본업이 화가가 아닌 사람(배우·가수 등)을 걸러낸다. 제외 목록은 따로 저장한다.
  2단계) 유명한 순서(sitelinks 수)로 줄 세우고, 필요하면 상위 N명만 남긴다.
  3단계) 남은 화가들의 상세 정보(출생·사망, 국적, 사조, 장르, 대표작 등)를 가져온다.
  4단계) 보기 좋게 정리해서 JSON 파일 1개로 저장한다. (유명한 순서대로 저장됨)

[실행 방법]
  pip install requests python-dotenv
  python collect_painters.py              # 전체 화가 수집
  python collect_painters.py --top 500    # 유명한 상위 500명만 수집

[데이터 출처와 이용 조건]
  - Wikidata (https://www.wikidata.org) / 라이선스: CC0 1.0 (자유 이용, 출처 표시 의무 없음)
  - 한국어 위키백과 문서 제목·URL만 함께 저장한다. (본문은 이 스크립트에서 가져오지 않음)
"""

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 설정값
# ---------------------------------------------------------------------------
MAX_LIST_ITEMS = 10     # 대표작·사조 등 목록은 최대 몇 개까지 저장할지
DETAIL_BATCH_SIZE = 30  # 상세 정보는 한 번에 몇 명씩 요청할지
REQUEST_INTERVAL = 1.5  # 요청 사이에 몇 초 쉴지 (외부 API 예절, NF2006)
MAX_RETRIES = 3         # 실패하면 몇 번까지 다시 시도할지

OUTPUT_PATH = Path("data/painters.json")
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# .env 파일에서 연락처를 읽는다. 위키미디어는 User-Agent에 연락처를 넣으라고 요구한다.
load_dotenv()
CONTACT = os.getenv("WIKI_CONTACT_EMAIL", "your-email@example.com")
HEADERS = {
    "User-Agent": f"PainterRAG-SSAFY-StudentProject/1.0 ({CONTACT})",
    "Accept": "application/sparql-results+json",
}

# 상세 정보로 가져올 위키데이터 속성 (P번호 = 위키데이터의 '항목 이름')
DETAIL_PROPERTIES = {
    "P569": "birth",          # 출생일
    "P570": "death",          # 사망일
    "P19": "birth_place",     # 출생지
    "P27": "nationality",     # 국적
    "P135": "movements",      # 사조 (예: 인상주의)
    "P136": "genres",         # 장르 (예: 풍경화)
    "P800": "notable_works",  # 대표작
}


# ---------------------------------------------------------------------------
# 공통 함수: 위키데이터에 SPARQL 질문을 보내고 결과를 받는다.
# ---------------------------------------------------------------------------
def run_sparql(query: str) -> list[dict]:
    """SPARQL 쿼리를 보내고 결과 행(row) 목록을 돌려준다. 실패하면 잠깐 쉬고 다시 시도한다."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                SPARQL_ENDPOINT,
                data={"query": query, "format": "json"},
                headers=HEADERS,
                timeout=90,
            )
            # 429 = 너무 자주 요청함, 5xx = 서버 쪽 문제 → 기다렸다가 재시도
            if response.status_code == 429 or response.status_code >= 500:
                wait = int(response.headers.get("Retry-After", 10 * attempt))
                print(f"  ⚠ 서버 응답 {response.status_code}, {wait}초 후 재시도 ({attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()["results"]["bindings"]
        except requests.RequestException as error:
            print(f"  ⚠ 요청 실패: {error} ({attempt}/{MAX_RETRIES})")
            time.sleep(5 * attempt)
    raise RuntimeError("위키데이터 요청이 계속 실패했어요. 인터넷 연결이나 쿼리를 확인해 주세요.")


def qid(uri: str) -> str:
    """'http://www.wikidata.org/entity/Q5582' → 'Q5582' 처럼 끝의 ID만 떼어낸다."""
    return uri.rsplit("/", 1)[-1]


def split(row: dict, key: str) -> list[str]:
    """'유럽|아시아' 처럼 | 로 이어진 글자를 ['유럽', '아시아'] 목록으로 바꾼다."""
    return [v for v in row.get(key, {}).get("value", "").split("|") if v]


# ---------------------------------------------------------------------------
# 1단계: 화가 목록 가져오기 (모든 국적)
# ---------------------------------------------------------------------------
CANDIDATE_QUERY = """
SELECT ?item ?koTitle ?sitelinks
       (SAMPLE(?enLabel) AS ?nameEn)
       (SAMPLE(?koDesc) AS ?description)
       (SAMPLE(?enDesc) AS ?descriptionEn)
       (SAMPLE(?movement) AS ?hasMovement)
       (GROUP_CONCAT(DISTINCT ?continentLabel; separator="|") AS ?continents)
WHERE {
  ?item wdt:P31 wd:Q5 ;                 # 사람이고
        wdt:P106 wd:Q1028181 ;          # 직업이 화가이고
        wikibase:sitelinks ?sitelinks . # 몇 개 언어 위키에 문서가 있는지 (유명도 지표)

  # 한국어 위키백과 문서가 있는 사람만
  ?article schema:about ?item ;
           schema:isPartOf <https://ko.wikipedia.org/> ;
           schema:name ?koTitle .

  OPTIONAL { ?item rdfs:label ?enLabel . FILTER(LANG(?enLabel) = "en") }
  OPTIONAL { ?item schema:description ?koDesc . FILTER(LANG(?koDesc) = "ko") }
  OPTIONAL { ?item schema:description ?enDesc . FILTER(LANG(?enDesc) = "en") }
  OPTIONAL { ?item wdt:P135 ?movement . }   # 사조 정보가 있는지 (진짜 화가 판별용)
  # 국적 → 그 나라가 속한 대륙 (예: 프랑스 → 유럽)
  OPTIONAL {
    ?item wdt:P27/wdt:P30 ?continent .
    ?continent rdfs:label ?continentLabel . FILTER(LANG(?continentLabel) = "ko")
  }
}
GROUP BY ?item ?koTitle ?sitelinks
"""


def fetch_candidates() -> list[dict]:
    rows = run_sparql(CANDIDATE_QUERY)
    return [
        {
            "id": qid(row["item"]["value"]),
            "wiki_title": row["koTitle"]["value"],
            "sitelinks": int(row["sitelinks"]["value"]),
            "name_en": row.get("nameEn", {}).get("value", ""),
            "description": row.get("description", {}).get("value", ""),
            "description_en": row.get("descriptionEn", {}).get("value", ""),
            "has_movement": "hasMovement" in row,
            "continents": split(row, "continents"),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 1.5단계: 진짜 화가만 남기기
# ---------------------------------------------------------------------------
# 위키데이터는 직업을 여러 개 붙일 수 있어서, 본업이 배우·가수인데 '화가'도 붙은 사람이 섞인다.
# 그래서 아래 중 하나라도 맞아야 화가로 인정한다.
#   1) 한국어 소개에 '화가'가 있음 (단, '만화가'는 제외)
#   2) 영어 소개에 'painter'가 있음
#   3) 사조(인상주의 등) 정보가 있음
KO_PAINTER_PATTERN = re.compile(r"(?<!만)화가")
EN_PAINTER_PATTERN = re.compile(r"\bpainter\b", re.IGNORECASE)
EXCLUDED_PATH = Path("data/excluded_painters.json")


def is_real_painter(painter: dict) -> bool:
    return (
        bool(KO_PAINTER_PATTERN.search(painter["description"]))
        or bool(EN_PAINTER_PATTERN.search(painter["description_en"]))
        or painter["has_movement"]
    )


def filter_real_painters(candidates: list[dict]) -> list[dict]:
    kept = [p for p in candidates if is_real_painter(p)]
    excluded = [p for p in candidates if not is_real_painter(p)]

    # 제외된 사람은 따로 저장해서, 진짜 화가가 잘못 빠지지 않았는지 사람이 확인할 수 있게 한다.
    EXCLUDED_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXCLUDED_PATH.write_text(json.dumps(
        [
            {"id": p["id"], "name": p["wiki_title"], "description": p["description"],
             "description_en": p["description_en"],
             "source_url": f"https://www.wikidata.org/wiki/{p['id']}"}
            for p in sorted(excluded, key=lambda p: p["sitelinks"], reverse=True)
        ],
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    print(f"  {len(candidates)}명 중 화가로 확인된 {len(kept)}명 유지, "
          f"{len(excluded)}명 제외 (제외 목록: {EXCLUDED_PATH})")
    return kept


# ---------------------------------------------------------------------------
# 2단계: 유명한 순서로 줄 세우고, 필요하면 상위 N명만 남기기
# ---------------------------------------------------------------------------
def select_painters(candidates: list[dict], top: int | None) -> list[dict]:
    # sitelinks가 많을수록 여러 나라 위키에 문서가 있다 = 유명하다
    by_fame = sorted(candidates, key=lambda p: p["sitelinks"], reverse=True)
    selected = by_fame[:top] if top else by_fame

    # 대륙별로 몇 명인지 세어서 보여준다 (한 사람이 여러 대륙에 걸칠 수 있음)
    counts: dict[str, int] = {}
    for painter in selected:
        for continent in painter["continents"] or ["(대륙 정보 없음)"]:
            counts[continent] = counts.get(continent, 0) + 1

    print(f"  전체 {len(candidates)}명 중 {len(selected)}명 선택")
    for continent, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    - {continent}: {count}명")
    return selected


# ---------------------------------------------------------------------------
# 3단계: 상세 정보 가져오기
# ---------------------------------------------------------------------------
def build_detail_query(ids: list[str]) -> str:
    values = " ".join(f"wd:{i}" for i in ids)
    props = " ".join(f"wdt:{p}" for p in DETAIL_PROPERTIES)
    return f"""
SELECT ?item ?prop ?value ?valueLabel WHERE {{
  VALUES ?item {{ {values} }}
  VALUES ?prop {{ {props} }}
  ?item ?prop ?value .
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ko,en". }}
}}
"""


def fetch_details(painters: list[dict]) -> dict[str, dict[str, list[str]]]:
    """{화가ID: {"movements": [...], "birth": [...], ...}} 형태로 돌려준다."""
    details: dict[str, dict[str, list[str]]] = {}
    ids = [p["id"] for p in painters]

    for start in range(0, len(ids), DETAIL_BATCH_SIZE):
        batch = ids[start:start + DETAIL_BATCH_SIZE]
        print(f"  상세 정보 요청 중... ({start + len(batch)}/{len(ids)})")
        rows = run_sparql(build_detail_query(batch))

        for row in rows:
            painter_id = qid(row["item"]["value"])
            field = DETAIL_PROPERTIES[qid(row["prop"]["value"])]
            value = row["value"]["value"]

            if field in ("birth", "death"):
                text = value  # 날짜는 '1853-03-30T00:00:00Z' 같은 글자 그대로 받는다
            else:
                text = row.get("valueLabel", {}).get("value", "")
                # 한국어·영어 이름이 둘 다 없으면 'Q12345' 같은 ID가 오는데, 그건 버린다
                if not text or re.fullmatch(r"Q\d+", text):
                    continue

            bucket = details.setdefault(painter_id, {}).setdefault(field, [])
            if text not in bucket:
                bucket.append(text)

        time.sleep(REQUEST_INTERVAL)
    return details


def to_year(dates: list[str]) -> int | None:
    """'1853-03-30T00:00:00Z' → 1853. 기원전(-)도 처리한다."""
    for date in dates:
        match = re.match(r"^(-?\d+)-", date)
        if match:
            return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# 4단계: 정리해서 저장하기
# ---------------------------------------------------------------------------
def build_record(rank: int, painter: dict, detail: dict[str, list[str]]) -> dict:
    title = painter["wiki_title"]
    return {
        "id": painter["id"],                       # 위키데이터 ID (나중에 청크 식별자로 사용)
        "rank": rank,                              # 유명도 순위 (1위 = 가장 유명)
        "name": title,                             # 한국어 이름 (= 한국어 위키 문서 제목)
        "name_en": painter["name_en"],
        "description": painter["description"],     # 한 줄 소개
        "description_en": painter["description_en"],   # 영어 소개 (본업이 화가인지 가릴 때 씀)
        "continents": painter["continents"],       # 예: ["유럽"]
        "nationality": detail.get("nationality", [])[:MAX_LIST_ITEMS],
        "birth_year": to_year(detail.get("birth", [])),
        "death_year": to_year(detail.get("death", [])),
        "birth_place": detail.get("birth_place", [""])[0],
        "movements": detail.get("movements", [])[:MAX_LIST_ITEMS],
        "genres": detail.get("genres", [])[:MAX_LIST_ITEMS],
        "notable_works": detail.get("notable_works", [])[:MAX_LIST_ITEMS],
        "wiki_title": title,
        "wiki_url": "https://ko.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
        "sitelinks": painter["sitelinks"],
        "source": "Wikidata (CC0 1.0)",
        "source_url": f"https://www.wikidata.org/wiki/{painter['id']}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="위키데이터에서 화가 메타데이터 수집")
    parser.add_argument("--top", type=int, default=None,
                        help="유명한 상위 N명만 수집 (생략하면 전체)")
    args = parser.parse_args()

    print("1단계) 화가 목록을 가져오는 중...")
    candidates = fetch_candidates()
    time.sleep(REQUEST_INTERVAL)

    print("1.5단계) 본업이 화가가 아닌 사람을 걸러내는 중...")
    candidates = filter_real_painters(candidates)

    print("2단계) 유명한 순서로 정렬하는 중...")
    selected = select_painters(candidates, args.top)

    print("3단계) 상세 정보를 가져오는 중... (전체 수집이면 몇 분 걸려요)")
    details = fetch_details(selected)

    print("4단계) 파일로 저장하는 중...")
    records = [
        build_record(rank, p, details.get(p["id"], {}))
        for rank, p in enumerate(selected, start=1)
    ]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 완료! 화가 {len(records)}명을 {OUTPUT_PATH} 에 저장했어요.")
    print("예시 1건:")
    print(json.dumps(records[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
