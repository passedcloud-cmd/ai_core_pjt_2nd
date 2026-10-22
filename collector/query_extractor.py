"""
collector/query_extractor.py  (요구사항 F2003 — 검색어 추출, 프롬프트 1)
=======================================================================
화가 1명과 사용자 질문을 받아서, 위키백과에서 찾아볼 검색어 목록을 만든다.

흐름:
  화가 정보 + 질문 ──▶ 프롬프트 1 (LLM) ──▶ JSON 배열 ──▶ 파싱·정리
                                                           │
                       화가 본인 문서 제목을 항상 맨 앞에 끼워 넣음 ◀┘

[확인 방법]  (명세서 4.2 검증: 프롬프트 1이 뽑은 검색어 목록 확인)
  python -m collector.query_extractor --painter "클로드 모네" --question "수련 연작은 몇 점이고 어디 있어?"
"""

import argparse
import json
import re

from langchain_openai import ChatOpenAI

import config
from data_prep.painters import build_index_text, find_painter, load_painters
from rag.prompts import QUERY_EXTRACTION_PROMPT


def get_llm(model: str | None = None, timeout: float | None = None) -> ChatOpenAI:
    """
    GMS를 거쳐 OpenAI 대화 모델을 쓰는 객체를 만든다. 모델을 안 주면 config.CHAT_MODEL.
    timeout초 안에 응답이 없으면 포기하고 다시 보낸다 (GMS 응답이 가끔 25~59초씩 걸리는 문제 대응).
    """
    config.check_api_key()
    return ChatOpenAI(
        model=model or config.CHAT_MODEL,
        temperature=config.LLM_TEMPERATURE,
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_BASE_URL,
        timeout=timeout or config.LLM_TIMEOUT,
        max_retries=config.LLM_MAX_RETRIES,
    )


def extract_json_array(raw: str) -> list:
    """
    LLM 답변에서 JSON 배열을 꺼낸다. (프롬프트 1·2 공용)
    LLM이 가끔 ```json ... ``` 으로 감싸거나 앞뒤에 말을 붙여도 배열 부분만 찾아서 읽는다.
    읽을 수 없으면 빈 목록을 돌려준다. (프로그램이 멈추지 않게)
    """
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def parse_query_list(raw: str) -> list[str]:
    """LLM 답변 → 검색어 문자열 목록"""
    return [str(item).strip() for item in extract_json_array(raw) if isinstance(item, (str, int)) and str(item).strip()]


def extract_queries(painter: dict, question: str, llm=None, verbose: bool = False) -> list[str]:
    """
    검색어 목록을 돌려준다. 첫 번째는 항상 화가 본인의 위키백과 문서 제목이다. (F2003, F2004 규칙)
    """
    chain = QUERY_EXTRACTION_PROMPT | (llm or get_llm())
    response = chain.invoke(
        {
            "painter_info": build_index_text(painter),
            "question": question,
            "max_queries": config.MAX_SEARCH_QUERIES,
        }
    )
    if verbose:
        print(f"🤖 LLM 원본 답변: {response.content}")

    extracted = parse_query_list(response.content)[: config.MAX_SEARCH_QUERIES]

    # 화가 본인 문서를 맨 앞에 고정하고, 중복(띄어쓰기·대소문자만 다른 것 포함)은 제거
    own_title = painter.get("wiki_title") or painter["name"]
    queries, seen = [], set()
    for q in [own_title, *extracted]:
        key = q.replace(" ", "").lower()
        if key not in seen:
            seen.add(key)
            queries.append(q)
    return queries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="검색어 추출 테스트 (F2003)")
    parser.add_argument("--painter", required=True, help='화가 이름 (예: "클로드 모네")')
    parser.add_argument("--question", required=True, help="사용자 질문")
    args = parser.parse_args()

    painter = find_painter(load_painters(config.PAINTERS_PATH), args.painter)
    print(f"🎨 화가: {painter['name']} ({painter['id']})")
    print(f"❓ 질문: {args.question}\n")

    queries = extract_queries(painter, args.question, verbose=True)

    print(f"\n✅ 최종 검색어 {len(queries)}개")
    for i, q in enumerate(queries, 1):
        tag = "  ← 화가 본인 문서 (항상 포함)" if i == 1 else ""
        print(f"  {i}. {q}{tag}")
