"""
api/schemas.py — 화면과 서버가 주고받는 데이터의 '약속서' (F2013: 요청·응답 형식 정의)
=====================================================================================
화면(web/app.js)은 이 모양대로 요청을 보내고, 서버는 이 모양대로 응답한다.
FastAPI가 이 정의를 읽어서 http://localhost:8600/docs 에 API 설명서를 자동으로 만들어 준다.

비유: 식당 주문서 양식. 손님(화면)과 주방(서버)이 같은 양식을 쓰니까 주문이 엇갈리지 않는다.
"""

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 공통 부품
# ---------------------------------------------------------------------------
class ImageItem(BaseModel):
    """작품·관련 이미지 1장 (보여주기용)"""
    title: str = Field(description="작품명 또는 이미지 설명")
    sub: str = Field("", description="제작 연도 · 소장처 등 보조 설명")
    thumb: str = Field(description="목록용 이미지 주소 (폭 500px)")
    large: str = Field(description="크게 볼 이미지 주소 (폭 1920px, 원본이 작으면 원본)")
    page: str = Field("", description="원본 파일 페이지 (출처·이용 조건, NF2007)")
    kind: Literal["work", "related", "portrait"] = Field(description="작품(위키데이터) / 관련 이미지(위키백과 문서) / 초상")


class PainterSummary(BaseModel):
    """화가 목록·카드에 쓰는 요약 정보"""
    id: str = Field(description="위키데이터 ID (예: Q296)")
    name: str
    name_en: str = ""
    label: str = Field(description="'프랑스 · 1840–1926' 처럼 짧은 소개")
    rank: int = Field(description="유명한 순위 (sitelinks 기준)")
    thumb: str | None = Field(None, description="대표 이미지 (작은 크기)")


class Fact(BaseModel):
    label: str
    value: str


class PainterDetail(PainterSummary):
    """화가 1명의 상세 정보 + 작품 이미지"""
    description: str = ""
    facts: list[Fact] = Field(default_factory=list, description="국적·사조·장르·생애 등 (화가 메타데이터)")
    wiki_url: str = ""
    portrait: ImageItem | None = None
    hero: ImageItem | None = Field(None, description="첫 화면에 크게 깔 대표작")
    works: list[ImageItem] = Field(default_factory=list)
    related: list[ImageItem] = Field(default_factory=list)


class Evidence(BaseModel):
    """근거 1건 (F2008: 제목 + 원문 출처, F2016: 출처 종류 구분)"""
    no: int = Field(description="답변 속 [근거 N]의 N")
    source_type: str = Field(description="화가 메타데이터 / 위키백과 / 국립현대미술관 소장품")
    title: str
    url: str = ""
    chunk_id: str = ""
    text: str = Field("", description="근거 본문 (화면의 '검색된 근거 전체'에서 일부만 보여줌)")
    painter_name: str | None = Field(None, description="화가 비교에서 어느 화가의 근거인지")


# ---------------------------------------------------------------------------
# 상태 (F2012: 색인 없음·키 누락 안내용)
# ---------------------------------------------------------------------------
class Status(BaseModel):
    api_key_ok: bool
    meta_count: int = Field(description="화가 메타데이터 청크 수")
    wiki_count: int = Field(description="외부 문서 청크 수 (위키백과 + 국립현대미술관)")
    painter_count: int
    message: str = Field("", description="문제가 있을 때 화면에 띄울 안내")


# ---------------------------------------------------------------------------
# 질문하기 (F2006~F2008) — 응답은 한 줄에 JSON 하나씩 흘려보낸다 (NDJSON)
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    painter_id: str = Field(description="질문할 화가의 위키데이터 ID")
    question: str = Field(min_length=1, max_length=500)
    search_type: Literal["similarity", "mmr"] = Field("similarity", description="근거 검색 방식 (F2014)")


class AskEvent(BaseModel):
    """
    /api/ask 응답은 처리 단계마다 이 모양의 줄을 하나씩 보낸다. (F2012 진행 상태 표시)
      {"type": "status",    "message": "근거 문서를 확인하는 중…"}
      {"type": "retrieved", "queries": [...], "documents": [...], "cached": [...], "evidence_count": 6}
      {"type": "answer",    "answer": "...", "cited": [...], "evidence": [...]}
      {"type": "error",     "message": "친절한 안내 문구"}
    """
    type: Literal["status", "retrieved", "answer", "error"]
    message: str = ""
    queries: list[str] = Field(default_factory=list)
    documents: list[str] = Field(default_factory=list)
    cached: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    answer: str = ""
    cited: list[Evidence] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    search_type: str = ""


# ---------------------------------------------------------------------------
# 조건으로 화가 찾기 (F2011)
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    condition: str = Field(min_length=1, max_length=200, description="예: 19세기 프랑스 인상주의 풍경화가")


class SearchResult(BaseModel):
    painter: PainterSummary
    reason: str = Field(description="조건에 맞는 이유 (LLM 검증 결과)")
    image: ImageItem | None = None


class SearchResponse(BaseModel):
    condition: str
    parsed: list[Fact] = Field(description="해석된 조건 (국적·사조·장르·시대·기타)")
    filtered: int = Field(description="조건 필터 통과 수")
    candidates: int = Field(description="LLM 검증에 넘긴 후보 수")
    results: list[SearchResult]
    empty_reasons: list[str] = Field(default_factory=list, description="0명일 때 이유 안내")


# ---------------------------------------------------------------------------
# 화가 비교 (F2010)
# ---------------------------------------------------------------------------
class CompareRequest(BaseModel):
    a: str = Field(description="화가 A ID")
    b: str = Field(description="화가 B ID")
    focus: str = Field("", max_length=100, description="비교 관점 (비우면 기본 관점)")


class ComparePainter(BaseModel):
    painter: PainterSummary
    images: list[ImageItem]


class CompareResponse(BaseModel):
    focus: str
    answer: str = Field(description="마크다운 (비교표 + 공통점·차이점)")
    cited: list[Evidence]
    evidence: list[Evidence]
    painters: list[ComparePainter]
