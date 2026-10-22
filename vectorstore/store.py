"""
vectorstore/store.py — ChromaDB 컬렉션을 다루는 공용 도구
=========================================================
이 파일은 "화가"를 전혀 모른다. 어떤 텍스트든 받아서 임베딩하고 저장·검색만 한다.
그래서 도메인을 바꿔도 이 파일은 그대로 쓸 수 있다. (NF2005 모듈 분리)

비유하자면:
  - 임베딩 = 글의 '의미'를 좌표(숫자 목록)로 바꾸는 것
  - 컬렉션 = 그 좌표들을 모아둔 지도 한 장
  - 검색   = 질문도 좌표로 바꿔서, 지도에서 가장 가까운 점들을 찾는 것
"""

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

import config


def get_embeddings() -> OpenAIEmbeddings:
    """GMS를 거쳐 OpenAI 임베딩 모델을 쓰는 객체를 만든다."""
    config.check_api_key()
    return OpenAIEmbeddings(
        model=config.EMBEDDING_MODEL,
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL,
    )


def get_collection(name: str, embeddings=None) -> Chroma:
    """
    이름에 해당하는 컬렉션을 연다. 없으면 새로 만든다.
    결과는 chroma_db/ 폴더에 저장되므로 프로그램을 껐다 켜도 남아 있다.
    """
    return Chroma(
        collection_name=name,
        embedding_function=embeddings or get_embeddings(),
        persist_directory=str(config.CHROMA_DIR),
    )


def count(store: Chroma) -> int:
    """컬렉션에 저장된 청크 수 (F2002: 색인 결과 확인용)"""
    return store._collection.count()


def reset_collection(name: str, embeddings=None) -> Chroma:
    """컬렉션을 통째로 지우고 빈 컬렉션을 새로 만든다. (항목 수를 줄였을 때 사용)"""
    store = get_collection(name, embeddings)
    store.delete_collection()
    return get_collection(name, embeddings)


def upsert(store: Chroma, docs: list[dict], batch_size: int, quiet: bool = False) -> None:
    """
    [{id, text, metadata}, ...] 를 컬렉션에 넣는다.
    같은 ID가 이미 있으면 새로 추가하지 않고 '덮어쓰기'한다 (upsert).
    → 색인을 두 번 돌려도 청크 수가 늘지 않는다. (명세서 4.1 검증)
    """
    total = len(docs)
    for start in range(0, total, batch_size):
        batch = docs[start : start + batch_size]
        store.add_texts(
            texts=[d["text"] for d in batch],
            metadatas=[d["metadata"] for d in batch],
            ids=[d["id"] for d in batch],
        )
        if not quiet:  # quiet=True 면 진행 상황을 출력하지 않음 (작은 문서 여러 개를 색인할 때)
            print(f"  ... {min(start + batch_size, total)}/{total} 색인 중")


def get_where(store: Chroma, where: dict) -> dict:
    """조건(메타데이터)에 맞는 청크들의 ID와 메타데이터를 가져온다. (임베딩 호출 없음)"""
    return store.get(where=where, include=["metadatas"])


def delete_where(store: Chroma, where: dict) -> int:
    """조건에 맞는 청크를 모두 지우고, 지운 개수를 돌려준다."""
    ids = get_where(store, where)["ids"]
    if ids:
        store.delete(ids=ids)
    return len(ids)


def search(store: Chroma, query: str, k: int = 5):
    """질문과 의미가 가까운 청크 k개를 (문서, 거리) 목록으로 돌려준다. 거리가 작을수록 가깝다."""
    return store.similarity_search_with_score(query, k=k)
