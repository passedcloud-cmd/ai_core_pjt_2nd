"""
index_meta.py  (요구사항 F2002 — 메타데이터 색인)
=================================================
data/painters.json 의 화가들을 검색용 텍스트로 만들어(F2001)
임베딩한 뒤 ChromaDB 'painter_meta' 컬렉션에 저장한다.

[실행 방법]
  python index_meta.py            → 색인 (여러 번 실행해도 청크 수가 늘지 않음)
  python index_meta.py --reset    → 컬렉션을 비우고 처음부터 다시 색인
                                     (화가 수를 줄였을 때는 꼭 이걸로!)
  python index_meta.py --search "인상주의 풍경화가"
                                  → 색인은 건너뛰고 검색만 테스트
"""

import argparse

import config
from data_prep.painters import build_documents, load_painters
from vectorstore import store as vs


def index(reset: bool) -> None:
    painters = load_painters(config.PAINTERS_PATH)
    documents = build_documents(painters)
    print(f"📂 불러온 화가 수: {len(painters)}명")

    embeddings = vs.get_embeddings()
    if reset:
        print(f"🧹 '{config.META_COLLECTION}' 컬렉션을 비우고 새로 만듭니다.")
        collection = vs.reset_collection(config.META_COLLECTION, embeddings)
    else:
        collection = vs.get_collection(config.META_COLLECTION, embeddings)

    before = vs.count(collection)
    print(f"🔢 색인 전 청크 수: {before}개")

    vs.upsert(collection, documents, config.INDEX_BATCH_SIZE)

    after = vs.count(collection)
    print(f"\n✅ 색인 후 청크 수: {after}개  (컬렉션: {config.META_COLLECTION})")
    if after == len(painters):
        print("✅ 화가 수와 청크 수가 일치합니다.")
    else:
        print(
            f"⚠️ 화가 수({len(painters)})와 청크 수({after})가 달라요. "
            "화가 수를 줄였다면 --reset 으로 다시 실행해 주세요."
        )


def search(query: str, k: int = 5) -> None:
    collection = vs.get_collection(config.META_COLLECTION)
    print(f"🔎 검색어: {query}  (저장된 청크 {vs.count(collection)}개 중 상위 {k}개)\n")
    for i, (doc, distance) in enumerate(vs.search(collection, query, k), 1):
        meta = doc.metadata
        print(f"{i}. {meta['name']}  (유명도 {meta['rank']}위, 거리 {distance:.3f})")
        first_lines = doc.page_content.splitlines()[1:3]  # 소개·국적 줄만 살짝
        for line in first_lines:
            print(f"     {line}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="화가 메타데이터 색인 (F2002)")
    parser.add_argument("--reset", action="store_true", help="컬렉션을 비우고 다시 색인")
    parser.add_argument("--search", metavar="질문", help="색인 대신 검색 테스트만 실행")
    parser.add_argument("--k", type=int, default=5, help="검색 결과 수 (기본 5)")
    args = parser.parse_args()

    if args.search:
        search(args.search, args.k)
    else:
        index(args.reset)
