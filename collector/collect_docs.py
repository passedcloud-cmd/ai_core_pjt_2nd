"""
collector/collect_docs.py  (요구사항 F2004 — 외부 문서 수집, 프롬프트 2 / NF2002 수집 캐시)
==========================================================================================
흐름:
  1) 프롬프트 1로 검색어를 뽑는다 (F2003). 첫 검색어 = 화가 본인 문서.
  2) 화가 본인 문서는 선별·길이 규칙을 건너뛰고 '무조건' 수집한다.
  3) 나머지 검색어마다 위키백과 후보(제목+요약)를 모은다.
  4) 프롬프트 2가 제목·요약만 보고 고른 문서만 본문을 받는다. (본문 받기 전에 버려서 호출 절약)
  5) 본문이 너무 짧은 문서는 버린다.
  6) '화가+검색어' 조합을 캐시에 적어서, 다음에 같은 검색어가 나오면 다시 수집하지 않는다. (NF2002)

저장 위치:
  data/wiki_docs/{문서ID}.json   문서 본문 + 원문 URL + 관련 화가 (F2005 색인 재료)
  data/collect_cache.json        이미 수집한 '화가ID|검색어' 기록 + '화가ID|질문'별 검색어 기록

[확인 방법]  (명세서 4.2 검증)
  python -m collector.collect_docs --painter "클로드 모네" --question "수련 연작은 몇 점이고 어디 있어?"
  → 같은 명령을 한 번 더 실행하면 '이미 수집함'으로 건너뛰는지 확인
"""

import argparse
import json
from datetime import datetime

import config
from collector import wikipedia
from collector.query_extractor import extract_json_array, extract_queries, get_llm
from data_prep.painters import find_painter, load_painters
from rag.prompts import DOC_SELECTION_PROMPT


# ---------------------------------------------------------------------------
# 저장 도우미: 캐시와 문서 파일
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cache_key(painter_id: str, query: str) -> str:
    """'Q296|수련(모네)' 처럼 화가ID + 검색어(띄어쓰기 무시)로 만든다."""
    return f"{painter_id}|{query.replace(' ', '').lower()}"


def load_cache() -> dict:
    """
    캐시는 두 칸짜리 서랍이다.
      "questions": '화가ID|질문' → 프롬프트 1이 뽑은 검색어 목록
                   (LLM은 같은 질문에도 가끔 다른 답을 내서, 한 번 뽑은 검색어를 고정해 둔다)
      "queries":   '화가ID|검색어' → 그 검색어로 수집한 문서 ID 목록
    """
    if config.COLLECT_CACHE_PATH.exists():
        cache = json.loads(config.COLLECT_CACHE_PATH.read_text(encoding="utf-8"))
    else:
        cache = {}
    cache.setdefault("questions", {})
    cache.setdefault("queries", {})
    return cache


def save_cache(cache: dict) -> None:
    config.COLLECT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COLLECT_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _doc_path(page_id: int):
    return config.WIKI_DOCS_DIR / f"{page_id}.json"


def load_doc(page_id: int) -> dict | None:
    path = _doc_path(page_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_doc(page: dict, painter: dict, query: str) -> dict:
    """
    문서를 저장한다. 같은 문서를 다른 화가가 또 수집하면 본문은 그대로 두고
    '관련 화가' 목록에만 추가한다. (F2005: 여러 화가가 같은 문서를 가져도 따로 색인)
    """
    doc = load_doc(page["page_id"]) or {**page, "collected_at": _now(), "related": []}
    relation = {"painter_id": painter["id"], "painter_name": painter["name"], "query": query}
    if not any(r["painter_id"] == painter["id"] for r in doc["related"]):
        doc["related"].append(relation)
    config.WIKI_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    _doc_path(page["page_id"]).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def get_page(title: str, page_id: int | None = None) -> dict | None:
    """이미 받아둔 문서면 파일에서 읽고, 없을 때만 위키백과에 요청한다."""
    if page_id is not None and (doc := load_doc(page_id)):
        return doc
    return wikipedia.fetch_page(title)


# ---------------------------------------------------------------------------
# 프롬프트 2: 후보 선별
# ---------------------------------------------------------------------------
def select_candidates(painter: dict, question: str, candidates: list[dict], llm) -> list[int]:
    """후보 목록 중 프롬프트 2가 고른 후보의 '순서 번호(0부터)'를 돌려준다."""
    listing = "\n".join(
        f"{i}. 제목: {c['title']}\n   요약: {c['summary'] or '(요약 없음)'}"
        for i, c in enumerate(candidates, 1)
    )
    response = (DOC_SELECTION_PROMPT | llm).invoke(
        {"painter_name": painter["name"], "question": question, "candidates": listing}
    )
    picked = []
    for item in extract_json_array(response.content):
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= len(candidates) and number - 1 not in picked:  # 없는 번호는 무시
            picked.append(number - 1)
    return picked


# ---------------------------------------------------------------------------
# 전체 수집 흐름
# ---------------------------------------------------------------------------
def collect(painter: dict, question: str, queries: list[str] | None = None, llm=None) -> dict:
    """
    결과: {
      "queries": [...],
      "kept":    [{page_id, title, url, query, note}],   ← 색인할 문서
      "dropped": [{title, query, reason}],               ← 버린 문서 (검증 4.2용)
      "cached":  [검색어, ...],                           ← 이미 수집해서 건너뛴 검색어
    }
    """
    llm = llm or get_llm()
    cache = load_cache()

    # 같은 화가·같은 질문이면 프롬프트 1을 다시 부르지 않고 저장된 검색어를 쓴다
    question_key = _cache_key(painter["id"], question)
    question_cached = queries is None and question_key in cache["questions"]
    if queries is None:
        if question_cached:
            queries = cache["questions"][question_key]["queries"]
        else:
            queries = extract_queries(painter, question, llm=llm)
            cache["questions"][question_key] = {"question": question, "queries": queries, "created_at": _now()}
            save_cache(cache)

    result = {"queries": queries, "kept": [], "dropped": [], "cached": [], "question_cached": question_cached}
    seen_ids: set[int] = set()  # 이번 실행에서 이미 다룬 문서 (검색어끼리 중복 방지)

    def keep(doc: dict, query: str, note: str) -> None:
        if doc["page_id"] in seen_ids:
            return
        seen_ids.add(doc["page_id"])
        result["kept"].append(
            {"page_id": doc["page_id"], "title": doc["title"], "url": doc["url"], "query": query, "note": note}
        )

    def use_cache(query: str) -> bool:
        """캐시에 있으면 저장된 문서를 그대로 쓰고 True. (위키백과·LLM 호출 없음)"""
        entry = cache["queries"].get(_cache_key(painter["id"], query))
        if entry is None:
            return False
        result["cached"].append(query)
        for page_id in entry["page_ids"]:
            if doc := load_doc(page_id):
                keep(doc, query, "이미 수집함 (캐시)")
        return True

    def remember(query: str, page_ids: list[int]) -> None:
        cache["queries"][_cache_key(painter["id"], query)] = {
            "painter_id": painter["id"],
            "query": query,
            "page_ids": page_ids,
            "collected_at": _now(),
        }
        save_cache(cache)

    # ── 1) 화가 본인 문서: 어떤 규칙보다 우선해서 항상 수집 (F2004) ──
    own_query = queries[0]
    if not use_cache(own_query):
        page = get_page(own_query)
        if page:
            doc = save_doc(page, painter, own_query)
            keep(doc, own_query, "화가 본인 문서 (항상 포함)")
            remember(own_query, [doc["page_id"]])
        else:
            print(f"  ⚠️ 화가 본인 문서 '{own_query}'를 위키백과에서 찾지 못했어요.")

    # ── 2) 나머지 검색어: 후보 검색 → 프롬프트 2 선별 → 본문 수집 → 길이 확인 ──
    for query in queries[1:]:
        if use_cache(query):
            continue

        candidates = [c for c in wikipedia.search(query, config.CANDIDATES_PER_QUERY) if c["page_id"] not in seen_ids]
        picked = select_candidates(painter, question, candidates, llm) if candidates else []

        saved_ids = []
        for i, cand in enumerate(candidates):
            if i not in picked:
                result["dropped"].append({"title": cand["title"], "query": query, "reason": "프롬프트 2가 제외"})
                continue
            page = get_page(cand["title"], cand["page_id"])
            if not page:
                result["dropped"].append({"title": cand["title"], "query": query, "reason": "본문을 가져오지 못함"})
                continue
            if len(page["text"]) < config.MIN_DOC_LENGTH:
                result["dropped"].append(
                    {"title": cand["title"], "query": query, "reason": f"본문이 너무 짧음 ({len(page['text'])}자)"}
                )
                continue
            doc = save_doc(page, painter, query)
            keep(doc, query, "프롬프트 2가 선택")
            saved_ids.append(doc["page_id"])

        remember(query, saved_ids)  # 고른 게 없어도 기록 → 다음에 같은 검색을 반복하지 않음

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="위키백과 문서 수집 테스트 (F2004)")
    parser.add_argument("--painter", required=True, help='화가 이름 (예: "클로드 모네")')
    parser.add_argument("--question", required=True, help="사용자 질문")
    args = parser.parse_args()

    painter = find_painter(load_painters(config.PAINTERS_PATH), args.painter)
    print(f"🎨 화가: {painter['name']} ({painter['id']})")
    print(f"❓ 질문: {args.question}\n")

    result = collect(painter, args.question)

    source = "저장된 검색어 재사용 (프롬프트 1 생략)" if result["question_cached"] else "프롬프트 1"
    print(f"🔑 검색어 [{source}]: {', '.join(result['queries'])}")
    if result["cached"]:
        print(f"♻️  이미 수집해서 건너뛴 검색어: {', '.join(result['cached'])}")

    print(f"\n✅ 색인할 문서 {len(result['kept'])}개")
    for d in result["kept"]:
        print(f"  - {d['title']}  [검색어: {d['query']}] {d['note']}")
        print(f"      {d['url']}")

    print(f"\n🗑️  버린 문서 {len(result['dropped'])}개")
    for d in result["dropped"]:
        print(f"  - {d['title']}  [검색어: {d['query']}] {d['reason']}")
