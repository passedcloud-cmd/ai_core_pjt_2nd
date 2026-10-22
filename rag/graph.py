"""
rag/graph.py  (요구사항 F2006 근거 검색·병합 / F2007 답변 생성 / F2008 근거 표시)
================================================================================
LangGraph로 '검색 → 생성' 두 단계를 잇는다.

    START → [retrieve] → [generate] → END

  retrieve: (필요하면) 문서 수집·색인 → 화가 메타데이터 1건 + 위키 청크 Top-k → 병합·중복 제거
  generate: 프롬프트 3으로 답변 → 답변 속 [근거 N]을 찾아 실제로 인용된 근거만 남김

비유: retrieve는 '자료 조사 담당', generate는 '자료만 보고 발표하는 담당'.
      발표 담당은 조사 담당이 가져온 자료 밖의 말은 하면 안 된다.

[확인 방법]  (명세서 4.3 검증)
  python -m rag.graph --painter "파블로 피카소" --question "게르니카는 어떤 사건을 그린 거야?"
  python -m rag.graph --show-graph     → 그래프 구조(노드 2개) 출력
"""

import argparse
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import config
from collector.collect_docs import collect
from collector.query_extractor import get_llm
from data_prep.painters import find_painter, load_painters
from index_wiki import index_painter_docs
from rag.prompts import ANSWER_PROMPT, NO_EVIDENCE_ANSWER
from vectorstore import store as vs


# ---------------------------------------------------------------------------
# 그래프가 들고 다니는 상태 (노드끼리 주고받는 바구니)
# ---------------------------------------------------------------------------
class RAGState(TypedDict, total=False):
    painter: dict            # 선택한 화가 (입력)
    question: str            # 사용자 질문 (입력)
    collect: bool            # True면 retrieve 단계에서 문서 수집·색인부터 함 (입력)
    top_k: int               # 위키 청크 검색 수 (없으면 config.WIKI_TOP_K, 화가 비교 기능은 따로 지정)
    search_type: str         # "similarity" 또는 "mmr" (없으면 config.SEARCH_TYPE, 화면에서 바꿀 수 있음 — F2014)
    collect_result: dict     # F2004 수집 결과 (검색어, 색인/버린 문서)
    evidence: list[dict]     # 번호 붙은 근거 전체 (retrieve 결과)
    answer: str              # 최종 답변 (generate 결과)
    cited: list[dict]        # 답변에 실제로 인용된 근거만 (F2008)


# ---------------------------------------------------------------------------
# 노드 1: retrieve (F2006)
# ---------------------------------------------------------------------------
def _wiki_chunk_id(doc) -> str:
    """청크 ID. 검색 결과에 ID가 없으면 메타데이터로 다시 만든다. ("화가ID:문서ID:순번")"""
    m = doc.metadata
    return doc.id or f"{m['painter_id']}:{m['page_id']}:{m['chunk_index']}"


def search_wiki(painter_id: str, question: str, k: int | None = None, search_type: str | None = None) -> list:
    """
    그 화가의 위키 청크 안에서만 질문과 가까운 청크를 찾는다. (설정: SEARCH_TYPE, WIKI_TOP_K)

    similarity: 질문과 가장 비슷한 순서대로 k개
    mmr (F2014): 먼저 MMR_FETCH_K개를 넉넉히 가져온 뒤, 한 개씩 고를 때마다
                 '질문과 비슷한 정도'에서 '이미 고른 청크와 겹치는 정도'를 빼서 점수를 매긴다.
                 → 내용이 겹치는 청크(같은 큰 문서의 이웃 청크 등)가 근거에 몰리지 않는다.
    """
    k = k or config.WIKI_TOP_K
    search_type = search_type or config.SEARCH_TYPE
    store = vs.get_collection(config.WIKI_COLLECTION)
    where = {"painter_id": painter_id}  # ← 이 필터가 없으면 다른 화가의 문서가 섞인다
    if search_type == "mmr":
        return store.max_marginal_relevance_search(
            question, k=k, fetch_k=config.MMR_FETCH_K, lambda_mult=config.MMR_LAMBDA, filter=where
        )
    return store.similarity_search(question, k=k, filter=where)


def retrieve(state: RAGState) -> RAGState:
    painter, question = state["painter"], state["question"]
    result: RAGState = {}

    # (1) 문서 수집·색인 (F2003~F2005). 이미 물었던 질문이면 캐시 덕분에 거의 바로 끝난다.
    if state.get("collect", True):
        collected = collect(painter, question)
        index_painter_docs(painter["id"], [d["page_id"] for d in collected["kept"]])
        result["collect_result"] = collected

    evidence, seen_ids, seen_texts = [], set(), set()

    def add(chunk_id: str, text: str, meta: dict) -> None:
        """병합하면서 중복 제거: 같은 청크 ID 또는 완전히 같은 내용이면 한 번만 넣는다."""
        text_key = re.sub(r"\s+", "", text)
        if chunk_id in seen_ids or text_key in seen_texts:
            return
        seen_ids.add(chunk_id)
        seen_texts.add(text_key)
        evidence.append(
            {
                "no": len(evidence) + 1,  # [근거 N]의 N
                "chunk_id": chunk_id,
                "source_type": meta.get("source_type", ""),  # 화가 메타데이터 / 위키백과 (F2007)
                "title": meta.get("title", ""),
                "url": meta.get("source_url") or meta.get("wiki_url", ""),
                "text": text,
            }
        )

    # (2) 화가 메타데이터 청크: 검색 없이 ID로 바로 꺼내서 '항상' 근거 1번에 넣는다 (F2006)
    meta_store = vs.get_collection(config.META_COLLECTION)
    meta = meta_store.get(ids=[painter["id"]], include=["documents", "metadatas"])
    if meta["ids"]:
        add(meta["ids"][0], meta["documents"][0], meta["metadatas"][0])

    # (3) 위키 청크: 질문과의 의미 유사도로 검색 (그 화가 범위 안에서)
    for doc in search_wiki(painter["id"], question, state.get("top_k"), state.get("search_type")):
        add(_wiki_chunk_id(doc), doc.page_content, doc.metadata)

    result["evidence"] = evidence
    return result


# ---------------------------------------------------------------------------
# 노드 2: generate (F2007, F2008)
# ---------------------------------------------------------------------------
def format_evidence(evidence: list[dict]) -> str:
    """근거를 프롬프트에 넣을 글로 만든다. 출처 유형을 함께 적는다. (F2007)"""
    return "\n\n".join(
        f"[근거 {e['no']}] ({e['source_type']}) {e['title']}\n{e['text']}" for e in evidence
    )


def find_cited_numbers(answer: str) -> list[int]:
    """답변에서 [근거 3], [근거 1, 2], [근거1][근거2] 같은 표기를 찾아 번호 목록으로 만든다."""
    numbers = []
    for group in re.findall(r"\[근거\s*([\d\s,]+)\]", answer):
        for n in re.findall(r"\d+", group):
            if int(n) not in numbers:
                numbers.append(int(n))
    return numbers


def generate(state: RAGState) -> RAGState:
    evidence = state.get("evidence", [])
    if not evidence:  # 근거가 하나도 없으면 LLM을 부르지 않는다 (추측 방지)
        return {"answer": NO_EVIDENCE_ANSWER, "cited": []}

    response = (ANSWER_PROMPT | get_llm(config.ANSWER_MODEL)).invoke(
        {
            "painter_name": state["painter"]["name"],
            "evidence": format_evidence(evidence),
            "question": state["question"],
            "no_evidence": NO_EVIDENCE_ANSWER,
        }
    )
    answer = response.content.strip()

    # 답변에 실제로 등장한 번호의 근거만 남긴다. 없는 번호(LLM이 지어낸 번호)는 무시. (F2008)
    by_no = {e["no"]: e for e in evidence}
    cited = [by_no[n] for n in sorted(find_cited_numbers(answer)) if n in by_no]
    return {"answer": answer, "cited": cited}


# ---------------------------------------------------------------------------
# 그래프 조립 (F2007: 검색과 생성, 노드 2개)
# ---------------------------------------------------------------------------
def build_graph():
    graph = StateGraph(RAGState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()


def ask(painter: dict, question: str, collect_docs: bool = True) -> RAGState:
    """화가와 질문을 넣으면 답변·근거가 담긴 최종 상태를 돌려준다. (UI에서도 이 함수를 부름)"""
    return build_graph().invoke({"painter": painter, "question": question, "collect": collect_docs})


# ---------------------------------------------------------------------------
# 직접 실행: 검증용 출력
# ---------------------------------------------------------------------------
def print_result(state: RAGState) -> None:
    print(f"⚙️  설정: 위키 Top-k = {config.WIKI_TOP_K}, 검색 방식 = {config.SEARCH_TYPE}, 답변 모델 = {config.ANSWER_MODEL}")
    print(f"\n📚 검색된 근거 {len(state['evidence'])}개 (메타데이터 1 + 위키 청크, 중복 제거 후)")
    for e in state["evidence"]:
        print(f"  [근거 {e['no']}] ({e['source_type']}) {e['title']}  · {e['chunk_id']}")

    print(f"\n💬 답변\n{state['answer']}\n")

    if state["cited"]:
        print(f"🔗 답변에 인용된 근거 {len(state['cited'])}개")
        for e in state["cited"]:
            print(f"  [근거 {e['no']}] ({e['source_type']}) {e['title']}\n      {e['url']}")
    else:
        print("ℹ️  답변에 인용된 근거가 없습니다. (문서에서 확인된 내용이 없다는 뜻이에요)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="질문 → 근거 검색 → 답변 (F2006~F2008)")
    parser.add_argument("--painter", help='화가 이름 (예: "클로드 모네")')
    parser.add_argument("--question", help="사용자 질문")
    parser.add_argument("--no-collect", action="store_true", help="문서 수집 없이 이미 색인된 것만으로 답변")
    parser.add_argument("--show-graph", action="store_true", help="그래프 구조만 출력")
    args = parser.parse_args()

    if args.show_graph:
        g = build_graph().get_graph()
        print("노드:", [n for n in g.nodes])
        print("연결:", [(e.source, e.target) for e in g.edges])
        print("\n[Mermaid 그림 코드]\n" + g.draw_mermaid())
    else:
        if not (args.painter and args.question):
            parser.error("--painter 와 --question 을 함께 넣어 주세요.")
        painter = find_painter(load_painters(config.PAINTERS_PATH), args.painter)
        print(f"🎨 화가: {painter['name']} ({painter['id']})\n❓ 질문: {args.question}")
        print_result(ask(painter, args.question, collect_docs=not args.no_collect))
