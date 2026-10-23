"""
config.py — 프로젝트 전체 설정을 한 곳에 모아둔 파일
=====================================================
모델 이름, 컬렉션 이름, 배치 크기처럼 "나중에 바꿀 수도 있는 값"은 전부 여기에 둔다.
코드 여기저기에 숫자를 흩어두면 바꿀 때 빠뜨리기 쉽기 때문.

API 키와 주소는 코드에 직접 쓰지 않고 .env 파일에서만 읽는다. (명세서 NF2003)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # .env 파일 내용을 환경변수로 불러온다

# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PAINTERS_PATH = DATA_DIR / "painters.json"
# ChromaDB가 색인 결과를 저장하는 폴더. 환경변수 CHROMA_DIR로 바꿀 수 있다.
# (예: 빈 폴더를 가리키게 해서 '색인 없음' 안내를 검증할 때 — 진짜 색인은 건드리지 않음)
CHROMA_DIR = Path(os.getenv("CHROMA_DIR") or BASE_DIR / "chroma_db")


def _ascii_chroma_path(path: Path) -> Path:
    """
    ChromaDB는 폴더 경로에 한글이 있으면(예: 바탕화면\\AI_코어프로젝트2_화가검색봇\\chroma_db)
    검색 색인 파일을 열지 못한다 ("Error loading hnsw index"). 내부 C++ 코드가 한글 경로를 못 읽기 때문.
    → 지금 실행 위치 기준의 '상대 경로'(예: chroma_db)로 바꿔 넘기면 한글이 빠져서 정상 동작한다.
    """
    if str(path).isascii():
        return path
    try:
        relative = Path(os.path.relpath(path))
    except ValueError:  # 드라이브가 달라 상대 경로를 만들 수 없음
        relative = path
    if not str(relative).isascii():
        print(f"⚠️ 색인 폴더 경로에 한글이 있어 검색이 실패할 수 있어요: {path}\n"
              f"   프로젝트 폴더에서 실행하거나, 영어 이름 폴더로 옮겨 주세요.")
    return relative if str(relative).isascii() else path


CHROMA_DIR = _ascii_chroma_path(CHROMA_DIR)


# ---------------------------------------------------------------------------
# OpenAI (SSAFY GMS 경유)
# ---------------------------------------------------------------------------
def _clean_base_url(url: str) -> str:
    """
    GMS 주소는 '.../v1' 까지만 있어야 한다.
    라이브러리가 뒤에 '/embeddings', '/chat/completions'를 알아서 붙이기 때문.
    실수로 끝까지 복사한 주소가 들어와도 동작하도록 뒷부분을 잘라준다.
    """
    url = url.strip().rstrip("/")
    for tail in ("/chat/completions", "/embeddings", "/completions"):
        if url.endswith(tail):
            url = url[: -len(tail)]
    return url


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = _clean_base_url(os.getenv("OPENAI_BASE_URL", ""))

EMBEDDING_MODEL = "text-embedding-3-small"  # 텍스트 → 숫자 벡터로 바꾸는 모델
CHAT_MODEL = "gpt-4o-mini"                  # 프롬프트 1(검색어 추출)·2(후보 선별)에 쓸 대화 모델
ANSWER_MODEL = "gpt-4.1-mini"               # 프롬프트 3(답변 생성)에 쓸 모델
# ↑ 답변 모델 비교 실험(같은 질문 3회): gpt-4o-mini는 근거 번호를 틀리게 붙인 경우가 있었고,
#   gpt-4.1-mini는 틀린 인용 0회 + 질문 핵심(소장처)을 가장 잘 담았다.


def check_api_key() -> None:
    """API 키·주소가 비어 있으면 친절한 안내와 함께 멈춘다."""
    missing = [
        name
        for name, value in [("OPENAI_API_KEY", OPENAI_API_KEY), ("OPENAI_BASE_URL", OPENAI_BASE_URL)]
        if not value
    ]
    if missing:
        raise SystemExit(
            f"❌ .env 파일에 {', '.join(missing)} 값이 없어요.\n"
            "   env.example 을 참고해서 .env 에 채워 주세요."
        )


# ---------------------------------------------------------------------------
# Vector DB (ChromaDB)
# ---------------------------------------------------------------------------
META_COLLECTION = "painter_meta"  # 컬렉션 1: 화가 메타데이터 (화가 1명 = 청크 1개)
WIKI_COLLECTION = "painter_wiki"  # 컬렉션 2: 위키백과 문서 청크 (F2005에서 사용)

INDEX_BATCH_SIZE = 100  # 한 번에 임베딩 요청을 보낼 텍스트 수 (너무 크면 API가 거절할 수 있음)


# ---------------------------------------------------------------------------
# 외부 문서 수집 (F2003 ~ F2004)
# ---------------------------------------------------------------------------
MAX_SEARCH_QUERIES = 5     # 프롬프트 1이 뽑을 검색어 최대 개수 (화가 본인 문서 제외)
LLM_TEMPERATURE = 0        # 0 = 매번 비슷한 답 (검색어 추출은 창의성보다 일관성이 중요)
# GMS 응답 속도가 들쭉날쭉해서(같은 요청이 보통 3초, 가끔 25~59초) 시간 제한을 두고 넘으면 다시 보낸다
LLM_TIMEOUT = 30           # 일반 호출(답변·비교표 등 긴 출력) 시간 제한(초)
LLM_FAST_TIMEOUT = 10      # 짧은 출력 호출(조건 해석·검증) 시간 제한(초)
LLM_MAX_RETRIES = 2        # 시간 초과·일시 오류 때 다시 보낼 횟수

WIKI_API_URL = "https://ko.wikipedia.org/w/api.php"
WIKI_CONTACT_EMAIL = os.getenv("WIKI_CONTACT_EMAIL", "").strip()
WIKI_USER_AGENT = f"PainterRAG-SSAFY-StudentProject/1.0 ({WIKI_CONTACT_EMAIL})"  # NF2006
WIKI_REQUEST_INTERVAL = 1.0  # 위키백과 요청 사이 쉬는 시간(초). 서버에 부담 안 주기 (NF2006)
WIKI_MAX_RETRIES = 3         # 요청 실패 시 다시 시도할 횟수

CANDIDATES_PER_QUERY = 5     # 검색어 1개당 가져올 후보 문서 수
SUMMARY_CHARS = 300          # 프롬프트 2에 보여줄 요약 길이 (본문 전체는 선별 후에만 받음)
MIN_DOC_LENGTH = 300         # 본문이 이보다 짧은 문서는 버림 (단, 화가 본인 문서는 예외)

WIKI_DOCS_DIR = DATA_DIR / "wiki_docs"               # 수집한 문서 본문 (문서 1개 = 파일 1개)
COLLECT_CACHE_PATH = DATA_DIR / "collect_cache.json"  # 이미 수집한 '화가+검색어' 기록 (NF2002)


# ---------------------------------------------------------------------------
# 두 번째 문서 출처: 국립현대미술관 소장작품 API (F2016 여러 문서 출처 결합)
# ---------------------------------------------------------------------------
MMCA_API_URL = "https://api.kcisa.kr/openapi/service/rest/meta10/get20150041"
MMCA_API_KEY = os.getenv("MMCA_API_KEY", "").strip()   # 문화데이터광장에서 발급 (.env에만 보관, NF2003)
MMCA_DOCS_PATH = DATA_DIR / "mmca_works.json"          # 받아 온 소장작품 설명 원본
MMCA_SOURCE_TYPE = "국립현대미술관 소장품"              # 근거에 표시할 출처 종류 (위키백과와 구분)
MMCA_SINGLE_CHUNK_MAX = 1500   # 작품 설명이 이 길이 이하면 '작품 1점 = 청크 1개'로 자르지 않는다


# ---------------------------------------------------------------------------
# 작품 이미지 (화면 표시용 — RAG 답변에는 쓰이지 않음)
# ---------------------------------------------------------------------------
IMAGES_PATH = DATA_DIR / "painter_images.json"  # 화가별 작품·관련 이미지 정보 (이미지 파일은 저장 안 하고 주소만)
MAX_WORKS_PER_PAINTER = 40     # 위키데이터 작품 이미지: 화가당 최대 개수 (유명한 순)
MAX_ARTICLE_IMAGES = 30        # 위키백과 문서 속 이미지: 화가당 최대 개수
MIN_IMAGE_SIDE = 400           # 짧은 변이 이보다 작은 이미지는 버림 (아이콘·저화질 제외)
# 위키미디어 썸네일은 정해진 폭만 허용된다 (그 외 폭은 400 오류) — 직접 확인한 값
THUMB_WIDTHS = [250, 330, 500, 960, 1280, 1920]


# ---------------------------------------------------------------------------
# 외부 문서 색인 (F2005)
# ---------------------------------------------------------------------------
CHUNK_SIZE = 1000     # 청크 1개의 최대 글자 수
CHUNK_OVERLAP = 200   # 앞 청크와 겹치게 둘 글자 수 (문장이 청크 경계에서 잘려도 앞뒤 맥락이 남도록)

# 위키백과 본문 끝에 붙는 이 제목 이후는 근거로 쓸 내용이 거의 없어서 잘라낸다
WIKI_DROP_SECTIONS = ["각주", "주석", "같이 보기", "외부 링크", "참고 문헌", "참고 자료", "출처"]


# ---------------------------------------------------------------------------
# 근거 검색 (F2006)
# ---------------------------------------------------------------------------
# 근거 = 화가 메타데이터 1건(항상) + 위키 청크 WIKI_TOP_K개
WIKI_TOP_K = 5
SEARCH_TYPE = "similarity"  # "similarity"(유사도 순) 또는 "mmr"(비슷한 청크끼리 겹치지 않게 골고루)
MMR_FETCH_K = 20            # MMR일 때 먼저 넉넉히 뽑아둘 후보 수 (여기서 WIKI_TOP_K개를 고름)
MMR_LAMBDA = 0.5            # MMR 균형: 1에 가까울수록 '질문과의 관련성', 0에 가까울수록 '다양성'을 중시

# ---------------------------------------------------------------------------
# 조건 기반 화가 탐색 (F2011)
# ---------------------------------------------------------------------------
EXPLORE_CANDIDATES = 30         # 조건 필터를 통과한 화가 중 몇 명을 LLM 검증에 넘길지
EXPLORE_VERIFY_BATCH = 10       # LLM 검증을 몇 명씩 나눠서 할지 (30명을 한 번에 주면 중간 후보를 빠뜨리는 문제가 있었음)
EXPLORE_SIMILAR_NAMES = 10      # 조건으로 0명일 때 보여줄 '비슷한 이름' 화가 수 (오타 검색: '다반치' → 레오나르도 다 빈치)
EXPLORE_MODEL = "gpt-4.1-mini"  # 조건 해석·검증에 쓸 모델 (잘못 고르면 조건에 안 맞는 화가가 섞이므로 정확도 우선)


# ---------------------------------------------------------------------------
# 화가 비교 (F2010 도메인 특화 기능)
# ---------------------------------------------------------------------------
COMPARE_TOP_K = 4                          # 화가 1명당 가져올 위키 청크 수 (두 화가면 메타 2 + 청크 최대 8)
COMPARE_DEFAULT_FOCUS = "생애, 사조, 대표작, 화풍"  # 비교 관점을 비워 두면 쓸 기본값
