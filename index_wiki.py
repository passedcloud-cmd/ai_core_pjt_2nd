"""
index_wiki.py  (요구사항 F2005 — 외부 문서 색인)
=================================================
F2004에서 수집한 위키백과 문서(data/wiki_docs/*.json)를 청크로 잘라
ChromaDB 'painter_wiki' 컬렉션에 색인한다. (화가 메타데이터 컬렉션과 분리)

청크 ID = "화가ID:문서ID:순번"   예) Q296:3763996:0
  → 모네와 다른 화가가 같은 '인상주의' 문서를 수집해도 서로 덮어쓰지 않는다.

비유: 두꺼운 책(문서)을 1,000자짜리 카드로 잘라 상자에 넣는 것.
      카드끼리 200자씩 겹치게 자르면, 문장이 카드 경계에서 잘려도 앞뒤 맥락이 남는다.

[실행 방법]
  python index_wiki.py           → 수집된 문서 전체 색인 (이미 같은 설정으로 색인된 문서는 건너뜀)
  python index_wiki.py --reset   → 컬렉션을 비우고 처음부터 다시 색인
  python index_wiki.py --search "게르니카 폭격" [--painter "피카소"]
                                 → 검색 테스트 (화가를 주면 그 화가 문서 안에서만 찾음)

[검증 4.2] config.py 의 CHUNK_SIZE / CHUNK_OVERLAP 을 바꾸고 다시 실행하면
           바뀐 문서만 새로 잘라 색인하고, 청크 수가 달라진다.
"""

import argparse
import json
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

import config
from data_prep.painters import find_painter, load_painters
from vectorstore import store as vs


# ---------------------------------------------------------------------------
# 1) 본문 다듬기와 청크 나누기
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """
    위키백과 본문 정리:
      - '== 각주 ==', '== 외부 링크 ==' 같은 꼬리 부분을 잘라낸다 (근거로 쓸 내용이 거의 없음)
      - 3줄 이상 연속된 빈 줄을 줄인다
    """
    names = "|".join(re.escape(n) for n in config.WIKI_DROP_SECTIONS)
    match = re.search(rf"^==+\s*({names})\s*==+\s*$", text, re.MULTILINE)
    if match:
        text = text[: match.start()]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def get_splitter() -> RecursiveCharacterTextSplitter:
    """
    문단 → 줄 → 문장 → 띄어쓰기 순서로 '자연스러운 경계'를 먼저 찾아 자른다.
    크기·중복은 config.py 에서만 바꾼다. (F2005: 코드가 아니라 설정으로 조정)
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", "다. ", " ", ""],
    )


def build_chunks(doc: dict, relation: dict) -> list[dict]:
    """
    문서 1개 + 관련 화가 1명 → [{id, text, metadata}, ...]
    청크 앞에 [문서 제목]을 붙여서, 청크만 봐도 어느 문서의 내용인지 알 수 있게 한다.
    (예: "1937년 4월 26일..." 만 있으면 무슨 얘기인지 모르지만, [게르니카 폭격]이 붙으면 분명해짐)
    """
    pieces = get_splitter().split_text(clean_text(doc["text"]))
    return [
        {
            "id": f"{relation['painter_id']}:{doc['page_id']}:{i}",
            "text": f"[{doc['title']}]\n{piece}",
            "metadata": {
                "painter_id": relation["painter_id"],      # 검색 범위를 이 화가로 좁히는 필터
                "painter_name": relation["painter_name"],  # 관련 항목
                "page_id": doc["page_id"],
                "title": doc["title"],                     # 문서 제목
                "source_url": doc["url"],                  # 원문 출처
                "source_type": "위키백과",                   # 근거 출처 유형 (F2007)
                "query": relation.get("query", ""),        # 어떤 검색어로 수집했는지
                "collected_at": doc.get("collected_at", ""),
                "chunk_index": i,
                "chunk_total": len(pieces),
                "chunk_size": config.CHUNK_SIZE,           # 이 설정으로 잘랐다는 기록
                "chunk_overlap": config.CHUNK_OVERLAP,     #   → 설정이 바뀌면 다시 색인
            },
        }
        for i, piece in enumerate(pieces)
    ]


# ---------------------------------------------------------------------------
# 2) 색인
# ---------------------------------------------------------------------------
def _where(painter_id: str, page_id: int) -> dict:
    return {"$and": [{"painter_id": painter_id}, {"page_id": page_id}]}


def is_indexed(store, painter_id: str, page_id: int) -> bool:
    """이 화가·문서가 '지금 설정 그대로' 이미 색인돼 있는지 확인한다."""
    found = vs.get_where(store, _where(painter_id, page_id))
    if not found["ids"]:
        return False
    meta = found["metadatas"][0]
    return (
        meta.get("chunk_size") == config.CHUNK_SIZE
        and meta.get("chunk_overlap") == config.CHUNK_OVERLAP
        and len(found["ids"]) == meta.get("chunk_total")
    )


def index_doc(store, doc: dict, relation: dict) -> tuple[str, int]:
    """
    문서 1개를 화가 1명 기준으로 색인한다. 결과: (상태, 청크 수)
    옛 청크를 먼저 지우고 새로 넣는다. 그냥 덮어쓰면, 설정을 바꿔 청크가 줄었을 때
    뒤쪽 옛 청크가 남아 쓰레기가 되기 때문이다.
    """
    if is_indexed(store, relation["painter_id"], doc["page_id"]):
        count = len(vs.get_where(store, _where(relation["painter_id"], doc["page_id"]))["ids"])
        return "건너뜀 (이미 색인됨)", count

    chunks = build_chunks(doc, relation)
    removed = vs.delete_where(store, _where(relation["painter_id"], doc["page_id"]))
    vs.upsert(store, chunks, config.INDEX_BATCH_SIZE, quiet=True)
    return ("다시 색인 (설정 변경)" if removed else "새로 색인"), len(chunks)


def load_wiki_docs(page_ids: list[int] | None = None) -> list[dict]:
    """수집된 문서 파일들을 읽는다. page_ids를 주면 그 문서만."""
    paths = sorted(config.WIKI_DOCS_DIR.glob("*.json")) if config.WIKI_DOCS_DIR.exists() else []
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if page_ids is not None:
        docs = [d for d in docs if d["page_id"] in set(page_ids)]
    return docs


def index_painter_docs(painter_id: str, page_ids: list[int], store=None) -> int:
    """
    한 화가에 대해 수집된 문서들만 색인한다. (나중에 질문 처리 흐름에서 F2004 다음에 호출)
    돌려주는 값: 이 화가의 위키 청크 수
    """
    store = store or vs.get_collection(config.WIKI_COLLECTION)
    for doc in load_wiki_docs(page_ids):
        for relation in doc["related"]:
            if relation["painter_id"] == painter_id:
                index_doc(store, doc, relation)
    return len(vs.get_where(store, {"painter_id": painter_id})["ids"])


def index_all(reset: bool) -> None:
    docs = load_wiki_docs()
    if not docs:
        raise SystemExit("❌ 수집된 문서가 없어요. 먼저 'python -m collector.collect_docs ...'를 실행해 주세요.")

    embeddings = vs.get_embeddings()
    if reset:
        print(f"🧹 '{config.WIKI_COLLECTION}' 컬렉션을 비우고 새로 만듭니다.")
        print("   ※ 같은 컬렉션의 국립현대미술관 소장품 청크도 지워져요 → 끝나고 'python index_mmca.py'를 다시 실행해 주세요. (F2016)")
        store = vs.reset_collection(config.WIKI_COLLECTION, embeddings)
    else:
        store = vs.get_collection(config.WIKI_COLLECTION, embeddings)

    print(f"⚙️  설정: 청크 {config.CHUNK_SIZE}자 / 중복 {config.CHUNK_OVERLAP}자")
    print(f"🔢 색인 전 청크 수: {vs.count(store)}개\n")

    pairs = 0
    for doc in docs:
        for relation in doc["related"]:
            status, n = index_doc(store, doc, relation)
            pairs += 1
            print(f"  - [{relation['painter_name']}] {doc['title']}  ({len(doc['text']):,}자 → 청크 {n}개)  {status}")

    print(f"\n✅ 색인 후 청크 수: {vs.count(store)}개  (문서 {len(docs)}개 · 화가-문서 쌍 {pairs}개, 컬렉션: {config.WIKI_COLLECTION})")


def search(query: str, painter_name: str | None, k: int) -> None:
    store = vs.get_collection(config.WIKI_COLLECTION)
    where = None
    if painter_name:
        painter = find_painter(load_painters(config.PAINTERS_PATH), painter_name)
        where = {"painter_id": painter["id"]}
        print(f"🎨 {painter['name']}의 문서 안에서만 검색")
    print(f"🔎 검색어: {query}\n")
    results = store.similarity_search_with_score(query, k=k, filter=where)
    for i, (doc, distance) in enumerate(results, 1):
        m = doc.metadata
        print(f"{i}. [{m['painter_name']}] {m['title']} #{m['chunk_index']}  (거리 {distance:.3f})")
        print(f"     {doc.page_content.splitlines()[1][:80]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="위키백과 문서 색인 (F2005)")
    parser.add_argument("--reset", action="store_true", help="컬렉션을 비우고 다시 색인")
    parser.add_argument("--search", metavar="질문", help="색인 대신 검색 테스트만 실행")
    parser.add_argument("--painter", help="검색할 때 이 화가의 문서로만 범위를 좁힘")
    parser.add_argument("--k", type=int, default=5, help="검색 결과 수 (기본 5)")
    args = parser.parse_args()

    if args.search:
        search(args.search, args.painter, args.k)
    else:
        index_all(args.reset)
