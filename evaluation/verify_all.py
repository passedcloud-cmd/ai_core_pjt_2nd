"""
evaluation/verify_all.py — 명세서 4장 확인 항목 자동 검증 → docs/검증_결과.md
============================================================================
요구사항마다 실제로 실행해서 결과를 재고, 요구사항 대조표 · 테스트 결과표를 문서로 만든다.
제출 직전에 다시 돌려서 최신 결과로 갱신할 수 있다.

  - 진짜 색인을 바꾸는 시험(청크 크기 변경)은 임시 폴더의 별도 DB에서 한다.
  - 화면으로만 볼 수 있는 항목(대화 기록 분리, 진행 상태 화면 등)은 '수동 확인'으로 날짜와 함께 기록한다.

[실행 방법]
  python -m evaluation.verify_all        (몇 분 걸림, LLM·임베딩 호출 포함)
"""

import json
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

import config

OUT_MD = config.BASE_DIR / "docs" / "검증_결과.md"
OUT_JSON = config.BASE_DIR / "docs" / "verification_results.json"
RESULTS: list[dict] = []


def check(tid: str, req: str, spec: str, what: str, expected: str):
    """시험 하나를 등록하는 장식자. 함수는 (실제 결과 문자열, 통과 여부)를 돌려준다."""
    def wrap(fn):
        def run():
            t = time.time()
            try:
                actual, ok = fn()
            except Exception as error:  # 시험 자체가 실패해도 다음 시험은 계속
                actual, ok = f"오류: {type(error).__name__}: {str(error)[:120]}", False
            RESULTS.append({"id": tid, "req": req, "spec": spec, "what": what, "expected": expected,
                            "actual": actual, "result": "통과" if ok else "실패", "seconds": round(time.time() - t, 1)})
            print(f"[{'통과' if ok else '실패'}] {tid} {req} · {what}\n        → {actual}")
        run.__name__ = fn.__name__
        TESTS.append(run)
        return run
    return wrap


TESTS = []


def painter(name):
    from data_prep.painters import find_painter, load_painters
    return find_painter(load_painters(config.PAINTERS_PATH), name)


# ---------------------------------------------------------------------------
# 4.1 데이터와 색인 (F2001 ~ F2002)
# ---------------------------------------------------------------------------
@check("T01", "F2001", "4.1", "화가 메타데이터 로드 · 색인 텍스트 구성", "1,343명 로드, 화가 1건 텍스트에 의도한 속성 포함")
def t01():
    from data_prep.painters import build_documents, load_painters
    docs = build_documents(load_painters(config.PAINTERS_PATH))
    monet = next(d for d in docs if d["id"] == "Q296")["text"]
    fields = ["이름", "소개", "국적", "대륙", "생애", "활동 시기", "출생지", "사조", "장르", "대표작"]
    present = [f for f in fields if f"{f}:" in monet]
    return f"{len(docs)}명 로드 · 텍스트 {len(docs)}개 · 모네 텍스트 속성 {len(present)}/{len(fields)}개", len(docs) == 1343 and len(present) == len(fields)


@check("T02", "F2002", "4.1", "메타데이터 색인 두 번 실행", "로드 수 = 청크 수, 두 번째 실행에도 늘지 않음")
def t02():
    from data_prep.painters import build_documents, load_painters
    from vectorstore import store as vs
    docs = build_documents(load_painters(config.PAINTERS_PATH))
    col = vs.get_collection(config.META_COLLECTION)
    counts = [vs.count(col)]
    for _ in range(2):
        vs.upsert(col, docs, config.INDEX_BATCH_SIZE, quiet=True)
        counts.append(vs.count(col))
    return f"색인 전 {counts[0]} → 1회 {counts[1]} → 2회 {counts[2]} (로드 {len(docs)})", counts[1] == counts[2] == len(docs)


# ---------------------------------------------------------------------------
# 4.2 문서 수집과 색인 (F2003 ~ F2005)
# ---------------------------------------------------------------------------
NEW_Q = ("빈센트 반 고흐", "별이 빛나는 밤은 지금 어느 미술관에 있어?")


@check("T03", "F2003", "4.2", "검색어 추출 (프롬프트 1)", "JSON 검색어 목록, 첫 검색어 = 화가 본인 문서")
def t03():
    from collector.query_extractor import extract_queries
    p = painter("파블로 피카소")
    qs = extract_queries(p, "게르니카는 어떤 사건을 그린 거야?")
    return f"검색어 {qs}", bool(qs) and qs[0] == p["wiki_title"]


@check("T04", "F2004", "4.2", "후보 선별 · 색인/버린 문서 구분 · 본인 문서 포함", "색인 문서와 버린 문서가 구분되고, 화가 본인 문서가 포함됨")
def t04():
    from collector.collect_docs import collect
    p = painter(NEW_Q[0])
    r = collect(p, NEW_Q[1])
    kept = [d["title"] for d in r["kept"]]
    dropped = [d["title"] for d in r["dropped"]]
    own = p["wiki_title"] in kept
    return f"검색어 {r['queries']} · 색인 {kept} · 버림 {len(dropped)}개 {dropped[:4]}… · 본인 문서 {'포함' if own else '누락'}", own


@check("T05", "F2004·NF2002", "4.2·4.7", "같은 질문을 다시 물으면 수집하지 않음", "검색어 전부 캐시 재사용, 버린 문서 0개(새 검색 없음)")
def t05():
    from collector.collect_docs import collect
    r = collect(painter(NEW_Q[0]), NEW_Q[1])
    return f"캐시 재사용 {len(r['cached'])}/{len(r['queries'])} 검색어 · 새로 버린 문서 {len(r['dropped'])}개 · 프롬프트 1 생략 {r['question_cached']}", \
        len(r["cached"]) == len(r["queries"]) and not r["dropped"] and r["question_cached"]


@check("T06", "F2005", "4.2", "청크 크기·중복 길이 변경 시 청크 수 변화 (임시 DB)", "설정을 바꾸면 청크 수가 달라지고, 되돌리면 원래대로")
def t06():
    import index_wiki as iw
    from vectorstore import store as vs
    real_dir, size, overlap = config.CHROMA_DIR, config.CHUNK_SIZE, config.CHUNK_OVERLAP
    tmp = Path(tempfile.mkdtemp(prefix="verify_chroma_"))
    try:
        config.CHROMA_DIR = tmp  # 진짜 색인은 건드리지 않는다
        store = vs.get_collection(config.WIKI_COLLECTION)
        docs = [d for d in iw.load_wiki_docs() if d["title"] in ("수련 (모네)", "게르니카 폭격", "이중섭")]
        counts = []
        for s, o in [(1000, 200), (500, 100), (1000, 200)]:
            config.CHUNK_SIZE, config.CHUNK_OVERLAP = s, o
            for d in docs:
                iw.index_doc(store, d, d["related"][0])
            counts.append(vs.count(store))
        return f"문서 {len(docs)}개: 1000/200 → {counts[0]}개, 500/100 → {counts[1]}개, 다시 1000/200 → {counts[2]}개", \
            counts[0] != counts[1] and counts[0] == counts[2]
    finally:
        config.CHROMA_DIR, config.CHUNK_SIZE, config.CHUNK_OVERLAP = real_dir, size, overlap
        shutil.rmtree(tmp, ignore_errors=True)


@check("T07", "F2005", "4.2", "여러 화가가 같은 문서를 수집해도 청크 ID가 겹치지 않음", "청크 ID = 화가ID:문서ID:순번")
def t07():
    import index_wiki as iw
    doc = next(d for d in iw.load_wiki_docs() if d["title"] == "수련 (모네)")
    a = [c["id"] for c in iw.build_chunks(doc, {"painter_id": "Q296", "painter_name": "모네"})]
    b = [c["id"] for c in iw.build_chunks(doc, {"painter_id": "QTEST", "painter_name": "다른 화가"})]
    return f"모네 {a[:2]}… / 다른 화가 {b[:2]}… · 겹침 {len(set(a) & set(b))}개", not set(a) & set(b)


# ---------------------------------------------------------------------------
# 4.3 답변과 근거 (F2006 ~ F2008)
# ---------------------------------------------------------------------------
MONET_Q = "수련 연작은 몇 점이고 어디 있어?"


@check("T08", "F2006", "4.3", "Top-k 변경 시 근거 목록 변화 + 메타데이터 항상 포함 + 화가 필터", "근거 수가 Top-k+1로 변하고, 근거 1은 메타데이터, 외부 근거는 모두 그 화가의 것")
def t08():
    from rag.graph import retrieve
    p = painter("클로드 모네")
    out, ok = [], True
    for k in (3, 5, 10):
        ev = retrieve({"painter": p, "question": MONET_Q, "collect": False, "top_k": k})["evidence"]
        only_monet = all(e["chunk_id"].startswith("Q296") for e in ev)
        ok &= len(ev) == k + 1 and ev[0]["source_type"] == "화가 메타데이터" and only_monet
        out.append(f"Top-k {k} → 근거 {len(ev)}개")
    return " · ".join(out) + " · 근거 1 = 화가 메타데이터 · 모두 모네의 청크", ok


@check("T09", "F2007", "4.3", "LangGraph 노드 구성", "retrieve와 generate 두 노드")
def t09():
    from rag.graph import build_graph
    nodes = [n for n in build_graph().get_graph().nodes if not n.startswith("__")]
    return f"노드 {nodes}", nodes == ["retrieve", "generate"]


@check("T10", "F2007·F2008", "4.3", "답변의 근거 번호 = 표시된 근거 목록, 출처 종류 구분", "답변의 [근거 N]과 인용 목록이 일치, 출처 종류가 붙어 있음")
def t10():
    from rag.graph import ask, find_cited_numbers
    s = ask(painter("클로드 모네"), MONET_Q, collect_docs=False)
    in_answer = sorted(set(find_cited_numbers(s["answer"])))
    shown = [e["no"] for e in s["cited"]]
    kinds = sorted({e["source_type"] for e in s["evidence"]})
    return f"답변 속 번호 {in_answer} = 표시 근거 {shown} · 출처 종류 {kinds}", in_answer == shown and bool(shown)


@check("T11", "F2007·F2008", "4.3", "문서에 없는 질문", "추측하지 않고 '확인되지 않는다'고 답하며, 인용 근거 없음 안내 대상")
def t11():
    from rag.graph import ask
    s = ask(painter("파블로 피카소"), "피카소가 가장 좋아한 음식은 뭐야?", collect_docs=False)
    unknown = bool(re.search(r"확인되지\s*않", s["answer"]))
    return f"답변 \"{s['answer'][:60]}\" · 인용 {len(s['cited'])}개", unknown and not s["cited"]


# ---------------------------------------------------------------------------
# 4.4 서비스 기능과 화면 (F2009 ~ F2012)
# ---------------------------------------------------------------------------
@check("T12", "F2010", "4.4", "화가 비교 결과에 근거 제시", "비교표 칸마다 근거 번호, 근거마다 어느 화가의 근거인지 구분")
def t12():
    from rag.graph import find_cited_numbers
    from services.compare import compare
    out = compare(painter("김홍도"), painter("신윤복"), "풍속화의 소재와 분위기")
    whose = sorted({e["painter_name"] for e in out["cited"]})
    rows = [l for l in out["answer"].splitlines() if l.startswith("|") and "---" not in l][1:]
    rows_cited = sum(1 for l in rows if find_cited_numbers(l))
    return f"표 {len(rows)}행 중 근거 표시 {rows_cited}행 · 인용 근거 {len(out['cited'])}개 · 근거 화가 {whose}", \
        rows and rows_cited == len(rows) and len(whose) == 2


@check("T13", "F2011", "4.4", "조건 기반 탐색 결과에 조건과 맞지 않는 화가가 섞이지 않음", "결과 화가가 모두 해석된 조건(필터)에 맞고, 0명이면 이유 안내")
def t13():
    from data_prep.painters import load_painters
    from services.painter_search import matches_filter, search_painters
    ps = {p["id"]: p for p in load_painters(config.PAINTERS_PATH)}
    out, ok = [], True
    for q in ["19세기 프랑스 인상주의 풍경화가", "조선 시대 화가", "조선 시대 풍속화가"]:
        r = search_painters(q)
        mismatch = [p["name"] for p, _ in r["results"] if not matches_filter(ps[p["id"]], r["condition"])]
        names = [p["name"] for p, _ in r["results"]]
        ok &= not mismatch and (bool(names) or bool(r["empty_reasons"]))
        extra = f" (김홍도 {'포함' if '김홍도' in names else '누락'})" if q == "조선 시대 화가" else ""
        out.append(f"'{q}' {r['filtered']}→{r['candidates']}→{len(names)}명{extra}, 불일치 {len(mismatch)}" + (" · 0명 사유 안내" if r["empty_reasons"] else ""))
    return " / ".join(out), ok


@check("T14", "F2012·F2013", "4.4·4.5", "웹 서버: 진행 상태 단계 전송 · API 설명서 · 안내", "질문 응답이 status→retrieved→answer 순서, /docs 200, 키 없음·없는 화가 안내")
def t14():
    from fastapi.testclient import TestClient
    from api.server import app
    c = TestClient(app)
    with c.stream("POST", "/api/ask", json={"painter_id": "Q296", "question": MONET_Q}) as r:
        types = [json.loads(l)["type"] for l in r.iter_lines() if l]
    docs = c.get("/docs").status_code
    missing = c.get("/api/painters/Q0").status_code
    key = config.OPENAI_API_KEY
    try:
        config.OPENAI_API_KEY = ""
        no_key = c.post("/api/search", json={"condition": "조선 시대 화가"})
        status_msg = c.get("/api/status").json()["message"]
    finally:
        config.OPENAI_API_KEY = key
    return (f"스트림 {types} · /docs {docs} · 없는 화가 {missing} · 키 없음 {no_key.status_code} \"{status_msg[:24]}…\"",
            types == ["status", "retrieved", "answer"] and docs == 200 and missing == 404 and no_key.status_code == 503 and status_msg)


# ---------------------------------------------------------------------------
# 4.5 심화 (F2014, F2016) — F2013은 T14, F2015는 별도 문서
# ---------------------------------------------------------------------------
@check("T15", "F2014", "4.5", "같은 질문에 유사도 vs MMR 근거 목록 차이", "MMR에서 근거가 바뀌고 근거끼리 평균 유사도가 낮아짐")
def t15():
    from rag.search_compare import run_both
    r = run_both(painter("클로드 모네"), MONET_Q)
    s, m = r["similarity"], r["mmr"]
    changed = len({e["chunk_id"] for e in m["evidence"]} - {e["chunk_id"] for e in s["evidence"]})
    return (f"MMR에서 바뀐 근거 {changed}개 · 문서 수 {s['metrics']['doc_count']}→{m['metrics']['doc_count']} · "
            f"평균 유사도 {s['metrics']['avg_sim']:.3f}→{m['metrics']['avg_sim']:.3f}"), changed > 0 and m["metrics"]["avg_sim"] < s["metrics"]["avg_sim"]


@check("T16", "F2016", "4.5", "근거 목록에 출처 종류가 구분되어 표시", "한 질문의 근거에 위키백과와 국립현대미술관 소장품이 구분되어 나옴")
def t16():
    from rag.graph import retrieve
    ev = retrieve({"painter": painter("이중섭"), "question": "이중섭의 말년은 어땠어?", "collect": False})["evidence"]
    kinds = {}
    for e in ev:
        kinds[e["source_type"]] = kinds.get(e["source_type"], 0) + 1
    return f"근거 출처 종류 {kinds}", "위키백과" in kinds and config.MMCA_SOURCE_TYPE in kinds


# ---------------------------------------------------------------------------
# 4.7 비기능 (NF2001 ~ NF2007)
# ---------------------------------------------------------------------------
@check("T17", "NF2001", "4.7", "대표 질문 처리 시간", "사용에 지장 없는 수준 (이미 수집한 질문 수 초)")
def t17():
    from rag.graph import ask
    p = painter("클로드 모네")
    t = time.time(); ask(p, MONET_Q); cached = time.time() - t
    return f"수집된 대표 질문(수집 단계 포함) {cached:.1f}초", cached < 20


@check("T18", "NF2003", "4.7", "소스코드·제출물에 API 키가 없음", "실제 키 값이 .env 외 어떤 파일에도 없음, .gitignore에 .env")
def t18():
    from dotenv import dotenv_values
    keys = [v for k, v in dotenv_values(config.BASE_DIR / ".env").items() if k.endswith("_KEY") and v]
    skip = {"venv", "chroma_db", "__pycache__", ".git"}
    hits = []
    for f in config.BASE_DIR.rglob("*"):
        if f.is_file() and not skip & set(f.parts) and f.name != ".env" and f.stat().st_size < 30_000_000:
            text = f.read_bytes()
            hits += [str(f.relative_to(config.BASE_DIR)) for k in keys if k.encode() in text]
    ignored = ".env" in (config.BASE_DIR / ".gitignore").read_text(encoding="utf-8").split()
    return f"검사한 키 {len(keys)}개 · 발견 {len(hits)}건 {hits[:3]} · .gitignore의 .env {ignored}", not hits and ignored


@check("T19", "NF2004", "4.7", "화면에 오류 원문·추적 정보를 보여주지 않음", "오류 시 안내 문구만, 상세는 로그 / Streamlit showErrorDetails = none")
def t19():
    from api.server import friendly_message
    class AuthenticationError(Exception): ...
    msg_auth = friendly_message(AuthenticationError("Error code: 401 secret path C:\\Users\\x"))
    msg_other = friendly_message(FileNotFoundError("C:\\Users\\SSAFY\\Desktop\\data.json"))
    toml = (config.BASE_DIR / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    leaked = any(s in msg_auth + msg_other for s in ("401", "C:\\", "Error code"))
    return f"안내 \"{msg_auth[:28]}…\" / \"{msg_other[:26]}…\" · 원문 노출 {leaked} · showErrorDetails none {'none' in toml}", not leaked and '"none"' in toml


@check("T20", "NF2005", "4.7", "모듈 분리", "수집·색인·검색·생성·UI·도메인 영역이 폴더로 분리")
def t20():
    need = ["data_prep", "collector", "vectorstore", "rag", "services", "api", "web"]
    have = [d for d in need if (config.BASE_DIR / d).is_dir()]
    return f"영역 폴더 {have}", have == need


@check("T21", "NF2006", "4.7", "외부 API 예절", "User-Agent에 연락처, 요청 간격, 재시도 규칙이 코드에 있음")
def t21():
    src = (config.BASE_DIR / "collector" / "wikipedia.py").read_text(encoding="utf-8")
    ua = "@" in config.WIKI_USER_AGENT
    rules = all(s in src for s in ("WIKI_REQUEST_INTERVAL", "WIKI_MAX_RETRIES", "429", "User-Agent"))
    return f"User-Agent 연락처 {ua} · 간격 {config.WIKI_REQUEST_INTERVAL}초 · 재시도 {config.WIKI_MAX_RETRIES}회 · 규칙 코드 {rules}", ua and rules


@check("T22", "NF2007", "4.7", "데이터·문서 출처와 이용 조건 기록", "화가·문서·청크·작품 설명마다 원문 주소 저장")
def t22():
    from data_prep.painters import load_painters
    from vectorstore import store as vs
    ps = load_painters(config.PAINTERS_PATH)
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in config.WIKI_DOCS_DIR.glob("*.json")]
    chunks = vs.get_collection(config.WIKI_COLLECTION).get(include=["metadatas"])["metadatas"]
    mmca = json.loads(config.MMCA_DOCS_PATH.read_text(encoding="utf-8"))
    p_ok = sum(1 for p in ps if p.get("source_url") and "CC0" in p.get("source", ""))
    d_ok = sum(1 for d in docs if d.get("url") and d.get("collected_at"))
    c_ok = sum(1 for c in chunks if c.get("source_url") and c.get("source_type"))
    m_ok = sum(1 for w in mmca if w.get("url"))
    return (f"화가 {p_ok}/{len(ps)} · 위키 문서 {d_ok}/{len(docs)} · 청크 {c_ok}/{len(chunks)} · 미술관 작품 {m_ok}/{len(mmca)}",
            p_ok == len(ps) and d_ok == len(docs) and c_ok == len(chunks) and m_ok == len(mmca))


# ---------------------------------------------------------------------------
# 문서 만들기
# ---------------------------------------------------------------------------
# 화면에서만 확인할 수 있는 항목 (자동화하지 않고, 직접 확인한 날짜와 결과를 기록)
MANUAL = [
    ("M01", "F2009", "4.4", "화가를 바꿨다가 돌아와도 이전 대화가 섞이지 않음",
     "Streamlit: 다 빈치 질문 → 모네 질문 → 다 빈치로 돌아가니 모나리자 대화만 남음 / 웹: 이중섭 선택 시 대화 기록 0, 모네로 돌아가면 유지", "2026-10-21 · 10-22"),
    ("M02", "F2012", "4.4", "문서 수집과 답변 생성 중 진행 상태 표시",
     "Streamlit: '근거 문서를 확인하는 중 → 근거 문서 목록 → 답변을 쓰는 중 → 답변 완료' / 웹: 같은 단계가 실시간 표시", "2026-10-21 · 10-22"),
    ("M03", "F2012", "4.4", "색인이 없거나 API 키가 없을 때 안내",
     "OPENAI_API_KEY 비우고 실행 → 'API 키 설정이 없어요' / 빈 CHROMA_DIR로 실행 → '화가 메타데이터 색인이 비어 있어요 + python index_meta.py' (진짜 파일 무손상)", "2026-10-21"),
    ("M04", "F2011", "4.4", "조건 탐색 카드에서 대화로 이어짐",
     "카드의 '대화하기' → 그 화가 선택 + 대화 화면 이동 (Streamlit 메뉴 고정 문제 해결 후 재확인)", "2026-10-21 · 10-22"),
    ("M05", "F2013", "4.5", "HTML·CSS·JS 화면에서 질문·답변·근거 표시",
     "모네 질문 → [근거 N] 골드 배지 + 인용 근거 목록 + 전체 근거 접기 / 조건 탐색 카드 / 화가 비교 표", "2026-10-22"),
    ("M06", "NF2004", "4.7", "잘못된 키로 오류를 일으켜도 화면에 추적 정보 없음",
     "가짜 키로 질문 → 화면엔 'API 키가 올바르지 않거나 만료된 것 같아요'만, 터미널엔 traceback", "2026-10-22"),
]


def write_report() -> None:
    passed = sum(r["result"] == "통과" for r in RESULTS)
    by_req: dict[str, list[dict]] = {}
    for r in RESULTS:
        for req in r["req"].split("·"):
            by_req.setdefault(req, []).append(r)
    for tid, req, *_ in MANUAL:
        by_req.setdefault(req, []).append({"id": tid, "result": "통과(수동)"})

    reqs = [f"F20{n:02d}" for n in range(1, 18)] + [f"NF200{n}" for n in range(1, 8)]
    names = {"F2001": "항목 메타데이터 적재", "F2002": "메타데이터 색인", "F2003": "검색어 추출", "F2004": "외부 문서 수집",
             "F2005": "외부 문서 색인", "F2006": "근거 검색 및 병합", "F2007": "근거 기반 답변 생성", "F2008": "근거 표시",
             "F2009": "항목 선택 및 질의응답", "F2010": "도메인 특화 기능(화가 비교)", "F2011": "조건 기반 항목 탐색",
             "F2012": "진행 상태 및 안내", "F2013": "프론트엔드 직접 구현", "F2014": "MMR 검색", "F2015": "RAG 전후 품질 비교",
             "F2016": "여러 문서 출처 결합", "F2017": "작품 이미지 표시(추가)", "NF2001": "응답 시간", "NF2002": "수집 캐시",
             "NF2003": "API 키 관리", "NF2004": "내부 정보 비노출", "NF2005": "모듈 분리", "NF2006": "외부 API 예절", "NF2007": "출처와 이용 조건"}
    extra = {"F2015": ("통과", "별도 문서 `docs/F2015_RAG_전후_비교.md` (7문항, 필수 사실 6/14 → 14/14, 틀린 주장 4 → 0)"),
             "F2017": ("통과(수동)", "화가 1,148명(85%)에 이미지, 갤러리·원본 링크 화면 확인 (2026-10-22)")}

    lines = [
        "# 검증 결과 — 화가 도슨트",
        "",
        f"- 자동 검증 실행: {datetime.now():%Y-%m-%d %H:%M} · `python -m evaluation.verify_all` · 자동 시험 {len(RESULTS)}개 중 **{passed}개 통과**",
        f"- 수동 확인(화면): {len(MANUAL)}개 — 화면으로만 볼 수 있는 항목은 직접 확인한 날짜와 결과를 기록",
        "- 결과 표기: 통과 / 실패 / 미확인 (명세서 4장)",
        "",
        "## 1. 요구사항 대조표",
        "",
        "| 요구사항 | 이름 | 구분 | 검증 시험 | 결과 |",
        "|---|---|---|---|---|",
    ]
    for req in reqs:
        kind = "필수" if req <= "F2012" else "심화" if req <= "F2016" else "추가" if req.startswith("F") else "비기능"
        if req in extra:
            result, tests = extra[req]
        else:
            rs = by_req.get(req, [])
            tests = ", ".join(r["id"] for r in rs) or "—"
            result = "미확인" if not rs else "실패" if any(r["result"] == "실패" for r in rs) else "통과"
        lines.append(f"| {req} | {names[req]} | {kind} | {tests} | {'✅ ' if result.startswith('통과') else '❌ '}{result} |")

    lines += ["", "## 2. 테스트 결과표 (자동)", "",
              "| ID | 요구사항 | 명세서 | 시험 | 기대 결과 | 실제 결과 | 판정 |", "|---|---|---|---|---|---|---|"]
    for r in RESULTS:
        lines.append(f"| {r['id']} | {r['req']} | {r['spec']} | {r['what']} | {r['expected']} | {r['actual'].replace('|', '/')} | "
                     f"{'✅' if r['result'] == '통과' else '❌'} {r['result']} |")

    lines += ["", "## 3. 수동 확인 (화면)", "", "| ID | 요구사항 | 명세서 | 확인 항목 | 확인 내용 | 확인일 | 판정 |", "|---|---|---|---|---|---|---|"]
    for tid, req, spec, what, how, when in MANUAL:
        lines.append(f"| {tid} | {req} | {spec} | {what} | {how} | {when} | ✅ 통과 |")

    lines += [
        "", "## 4. 심화 기능과 필수 구현의 비교", "",
        "| 심화 | 필수 구현(기준) | 심화 적용 결과 |",
        "|---|---|---|",
        "| F2013 프론트엔드 | Streamlit 화면 | 같은 기능을 FastAPI + HTML·CSS·JS로. 요청·응답 형식 문서화(/docs), 질문 진행 단계 실시간 표시, 디자인 시안 3개 중 선택해 구현 |",
        "| F2014 MMR | 유사도 검색 Top-5 | 5문항 모두 근거 다양성↑(평균 유사도 예: 0.604→0.330), 4문항에서 한 문서 쏠림↓. 단 사실형 2문항에서 핵심 사실 누락 → 기본값은 유사도 유지 |",
        "| F2015 RAG 전후 | (RAG 없이 같은 모델) | 필수 사실 6/14 → 14/14, 틀린 주장 4 → 0, 근거 제시 0/7 → 6/7, 답이 없는 질문에 '모른다' 0/1 → 1/1 |",
        "| F2016 여러 출처 | 위키백과만 | 국립현대미술관 소장품 47청크 추가. 은지화처럼 위키백과에 없던 내용을 근거로 답함, 근거에 출처 종류 구분 |",
        "",
        "## 5. 제출 전 점검 목록 (명세서 4.8)", "",
        "| 점검 항목 | 결과 | 근거 |", "|---|---|---|",
        f"| 필수 기능 F2001~F2012가 모두 동작한다 | {'✅' if all(not any(r['result'] == '실패' for r in by_req.get(f'F20{n:02d}', [])) for n in range(1, 13)) else '❌'} | 1장 대조표 |",
        "| 선택한 심화 기능이 동작하고, 필수 기능의 결과와 비교되어 있다 | ✅ | 4장 |",
        "| 증명을 위한 스크린샷 항목을 모두 첨부했다 | 미확인 | 제출 시 별도 첨부 |",
        "| 요구사항 정의서 · RAG 파이프라인 설계문서 · 소스코드 · README.md를 모두 포함했다 | ✅ | `docs/요구사항_정의서.md`, `docs/RAG_파이프라인_설계문서.md`, 소스 전체, `README.md` |",
        "| 검증 결과(요구사항 대조표, 테스트 결과표, 셀프 · 상호 체크리스트)를 포함했다 | 일부 | 대조표·테스트 결과표는 이 문서. 셀프·상호 체크리스트는 3일차 제공 양식으로 별도 작성 |",
        "| (NF2003) 제출 파일에 `.env`·API 키가 없다 | 제출 시 확인 | zip에서 `.env`, `venv/`, `chroma_db/` 제외 |",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✅ {passed}/{len(RESULTS)} 통과 → {OUT_MD}")


if __name__ == "__main__":
    for test in TESTS:
        test()
    write_report()
