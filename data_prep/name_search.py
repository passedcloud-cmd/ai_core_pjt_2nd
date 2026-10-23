"""
data_prep/name_search.py — 화가 이름 검색 (오타 허용)
=====================================================
'다반치' → 레오나르도 다 빈치, '김흥도' → 김홍도 처럼 이름을 조금 틀리게 입력해도 찾아준다.

[방법] 한글을 자음·모음으로 쪼갠 뒤, 몇 개를 고쳐야 같아지는지(편집 거리) 센다.
  다반치 → ㄷㅏ ㅂㅏㄴ ㅊㅣ
  다빈치 → ㄷㅏ ㅂㅣㄴ ㅊㅣ   ← 모음 하나만 다름 = 거리 1
  화가 이름이 검색어보다 길면('레오나르도 다 빈치') 검색어 길이만큼 잘라 가며 가장 가까운 부분과 비교한다.
  LLM을 쓰지 않아서 빠르고 비용이 없다.

[확인 방법]
  python -m data_prep.name_search 다반치 김흥도 피카스
"""

import re
import sys
from functools import lru_cache

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def norm(text: str) -> str:
    """이름 비교용: 괄호 설명·띄어쓰기·가운뎃점을 빼고 소문자로. '정선 (화가)' → '정선', 'Claude Monet' → 'claudemonet'"""
    return re.sub(r"[\s·.\-]", "", re.sub(r"\s*\(.*?\)", "", text or "")).lower()


@lru_cache(maxsize=4096)
def jamo(char: str) -> tuple[str, ...]:
    """글자 1개를 자음·모음으로 쪼갠다. '빈' → ('ㅂ', 'ㅣ', 'ㄴ'). 한글이 아니면 그대로."""
    code = ord(char) - 0xAC00
    if not 0 <= code < 11172:
        return (char,)
    parts = (CHO[code // 588], JUNG[(code % 588) // 28])
    return parts + ((JONG[code % 28],) if code % 28 else ())


def to_jamo(text: str) -> list[str]:
    return [j for ch in text for j in jamo(ch)]


def edit_distance(a: list[str], b: list[str]) -> int:
    """a를 b로 바꾸려면 몇 번 고쳐야 하나 (넣기·빼기·바꾸기 각 1번)"""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def distance(query: str, name: str) -> int:
    """검색어와 이름(의 가장 비슷한 부분) 사이의 거리. 둘 다 norm()을 거친 글자여야 한다."""
    q = to_jamo(query)
    width = len(query)
    pieces = [name[i:i + width] for i in range(max(1, len(name) - width + 1))]
    # 한 글자 더 길거나 짧은 부분도 본다 ('르느와르' ↔ '르누아르'는 같은 길이지만, '피카스' ↔ '피카소'처럼 받침 차이도 있음)
    pieces += [name[i:i + width + 1] for i in range(max(0, len(name) - width))]
    pieces += [name[i:i + width - 1] for i in range(len(name) - width + 2)] if width > 2 else []
    return min(edit_distance(q, to_jamo(p)) for p in pieces if p)


def allowed_distance(query: str) -> int:
    """짧은 검색어는 조금만 달라도 전혀 다른 이름이 되므로 1개까지, 길면 2개까지 허용"""
    return 1 if len(to_jamo(query)) <= 6 else 2


def exact_matches(query: str, painters: list[dict]) -> list[dict]:
    """이름(한글·영문)에 검색어가 그대로 들어 있는 화가 (입력 순서 유지)"""
    q = norm(query)
    if len(q) < 2:  # 한 글자('김')는 너무 많이 걸려서 이름 검색으로 보지 않음
        return []
    return [p for p in painters if q in norm(p["name"]) or q in norm(p.get("name_en") or "")]


def similar_matches(query: str, painters: list[dict], limit: int = 10) -> list[dict]:
    """
    이름이 검색어와 비슷한 화가 (정확히 들어 있는 화가는 빼고). 가까운 순 → 입력 순서(유명한 순).
    예) '다반치' → [레오나르도 다 빈치, …] / '김흥도' → [김홍도, 김흥수 (화가)]
    """
    q = norm(query)
    if len(q) < 2:
        return []
    limit_d = allowed_distance(q)
    exact = {p["id"] for p in exact_matches(query, painters)}
    scored = []
    for order, p in enumerate(painters):
        if p["id"] in exact:
            continue
        d = min(distance(q, norm(p["name"])), distance(q, norm(p.get("name_en") or "")) if p.get("name_en") else 99)
        if d <= limit_d:
            scored.append((d, order, p))
    return [p for _, _, p in sorted(scored, key=lambda x: x[:2])][:limit]


if __name__ == "__main__":
    import time

    from data_prep.painters import load_painters, picker_order

    all_painters = picker_order(load_painters())
    for word in sys.argv[1:] or ["다반치", "김흥도", "피카스", "모내", "르느와르", "monnet"]:
        start = time.time()
        exact = exact_matches(word, all_painters)
        similar = similar_matches(word, all_painters, limit=5)
        print(f"{word}: 정확 {[p['name'] for p in exact[:3]]} | 비슷 {[p['name'] for p in similar]} "
              f"({time.time() - start:.2f}초)")
