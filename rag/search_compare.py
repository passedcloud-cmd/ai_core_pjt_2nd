"""
rag/search_compare.py  (요구사항 F2014 — 유사도 검색 vs MMR 검색 비교)
=====================================================================
같은 질문에 두 검색 방식을 각각 적용해서, 근거 목록이 어떻게 달라지는지 나란히 보여준다.
(명세서 4.5: "같은 질문에 유사도 검색과 MMR 검색을 각각 적용해 근거 목록이 어떻게 달라지는지 확인")

다양성을 숫자로 보기 위한 지표 3개 (위키 청크만 대상, 메타데이터 1건 제외):
  - 서로 다른 문서 수     : 근거가 몇 개의 문서에서 왔나 (클수록 다양)
  - 한 문서 최대 점유     : 가장 많이 뽑힌 문서 하나가 몇 개를 차지했나 (작을수록 쏠림 적음)
  - 근거끼리 평균 유사도  : 근거 청크끼리 얼마나 닮았나 (작을수록 다양, 0~1)
    ※ ChromaDB에 저장된 벡터를 그대로 꺼내 계산하므로 임베딩 비용이 들지 않는다.

[확인 방법]
  python -m rag.search_compare --painter "이중섭" --question "소 그림이 그려진 시대 배경은?"
  python -m rag.search_compare --painter "이중섭" --question "소 그림이 그려진 시대 배경은?" --answer
  (--answer: 두 방식으로 답변까지 만들어 비교. LLM 호출 2번)
"""

import argparse
from collections import Counter
from itertools import combinations

import config
from data_prep.painters import find_painter, load_painters
from rag.graph import generate, retrieve
from vectorstore import store as vs

MODES = {"similarity": "유사도 검색", "mmr": "MMR 검색"}


def diversity(evidence: list[dict]) -> dict:
    """위키 청크 근거의 다양성 지표를 계산한다."""
    # 외부 문서 청크 전부 (위키백과 + 국립현대미술관 소장품, F2016). 항상 들어가는 화가 메타데이터 1건만 뺀다
    wiki = [e for e in evidence if e["source_type"] != "화가 메타데이터"]
    titles = Counter(e["title"] for e in wiki)

    avg_sim = None
    if len(wiki) >= 2:
        store = vs.get_collection(config.WIKI_COLLECTION)
        got = store.get(ids=[e["chunk_id"] for e in wiki], include=["embeddings"])
        vectors = list(got["embeddings"])
        # OpenAI 임베딩은 길이가 1로 맞춰져 있어서, 내적 = 코사인 유사도
        sims = [sum(x * y for x, y in zip(a, b)) for a, b in combinations(vectors, 2)]
        avg_sim = sum(sims) / len(sims)

    return {
        "wiki_count": len(wiki),
        "doc_count": len(titles),
        "max_share": max(titles.values()) if titles else 0,
        "top_doc": titles.most_common(1)[0][0] if titles else "",
        "avg_sim": avg_sim,
        "by_doc": dict(titles),
    }


def run_both(painter: dict, question: str, with_answer: bool = False) -> dict:
    """두 검색 방식의 근거(와 답변)를 돌려준다. {모드: {"evidence", "metrics", "answer", "cited"}}"""
    results = {}
    for mode in MODES:
        state = retrieve({"painter": painter, "question": question, "collect": False, "search_type": mode})
        item = {"evidence": state["evidence"], "metrics": diversity(state["evidence"])}
        if with_answer:
            out = generate({**state, "painter": painter, "question": question})
            item.update(answer=out["answer"], cited=out["cited"])
        results[mode] = item
    return results


def print_comparison(painter: dict, question: str, results: dict) -> None:
    print(f"🎨 {painter['name']} · ❓ {question}")
    print(f"⚙️  Top-k {config.WIKI_TOP_K} · MMR 후보 {config.MMR_FETCH_K}개 · λ {config.MMR_LAMBDA}\n")

    sim_ids = {e["chunk_id"] for e in results["similarity"]["evidence"]}
    for mode, label in MODES.items():
        print(f"── {label} ──")
        for e in results[mode]["evidence"]:
            if e["source_type"] == "화가 메타데이터":
                continue
            # MMR 쪽에서 유사도 검색엔 없던 새 근거를 표시
            new = "  ★ 새로 들어옴" if mode == "mmr" and e["chunk_id"] not in sim_ids else ""
            print(f"  [근거 {e['no']}] {e['title']} #{e['chunk_id'].rsplit(':', 1)[-1]}{new}")
        print()

    print(f"{'지표':22s} | {'유사도':>8s} | {'MMR':>8s}")
    s, m = results["similarity"]["metrics"], results["mmr"]["metrics"]
    fmt = lambda v: f"{v:.3f}" if isinstance(v, float) else str(v)
    print(f"{'서로 다른 문서 수 (↑ 다양)':22s} | {s['doc_count']:>8} | {m['doc_count']:>8}")
    print(f"{'한 문서 최대 점유 (↓ 쏠림 적음)':20s} | {s['max_share']:>8} | {m['max_share']:>8}")
    print(f"{'근거끼리 평균 유사도 (↓ 다양)':21s} | {fmt(s['avg_sim']):>8} | {fmt(m['avg_sim']):>8}")

    if "answer" in results["similarity"]:
        for mode, label in MODES.items():
            r = results[mode]
            print(f"\n💬 [{label}] 답변 (인용 {len(r['cited'])}개)\n{r['answer']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="유사도 검색 vs MMR 검색 비교 (F2014)")
    parser.add_argument("--painter", required=True, help='화가 이름 (예: "이중섭")')
    parser.add_argument("--question", required=True, help="질문 (이미 한 번 물어서 문서가 수집된 질문이어야 비교가 의미 있음)")
    parser.add_argument("--answer", action="store_true", help="두 방식으로 답변까지 생성해 비교")
    args = parser.parse_args()

    painter = find_painter(load_painters(config.PAINTERS_PATH), args.painter)
    print_comparison(painter, args.question, run_both(painter, args.question, args.answer))
