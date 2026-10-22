"""
services/compare.py  (요구사항 F2010 — 도메인 특화 기능: 화가 비교)
==================================================================
두 화가를 골라 비교 관점(예: "화풍과 대표작")을 주면, 수집된 문서만 근거로
비교표 + 공통점·차이점을 만들어 준다. 칸마다 [근거 N]을 붙이고, 실제로 인용된 근거만 보여준다.

왜 이 기능인가?
  미술관에서 관람객이 가장 많이 하는 질문 중 하나가 "이 화가랑 저 화가는 뭐가 달라요?"다.
  그리고 이미 만든 RAG 부품(화가별 수집·색인·검색)을 두 번 돌리면 되기 때문에,
  비교 결과도 자연스럽게 '화가별로 분리된 근거'를 갖는다.

흐름:
  화가 A ─ retrieve (F2003~F2006 재사용) ─┐
                                         ├─ 병합·번호 다시 매기기·중복 제거 ─▶ 비교 프롬프트 ─▶ 인용된 근거만 남기기
  화가 B ─ retrieve (F2003~F2006 재사용) ─┘

[확인 방법]  (명세서 4.4 검증: 특화 기능 결과에도 근거가 함께 제시되는지)
  python -m services.compare --a "클로드 모네" --b "빈센트 반 고흐"
  python -m services.compare --a "김홍도" --b "신윤복" --focus "풍속화의 소재와 분위기"
"""

import argparse
import re

import config
from collector.query_extractor import get_llm
from data_prep.painters import find_painter, load_painters
from rag.graph import find_cited_numbers, format_evidence, retrieve
from rag.prompts import COMPARE_PROMPT


def retrieval_question(painter: dict, focus: str) -> str:
    """
    화가별 수집·검색에 쓸 질문. 상대 화가 이름은 일부러 넣지 않는다.
    (넣었더니 김홍도의 근거를 찾는데 '신윤복' 문서가 검색되는 식으로, 근거가 상대 화가 쪽으로 쏠렸다)
    """
    return f"{painter['name']}의 {focus}는 어떤가?"


def gather_evidence(painters: list[dict], focus: str, on_progress=None) -> tuple[list[dict], list[dict]]:
    """
    두 화가의 근거를 각각 모아 하나로 합친다.
    결과: (번호 다시 매긴 근거 목록, 화가별 수집 결과 목록)
    """
    merged, seen_texts, collected = [], set(), []
    for painter in painters:
        if on_progress:
            on_progress(f"📚 {painter['name']}의 근거 문서를 확인하는 중… (처음이면 위키백과에서 수집해요)")

        state = retrieve(
            {
                "painter": painter,
                "question": retrieval_question(painter, focus),
                "collect": True,
                "top_k": config.COMPARE_TOP_K,
            }
        )
        collected.append({"painter": painter, **state.get("collect_result", {})})

        for e in state["evidence"]:
            # 두 화가가 같은 문서(예: '인상주의')를 수집했으면 청크 ID는 달라도 내용이 같다 → 한 번만
            text_key = re.sub(r"\s+", "", e["text"])
            if text_key in seen_texts:
                continue
            seen_texts.add(text_key)
            merged.append({**e, "no": len(merged) + 1, "painter_name": painter["name"]})
    return merged, collected


def compare(painter_a: dict, painter_b: dict, focus: str = "", on_progress=None) -> dict:
    """
    결과: {
      "focus": 비교 관점,
      "answer": 마크다운 비교 결과,
      "evidence": 근거 전체, "cited": 인용된 근거만,
      "collected": 화가별 수집 결과 (검색어·색인 문서)
    }
    """
    focus = focus.strip() or config.COMPARE_DEFAULT_FOCUS
    evidence, collected = gather_evidence([painter_a, painter_b], focus, on_progress)

    if on_progress:
        on_progress("✍️ 근거를 읽고 비교표를 만드는 중…")

    # 근거마다 어느 화가의 근거인지 앞에 붙인다 → LLM이 A의 칸을 B의 근거로 채우는 실수를 막음
    labeled = [{**e, "source_type": f"{e['source_type']} · {e['painter_name']}에 대한 근거"} for e in evidence]
    response = (COMPARE_PROMPT | get_llm(config.ANSWER_MODEL)).invoke(
        {
            "painter_a": painter_a["name"],
            "painter_b": painter_b["name"],
            "focus": focus,
            "evidence": format_evidence(labeled),
        }
    )
    answer = response.content.strip()

    by_no = {e["no"]: e for e in evidence}
    cited = [by_no[n] for n in sorted(find_cited_numbers(answer)) if n in by_no]
    return {"focus": focus, "answer": answer, "evidence": evidence, "cited": cited, "collected": collected}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="화가 비교 (F2010)")
    parser.add_argument("--a", required=True, help='화가 A (예: "클로드 모네")')
    parser.add_argument("--b", required=True, help='화가 B (예: "빈센트 반 고흐")')
    parser.add_argument("--focus", default="", help=f'비교 관점 (기본: "{config.COMPARE_DEFAULT_FOCUS}")')
    args = parser.parse_args()

    painters = load_painters(config.PAINTERS_PATH)
    a, b = find_painter(painters, args.a), find_painter(painters, args.b)
    if a["id"] == b["id"]:
        raise SystemExit("❌ 서로 다른 두 화가를 골라 주세요.")

    out = compare(a, b, args.focus, on_progress=print)
    print(f"\n⚖️  {a['name']} vs {b['name']}  (관점: {out['focus']})")
    for c in out["collected"]:
        print(f"  - {c['painter']['name']} 근거 문서: {', '.join(d['title'] for d in c.get('kept', []))}")
    print(f"\n{out['answer']}\n")
    print(f"🔗 인용된 근거 {len(out['cited'])}개 / 검색된 근거 {len(out['evidence'])}개")
    for e in out["cited"]:
        print(f"  [근거 {e['no']}] ({e['source_type']} · {e['painter_name']}) {e['title']}  {e['url']}")
