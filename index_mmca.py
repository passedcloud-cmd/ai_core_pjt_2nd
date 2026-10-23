"""
index_mmca.py  (요구사항 F2016 — 여러 문서 출처 결합)
=====================================================
국립현대미술관 소장작품 설명을 받아서, 위키백과 청크와 같은 외부 문서 컬렉션(painter_wiki)에 함께 색인한다.
근거에는 출처 종류가 "국립현대미술관 소장품"으로 표시되어 "위키백과"와 구분된다.

[출처마다 수집·청킹 방식을 다르게 한 이유] (명세서 F2016 요구)
                위키백과                               국립현대미술관 소장품
  수집          질문마다 검색어→후보 선별→본문 수집     API로 전체(112건)를 한 번에 받음 (검색어가 무시되는 API)
  문서 하나     화가·사건 전체를 다룬 긴 글(수천~수만 자)  작품 1점에 대한 완결된 설명 (중앙값 544자)
  청킹          1,000자 / 200자 중복으로 잘게 나눔       작품 1점 = 청크 1개 (1,500자 넘는 2건만 나눔)
  이유          긴 글에서 질문과 맞는 부분만 골라야 함    자르면 '어떤 작품 이야기인지'가 잘려 나가 근거가 애매해짐

[화가 연결]
  작가 이름이 우리 화가 이름과 같고, 작품 설명 안에 그 화가의 출생 연도가 나오는 경우만 연결한다.
  (같은 이름의 다른 사람을 잘못 연결하지 않도록 이중 확인)

[실행 방법]
  python index_mmca.py            → 받아서 색인 (이미 같은 내용이 색인돼 있으면 건너뜀)
  python index_mmca.py --reset    → 이 출처의 청크를 지우고 다시 색인
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime

import config
from collector.mmca import fetch_all, parse_item
from data_prep.painters import load_painters
from index_wiki import get_splitter
from vectorstore import store as vs


def match_painters(works: list[dict], painters: list[dict]) -> dict[str, dict]:
    """
    {작가 이름: 화가} 연결표를 만든다.
    '이도영 (화가)'처럼 괄호가 붙은 이름은 괄호 앞부분으로 비교하고,
    그 작가의 작품 설명 중 하나에라도 화가의 출생 연도가 나와야 연결한다.
    """
    by_name = defaultdict(list)
    for p in painters:
        by_name[p["name"].split(" (")[0]].append(p)

    texts = defaultdict(str)
    for w in works:
        texts[w["artist"]] += w["description"]

    matched = {}
    for artist, text in texts.items():
        candidates = [p for p in by_name.get(artist, []) if p.get("birth_year") and str(p["birth_year"]) in text]
        if len(candidates) == 1:  # 딱 한 명일 때만 (동명이인이 둘 이상 맞으면 판단 보류)
            matched[artist] = candidates[0]
    return matched


def build_chunks(work: dict, painter: dict) -> list[dict]:
    """작품 1점 → 청크 목록. 짧으면 1개, 1,500자를 넘으면 위키와 같은 규칙으로 나누되 머리글은 청크마다 붙인다."""
    header = f"[{work['title']}] {painter['name']}의 작품 · {config.MMCA_SOURCE_TYPE} 작품 설명"
    if len(work["description"]) <= config.MMCA_SINGLE_CHUNK_MAX:
        pieces, strategy = [work["description"]], "작품 1점 = 청크 1개"
    else:
        pieces, strategy = get_splitter().split_text(work["description"]), f"{config.CHUNK_SIZE}자/{config.CHUNK_OVERLAP}자 분할"

    return [
        {
            "id": f"{painter['id']}:mmca-{work['work_id']}:{i}",  # 화가ID:문서ID:순번 (위키와 같은 규칙)
            "text": f"{header}\n{piece}",
            "metadata": {
                "painter_id": painter["id"],
                "painter_name": painter["name"],
                "page_id": f"mmca-{work['work_id']}",
                "title": work["title"],
                "source_url": work["url"],
                "source_type": config.MMCA_SOURCE_TYPE,  # ← 근거에 '위키백과'와 구분해서 표시 (F2016)
                "query": "",
                "collected_at": work["collected_at"],
                "chunk_index": i,
                "chunk_total": len(pieces),
                "chunking": strategy,
            },
        }
        for i, piece in enumerate(pieces)
    ]


def main(reset: bool) -> None:
    painters = load_painters(config.PAINTERS_PATH)
    now = datetime.now().isoformat(timespec="seconds")

    # ① 수집: 전체를 한 번에 받아서 원본 그대로 저장 (출처 기록, NF2007)
    raw = [{**parse_item(item), "collected_at": now} for item in fetch_all()]

    # 같은 작품(관리번호)이 두 번 등록된 경우가 있다 (설명은 같고 제목만 조금 다름, 2026-10-21 재등록분)
    # → 먼저 등록된 것을 남긴다. 재등록분은 제목 자리에 재료('나무에 옻칠, 자개')가 들어간 경우도 있었음
    works = {}
    for w in sorted(raw, key=lambda w: w["reg_date"]):
        works.setdefault(w["work_id"], w)
    works = list(works.values())

    print(f"📥 국립현대미술관 소장작품 {len(raw)}건 수집 → 중복 등록 {len(raw) - len(works)}건 제외 → {len(works)}건 ({config.MMCA_DOCS_PATH.name})")

    # ② 화가 연결 — 연결된 화가 ID도 작품마다 저장해 둔다 (화면의 '미술관에서 작품 보기' 링크에 사용)
    matched = match_painters(works, painters)
    for w in works:
        w["painter_id"] = matched[w["artist"]]["id"] if w["artist"] in matched else None
    config.MMCA_DOCS_PATH.write_text(json.dumps(works, ensure_ascii=False, indent=1), encoding="utf-8")
    mine = [(w, matched[w["artist"]]) for w in works if w["artist"] in matched and w["description"]]
    artists = Counter(w["artist"] for w in works)
    print(f"🔗 작가 {len(artists)}명 중 우리 화가와 연결 {len(matched)}명 → 작품 {len(mine)}건")
    for artist, p in sorted(matched.items(), key=lambda x: -artists[x[0]]):
        print(f"   - {p['name']} ({p['id']}, {p['birth_year']}년생): {artists[artist]}점")

    # ③ 청킹
    chunks = [c for w, p in mine for c in build_chunks(w, p)]
    strategies = Counter(c["metadata"]["chunking"] for c in chunks if c["metadata"]["chunk_index"] == 0)
    print(f"✂️  청크 {len(chunks)}개 ({', '.join(f'{k} {v}건' for k, v in strategies.items())})")

    # ④ 색인: 위키 청크와 같은 외부 문서 컬렉션에 함께 넣는다 (화가 필터로 같이 검색됨)
    store = vs.get_collection(config.WIKI_COLLECTION)
    where = {"source_type": config.MMCA_SOURCE_TYPE}
    before_total = vs.count(store)
    existing = set(vs.get_where(store, where)["ids"])
    if existing == {c["id"] for c in chunks} and not reset:
        print(f"\n✅ 이미 같은 내용이 색인돼 있어요. 건너뜀 (컬렉션 전체 {before_total}개)")
        return
    removed = vs.delete_where(store, where)
    vs.upsert(store, chunks, config.INDEX_BATCH_SIZE, quiet=True)
    after_total = vs.count(store)
    print(f"\n✅ 색인 완료: 이 출처 청크 {removed}개 지우고 {len(chunks)}개 넣음")
    print(f"   컬렉션 {config.WIKI_COLLECTION} 전체: {before_total}개 → {after_total}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="국립현대미술관 소장작품 설명 색인 (F2016)")
    parser.add_argument("--reset", action="store_true", help="이 출처의 청크를 지우고 다시 색인")
    main(parser.parse_args().reset)
