"""
services/painter_search.py  (요구사항 F2011 — 조건 기반 화가 탐색)
==================================================================
사용자가 "19세기 프랑스 인상주의 풍경화가"처럼 자연어로 조건을 말하면, 조건에 맞는 화가만 찾아준다.

  ① 조건 해석 (LLM)   자연어 → {국적, 대륙, 사조, 장르, 세기, 기타 키워드}
                       데이터에 실제로 있는 표기만 쓰게 해서 필터가 빗나가지 않게 한다.
  ② 후보 찾기          조건으로 먼저 거르고(정확) → 남은 화가 중 painter_meta 색인에서
                       조건 문장과 의미가 가까운 순으로 상위 N명
  ③ 조건 검증 (LLM)   후보 정보만 보고 조건을 '전부' 만족하는 화가만 + 이유

왜 벡터 검색만 쓰지 않나?
  F2002 때 "인상주의 풍경화가"로 벡터 검색만 했더니, 정보가 거의 없는 명나라 화가가 1등이었다.
  벡터 검색은 '비슷한 느낌'은 잘 찾지만 '국적이 프랑스인가?' 같은 정확한 조건엔 약하다.
  그래서 정확한 조건은 필터로, 애매한 조건은 벡터 검색과 LLM으로 나눠 맡긴다.

[확인 방법]  (명세서 4.4 검증: 조건 탐색 결과에 조건과 맞지 않는 항목이 섞이지 않는지)
  python -m services.painter_search "19세기 프랑스 인상주의 풍경화가"
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from functools import lru_cache

import config
from collector.query_extractor import extract_json_array, get_llm
from data_prep.painters import build_index_text, load_painters, to_century
from rag.prompts import CONDITION_PARSE_PROMPT, CONDITION_VERIFY_PROMPT
from vectorstore import store as vs

FIELDS = {  # 조건 이름 → painters.json 필드 이름
    "nationality": "nationality",
    "continents": "continents",
    "movements": "movements",
    "genres": "genres",
}


# ---------------------------------------------------------------------------
# ① 조건 해석
# ---------------------------------------------------------------------------
@lru_cache
def vocabulary() -> dict[str, list[str]]:
    """데이터에 실제로 있는 표기 목록 (많이 쓰인 순). 프롬프트에 넣어 LLM이 이 표기만 쓰게 한다."""
    painters = load_painters(config.PAINTERS_PATH)
    return {
        name: [v for v, _ in Counter(v for p in painters for v in p.get(field, [])).most_common()]
        for name, field in FIELDS.items()
    }


def parse_condition(condition: str, llm) -> dict:
    vocab = vocabulary()
    response = (CONDITION_PARSE_PROMPT | llm).invoke(
        {
            "nationalities": ", ".join(vocab["nationality"]),
            "continents": ", ".join(vocab["continents"]),
            "movements": ", ".join(vocab["movements"]),
            "genres": ", ".join(vocab["genres"]),
            "condition": condition,
        }
    )
    # JSON 객체 부분만 꺼내서 읽는다
    raw = response.content
    try:
        parsed = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        parsed = {}

    # 목록에 없는 표기는 버린다 (LLM이 지어낸 표기로 필터링하면 아무도 안 걸리기 때문)
    result = {}
    for name in FIELDS:
        known = set(vocab[name])
        result[name] = [v for v in parsed.get(name) or [] if v in known]
    for key in ("century_from", "century_to"):
        value = parsed.get(key)
        result[key] = int(value) if isinstance(value, (int, float)) or str(value).isdigit() else None
    result["keywords"] = str(parsed.get("keywords") or "").strip()
    return result


# ---------------------------------------------------------------------------
# ② 후보 찾기: 정확한 필터 → 벡터 유사도 순
# ---------------------------------------------------------------------------
def active_centuries(painter: dict) -> tuple[int, int] | None:
    """화가가 활동한 세기 범위. 사망 연도를 모르면 출생 후 70년까지로 본다. 둘 다 모르면 None."""
    birth, death = painter.get("birth_year"), painter.get("death_year")
    if not birth and not death:
        return None
    start = to_century(birth or death - 70)
    end = to_century(death or birth + 70)
    return (start, end) if start and end else None


def matches_filter(painter: dict, cond: dict) -> bool:
    """
    조건 항목끼리는 AND (국적도 맞고, 사조도 맞고…), 한 항목 안의 표기끼리는 OR.
    조건에 적힌 항목인데 화가 정보가 비어 있으면 '확인 불가'라서 탈락시킨다.
    """
    for name, field in FIELDS.items():
        wanted = cond.get(name) or []
        if wanted and not set(wanted) & set(painter.get(field, [])):
            return False

    c_from, c_to = cond.get("century_from"), cond.get("century_to")
    if c_from or c_to:
        span = active_centuries(painter)
        if span is None:
            return False
        lo, hi = c_from or c_to, c_to or c_from
        if span[1] < lo or span[0] > hi:  # 활동 기간이 조건 세기와 겹치지 않으면 탈락
            return False
    return True


def has_filter(cond: dict) -> bool:
    return bool(any(cond.get(n) for n in FIELDS) or cond.get("century_from") or cond.get("century_to"))


def find_candidates(condition: str, cond: dict, painters: list[dict]) -> tuple[list[dict], int]:
    """
    (후보 화가 목록, 필터 통과 수)를 돌려준다. 후보는 painter_meta 색인에서 꺼낸다.
      - 기타 키워드가 있으면("빛을 잘 표현한"): 필터 통과 화가 중 키워드와 의미가 가까운 순
      - 키워드 없이 조건만 있으면("이탈리아 르네상스 화가"): 필터 통과 화가를 유명한 순
        (이때 의미 유사도 순으로 자르면 정보가 짧은 무명 화가가 앞에 오는 문제가 있었음)
    """
    filtered = has_filter(cond)
    passed = [p for p in painters if matches_filter(p, cond)] if filtered else painters
    if not passed:
        return [], 0

    store = vs.get_collection(config.META_COLLECTION)
    where = {"painter_id": {"$in": [p["id"] for p in passed]}} if filtered else None
    k = min(config.EXPLORE_CANDIDATES, len(passed))

    if cond["keywords"] or not filtered:
        docs = store.similarity_search(cond["keywords"] or condition, k=k, filter=where)
        ids = [d.metadata["painter_id"] for d in docs]
    else:
        found = vs.get_where(store, where)
        ids = [m["painter_id"] for m in sorted(found["metadatas"], key=lambda m: m.get("rank", 10**9))][:k]

    by_id = {p["id"]: p for p in passed}
    return [by_id[i] for i in ids if i in by_id], len(passed)


def explain_empty(cond: dict, painters: list[dict]) -> list[str]:
    """
    필터 통과가 0명일 때 이유를 찾는다.
    조건을 하나씩 빼 보면서, '그 조건만 빼면 통과하는데 그 정보가 비어 있는 화가'가 몇 명인지 센다.
    예) "국적: 조선 조건에 맞는 48명 중 45명은 장르 정보가 비어 있어요"
    """
    labels = {"nationality": "국적", "continents": "대륙", "movements": "사조", "genres": "장르"}
    reasons = []
    for name, field in FIELDS.items():
        if not cond.get(name):
            continue
        others = {**cond, name: []}
        rest = [p for p in painters if matches_filter(p, others)]
        empty = sum(1 for p in rest if not p.get(field))
        if rest and empty:
            reasons.append(f"나머지 조건에 맞는 화가 {len(rest)}명 중 {empty}명은 '{labels[name]}' 정보가 비어 있어요.")
    return reasons


# ---------------------------------------------------------------------------
# ③ 조건 검증
# ---------------------------------------------------------------------------
def explain_unverified(cond: dict, results: list) -> list[str]:
    """
    후보는 있었는데 검증에서 모두 떨어진 경우의 안내.
    '빛을 잘 표현한' 같은 조건은 화가 정보(국적·사조·장르·대표작 등)에 적혀 있지 않아 확인할 수 없다.
    (예전엔 LLM이 원래 알던 지식으로 페르메이르 등을 통과시켰지만, 근거 없는 판단이라 원문 대조로 막았다)
    """
    if results or not cond.get("keywords"):
        return []
    return [f"'{cond['keywords']}' 같은 조건은 화가 정보(국적·사조·장르·대표작 등)에서 확인할 수 없어서 고르지 못했어요. "
            "국적·시대·사조·장르처럼 정보에 있는 조건으로 찾아보거나, 화가를 고른 뒤 대화에서 물어보세요."]


def _verify_batch(condition: str, batch: list[dict], llm, need_quote: bool) -> dict[int, tuple[bool, str]]:
    """
    후보 묶음 하나를 검증한다. 결과: {묶음 안 순서(0부터): (맞는지, 이유)} — 판정이 안 온 후보는 빠져 있다.
    need_quote=True(그 밖의 조건이 있음)면, LLM이 옮겨 적은 quote가 후보 정보에 실제로 있어야만 '맞음'으로 인정한다.
    (LLM이 '빛 표현이 탁월' 같은 이유를 지어내 통과시키는 문제가 있었음 → 코드가 원문 대조로 걸러냄)
    """
    listing = "\n\n".join(f"[후보 {i}]\n{build_index_text(p)}" for i, p in enumerate(batch, 1))
    response = (CONDITION_VERIFY_PROMPT | llm).invoke({"condition": condition, "candidates": listing})
    decided = {}
    for item in extract_json_array(response.content):
        if not isinstance(item, dict):
            continue
        try:
            no = int(item.get("no"))
        except (TypeError, ValueError):
            continue
        if not (1 <= no <= len(batch)) or (no - 1) in decided:  # 없는 번호·중복은 무시
            continue
        match = item.get("match") is True
        if match and need_quote:
            quote = re.sub(r"\s+", "", str(item.get("quote") or ""))
            match = bool(quote) and quote in re.sub(r"\s+", "", build_index_text(batch[no - 1]))
        decided[no - 1] = (match, str(item.get("reason", "")).strip())
    return decided


def verify(condition: str, candidates: list[dict], llm, need_quote: bool = False) -> list[tuple[dict, str]]:
    """
    LLM이 후보 정보를 보고 조건을 전부 만족하는 화가만 고른다. 결과: [(화가, 이유), ...]

    처음엔 30명을 한 번에 넘기고 '맞는 화가만' 받았더니, 목록 중간의 김홍도·신윤복·장승업을
    판정 없이 빠뜨리는 문제가 있었다. 그래서
      ① 모든 후보에 맞다/아니다 판정을 받고  ② 10명씩 나눠 동시에 검증하고
      ③ 그래도 판정이 빠진 후보는 한 번 더 검증한다.
    """
    if not candidates:
        return []
    size = config.EXPLORE_VERIFY_BATCH
    batches = [candidates[i : i + size] for i in range(0, len(candidates), size)]
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:  # 묶음들을 동시에 → 전체 시간은 거의 그대로
        results = list(pool.map(lambda b: _verify_batch(condition, b, llm, need_quote), batches))

    decisions = {}
    for batch, decided in zip(batches, results):
        for i, p in enumerate(batch):
            if i in decided:
                decisions[p["id"]] = decided[i]

    missing = [p for p in candidates if p["id"] not in decisions]
    if missing:  # 판정이 빠진 후보만 모아 한 번 더
        retry = _verify_batch(condition, missing, llm, need_quote)
        for i, p in enumerate(missing):
            decisions[p["id"]] = retry.get(i, (False, "판정을 받지 못함"))

    return [(p, decisions[p["id"]][1]) for p in candidates if decisions[p["id"]][0]]


# ---------------------------------------------------------------------------
# 전체 흐름
# ---------------------------------------------------------------------------
def search_painters(condition: str) -> dict:
    """
    결과: {
      "condition": 해석된 조건,
      "filtered": 필터 통과 화가 수,
      "candidates": LLM 검증에 넘긴 후보 수,
      "results": [(화가, 이유), ...]  ← 유명한 순으로 정렬
      "empty_reasons": 필터 통과가 0명일 때 그 이유 안내
    }
    """
    llm = get_llm(config.EXPLORE_MODEL, timeout=config.LLM_FAST_TIMEOUT)  # 짧은 출력이라 10초 넘으면 다시 보냄
    painters = load_painters(config.PAINTERS_PATH)

    cond = parse_condition(condition, llm)
    candidates, filtered = find_candidates(condition, cond, painters)
    results = verify(condition, candidates, llm, need_quote=bool(cond["keywords"]))
    results.sort(key=lambda r: r[0].get("rank", 10**9))
    return {
        "condition": cond,
        "filtered": filtered,
        "candidates": len(candidates),
        "results": results,
        "empty_reasons": explain_empty(cond, painters) if filtered == 0 else explain_unverified(cond, results),
    }


def describe_condition(cond: dict) -> str:
    """해석된 조건을 사람이 읽기 좋게: '국적: 프랑스 · 사조: 인상주의 · 시대: 19세기'"""
    labels = {"nationality": "국적", "continents": "대륙", "movements": "사조", "genres": "장르"}
    parts = [f"{labels[n]}: {', '.join(cond[n])}" for n in labels if cond.get(n)]
    c_from, c_to = cond.get("century_from"), cond.get("century_to")
    if c_from or c_to:
        lo, hi = c_from or c_to, c_to or c_from
        parts.append(f"시대: {lo}세기" if lo == hi else f"시대: {lo}~{hi}세기")
    if cond.get("keywords"):
        parts.append(f"기타: {cond['keywords']}")
    return " · ".join(parts) or "(필터 조건 없음 → 의미 검색만 사용)"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="조건 기반 화가 탐색 (F2011)")
    parser.add_argument("condition", help='찾고 싶은 화가 조건 (예: "19세기 프랑스 인상주의 풍경화가")')
    args = parser.parse_args()

    out = search_painters(args.condition)
    print(f"🔎 조건: {args.condition}")
    print(f"① 해석: {describe_condition(out['condition'])}")
    order = "키워드와 의미가 가까운 순" if out["condition"]["keywords"] else "유명한 순"
    print(f"② 필터 통과 {out['filtered']}명 → {order} 상위 {out['candidates']}명을 LLM이 검증")
    print(f"③ 조건에 맞는 화가 {len(out['results'])}명 (유명한 순)\n")
    for line in out["empty_reasons"]:
        print(f"  ℹ️ {line}")
    for painter, reason in out["results"]:
        print(f"  🎨 {painter['name']} ({painter['rank']}위) — {reason}")
