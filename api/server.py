"""
api/server.py — 화가 도슨트 웹 서버 (F2013: 화면과 서버 분리)
==============================================================
기존 RAG 기능(rag/, services/)을 그대로 쓰고, 화면(web/)과는 JSON으로만 주고받는다.
화면 쪽은 HTML·CSS·JavaScript만으로 만들어져 있고, 이 서버가 web/ 폴더도 함께 열어 준다.

[실행 방법]  (프로젝트 폴더에서)
  uvicorn api.server:app --port 8600
  → 화면:        http://localhost:8600
  → API 설명서:  http://localhost:8600/docs  (요청·응답 형식을 자동으로 보여줌)

[API 목록]
  GET  /api/status              색인 건수·API 키 상태 (F2012 안내용)
  GET  /api/painters            화가 목록 (선택 창)
  GET  /api/painters/{id}       화가 상세 + 작품 이미지
  POST /api/ask                 질문 → 근거 검색 → 답변 (단계별로 한 줄씩 흘려보냄)
  POST /api/search              조건으로 화가 찾기 (F2011)
  POST /api/compare             화가 비교 (F2010)
"""

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
from api.schemas import (
    AskEvent, AskRequest, CompareRequest, CompareResponse, ComparePainter, Evidence, Fact, ImageItem, Link,
    PainterDetail, PainterSummary, SearchRequest, SearchResponse, SearchResult, Status,
)
from data_prep.images import external_links, museum_works, painter_images, thumb, work_caption
from data_prep.painters import active_period, load_painters, lifespan

logger = logging.getLogger("painter_rag.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="화가 도슨트 API",
    description="화가 RAG 서비스의 요청·응답 형식 (F2013). 모든 답변은 수집된 문서만 근거로 한다.",
    version="1.0",
)


# ---------------------------------------------------------------------------
# 오류 안내 (NF2004: 화면에 오류 원문·추적 정보를 보여주지 않는다)
# ---------------------------------------------------------------------------
def friendly_message(error: Exception) -> str:
    """오류 종류별 안내 문구. 자세한 내용은 서버 터미널 로그에만 남긴다."""
    logger.exception("처리 중 오류 발생")
    name = type(error).__name__
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "API 키가 올바르지 않거나 만료된 것 같아요. .env의 OPENAI_API_KEY를 확인해 주세요."
    if name == "RateLimitError":
        return "요청이 몰려서 잠시 처리할 수 없어요. 잠시 후 다시 시도해 주세요."
    if name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout"):
        return "네트워크 연결에 문제가 있어요. 잠시 후 다시 시도해 주세요."
    return "처리 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, error: Exception):
    return JSONResponse(status_code=500, content={"error": friendly_message(error)})


def require_ready() -> None:
    """API 키가 없으면 질문·검색 기능을 막고 안내한다. (F2012)"""
    if not (config.OPENAI_API_KEY and config.OPENAI_BASE_URL):
        raise HTTPException(503, "API 키 설정이 없어요. .env에 OPENAI_API_KEY와 OPENAI_BASE_URL을 넣고 서버를 다시 켜 주세요.")


# ---------------------------------------------------------------------------
# 데이터 → 응답 모양으로 바꾸는 도우미
# ---------------------------------------------------------------------------
@lru_cache
def painters_by_id() -> dict[str, dict]:
    return {p["id"]: p for p in load_painters(config.PAINTERS_PATH)}


def get_painter(painter_id: str) -> dict:
    painter = painters_by_id().get(painter_id)
    if not painter:
        raise HTTPException(404, "그 화가를 찾지 못했어요.")
    return painter


def to_image(img: dict, kind: str) -> ImageItem:
    title = img.get("title") or img.get("caption") or ""
    sub = " · ".join(str(x) for x in [img.get("year"), img.get("collection")] if x)
    return ImageItem(title=title, sub=sub, thumb=thumb(img, 500), large=thumb(img, 1920),
                     page=img.get("page", ""), kind=kind)


def first_image(painter_id: str) -> dict | None:
    imgs = painter_images(painter_id)
    return (imgs["works"] or [None])[0] or imgs["portrait"] or (imgs["article_images"] or [None])[0]


def summary(p: dict) -> PainterSummary:
    nation = ", ".join(p.get("nationality", [])[:1])
    years = f"{p.get('birth_year') or '?'}–{p.get('death_year') or ''}"
    img = first_image(p["id"])
    return PainterSummary(id=p["id"], name=p["name"], name_en=p.get("name_en") or "",
                          label=" · ".join(x for x in [nation, years] if x), rank=p.get("rank", 0),
                          thumb=thumb(img, 330) if img else None)


def to_evidence(items: list[dict]) -> list[Evidence]:
    return [Evidence(no=e["no"], source_type=e.get("source_type", ""), title=e.get("title", ""), url=e.get("url", ""),
                     chunk_id=e.get("chunk_id", ""), text=e.get("text", ""), painter_name=e.get("painter_name"))
            for e in items]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/status", response_model=Status, summary="색인 건수·API 키 상태 (F2012)")
def status() -> Status:
    from vectorstore import store as vs

    key_ok = bool(config.OPENAI_API_KEY and config.OPENAI_BASE_URL)
    meta = wiki = 0
    message = ""
    if key_ok:
        meta = vs.count(vs.get_collection(config.META_COLLECTION))
        wiki = vs.count(vs.get_collection(config.WIKI_COLLECTION))
        if meta == 0:
            message = "화가 메타데이터 색인이 비어 있어요. 터미널에서 'python index_meta.py'를 먼저 실행해 주세요."
    else:
        message = "API 키 설정이 없어요. .env에 OPENAI_API_KEY와 OPENAI_BASE_URL을 넣고 서버를 다시 켜 주세요."
    return Status(api_key_ok=key_ok, meta_count=meta, wiki_count=wiki, painter_count=len(painters_by_id()), message=message)


@app.get("/api/painters", response_model=list[PainterSummary], summary="화가 목록 (유명한 순)")
def list_painters() -> list[PainterSummary]:
    return [summary(p) for p in painters_by_id().values()]


@app.get("/api/painters/{painter_id}", response_model=PainterDetail, summary="화가 상세 + 작품 이미지")
def painter_detail(painter_id: str) -> PainterDetail:
    p = get_painter(painter_id)
    imgs = painter_images(painter_id)
    join = lambda xs: ", ".join(xs or [])
    facts = [Fact(label=l, value=v) for l, v in [
        ("국적", join(p.get("nationality"))), ("사조", join(p.get("movements"))), ("장르", join(p.get("genres"))),
        ("생애", lifespan(p.get("birth_year"), p.get("death_year"))), ("활동 시기", active_period(p.get("birth_year"), p.get("death_year"))),
        ("출생지", p.get("birth_place") or ""), ("대표작", join((p.get("notable_works") or [])[:4])),
    ] if v]
    works = [to_image(w, "work") for w in imgs["works"]]
    related = [to_image(a, "related") for a in imgs["article_images"]]
    # 첫 화면 이미지: 작품 → 위키백과 문서 속 이미지 → 초상 순서 (이중섭처럼 작품 이미지가 없는 화가도 그림이 먼저 보이게)
    hero = works[0] if works else related[0] if related else None
    portrait = None
    if imgs["portrait"]:
        portrait = to_image(imgs["portrait"], "portrait")
        portrait.title = portrait.title or f"{p['name']}의 초상"
    return PainterDetail(
        **summary(p).model_dump(), description=p.get("description") or "", facts=facts, wiki_url=p.get("wiki_url") or "",
        portrait=portrait, hero=hero or portrait, works=works, related=related,
        museum_works=[Link(label=w["title"], url=w["url"]) for w in museum_works(painter_id)],
        links=[Link(**l) for l in external_links(p)],
    )


@app.post("/api/ask", summary="질문 → 근거 검색 → 답변 (단계별 NDJSON 스트림)",
          response_description="AskEvent 모양의 JSON이 한 줄에 하나씩 온다")
def ask(req: AskRequest):
    require_ready()
    painter = get_painter(req.painter_id)

    def stream():
        line = lambda event: event.model_dump_json() + "\n"
        try:
            from rag.graph import build_graph

            yield line(AskEvent(type="status", message="근거 문서를 확인하는 중이에요… (처음 하는 질문이면 위키백과에서 수집해요)"))
            final: dict = {}
            inputs = {"painter": painter, "question": req.question, "collect": True, "search_type": req.search_type}
            for update in build_graph().stream(inputs, stream_mode="updates"):
                for node, output in update.items():
                    final.update(output)
                    if node == "retrieve":
                        c = output.get("collect_result", {})
                        yield line(AskEvent(type="retrieved", message="근거를 읽고 답변을 쓰는 중이에요…",
                                            queries=c.get("queries", []), cached=c.get("cached", []),
                                            documents=[d["title"] for d in c.get("kept", [])],
                                            evidence_count=len(output.get("evidence", []))))
            yield line(AskEvent(type="answer", answer=final.get("answer", ""), search_type=req.search_type,
                                cited=to_evidence(final.get("cited", [])), evidence=to_evidence(final.get("evidence", []))))
        except Exception as error:  # 스트림 도중 오류도 안내 문구로 (NF2004)
            yield line(AskEvent(type="error", message=friendly_message(error)))

    return StreamingResponse(stream(), media_type="application/x-ndjson")


COND_LABELS = {"nationality": "국적", "continents": "대륙", "movements": "사조", "genres": "장르"}


@app.post("/api/search", response_model=SearchResponse, summary="조건으로 화가 찾기 (F2011)")
def search(req: SearchRequest) -> SearchResponse:
    require_ready()
    from services.painter_search import search_painters

    out = search_painters(req.condition)
    cond = out["condition"]
    parsed = [Fact(label=COND_LABELS[k], value=", ".join(cond[k])) for k in COND_LABELS if cond.get(k)]
    lo, hi = cond.get("century_from") or cond.get("century_to"), cond.get("century_to") or cond.get("century_from")
    if lo:
        parsed.append(Fact(label="시대", value=f"{lo}세기" if lo == hi else f"{lo}~{hi}세기"))
    if cond.get("keywords"):
        parsed.append(Fact(label="기타", value=cond["keywords"]))
    if out["mode"] == "name":
        parsed = [Fact(label="이름", value=req.condition.strip())]

    results = []
    for p, reason in out["results"]:
        img = first_image(p["id"])
        results.append(SearchResult(painter=summary(p), reason=reason, image=to_image(img, "work") if img else None))
    return SearchResponse(mode=out["mode"], condition=req.condition, parsed=parsed, filtered=out["filtered"], candidates=out["candidates"],
                          results=results, empty_reasons=out["empty_reasons"])


@app.post("/api/compare", response_model=CompareResponse, summary="화가 비교 (F2010)")
def compare(req: CompareRequest) -> CompareResponse:
    require_ready()
    if req.a == req.b:
        raise HTTPException(400, "서로 다른 두 화가를 골라 주세요.")
    from services.compare import compare as run_compare

    a, b = get_painter(req.a), get_painter(req.b)
    out = run_compare(a, b, req.focus)
    painters = []
    for p in (a, b):
        imgs = painter_images(p["id"])
        shown = [to_image(w, "work") for w in imgs["works"]] + [to_image(x, "related") for x in imgs["article_images"]]
        painters.append(ComparePainter(painter=summary(p), images=shown[:3]))
    return CompareResponse(focus=out["focus"], answer=out["answer"], cited=to_evidence(out["cited"]),
                           evidence=to_evidence(out["evidence"]), painters=painters)


# 화면 파일(HTML·CSS·JS)은 맨 마지막에 연결한다 (위의 /api 주소보다 우선하지 않도록)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
