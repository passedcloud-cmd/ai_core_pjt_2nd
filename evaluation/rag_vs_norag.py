"""
evaluation/rag_vs_norag.py  (요구사항 F2015 — RAG 사용 전후 품질 비교)
=====================================================================
같은 질문을 ① RAG 없이(모델이 원래 아는 것만으로) ② RAG로(수집한 문서를 근거로) 답하게 하고,
명세서 4.6의 세 기준으로 비교해 문서(docs/F2015_RAG_전후_비교.md)로 만든다.

  기준 ① 사실 정확성     : 필수 사실 포함 여부(코드가 원문 키워드로 기계 채점)
                            + 틀린 주장 수(채점 모델이 '원문 발췌'와 대조)
  기준 ② 근거 제시 여부   : [근거 N] 표기와 인용된 근거 수
  기준 ③ 모르면 모른다고  : "확인되지 않", "알 수 없" 같은 표현 여부 (특히 문서에 답이 없는 질문)

공정하게 비교하려고 두 쪽 모두 같은 답변 모델(config.ANSWER_MODEL)과 같은 '도슨트' 역할을 쓴다.
다른 점은 '근거를 주느냐'뿐이다. 정답은 우리가 수집한 원문(위키백과·국립현대미술관)에서 미리 확인했다.

[실행 방법]
  python -m evaluation.rag_vs_norag     → docs/F2015_RAG_전후_비교.md + docs/f2015_results.json
"""

import json
import re
import time
from datetime import datetime

from langchain_core.prompts import ChatPromptTemplate

import config
from collector.query_extractor import extract_json_array, get_llm
from data_prep.painters import find_painter, load_painters
from rag.graph import ask

JUDGE_MODEL = "gpt-4.1"   # 채점은 답변 모델과 다른, 더 큰 모델로 (자기 답을 자기가 채점하지 않도록)
OUT_MD = config.BASE_DIR / "docs" / "F2015_RAG_전후_비교.md"
OUT_JSON = config.BASE_DIR / "docs" / "f2015_results.json"

# ---------------------------------------------------------------------------
# 대표 질문 7개 — 정답은 수집한 원문에서 직접 확인한 것 (source: 원문 문서, quote_from: 발췌 시작 문구)
# ---------------------------------------------------------------------------
QUESTIONS = [
    {"painter": "클로드 모네", "question": "수련 연작은 몇 점이고, 오랑주리 미술관에는 몇 점이 상설 전시돼 있어?",
     "facts": [["250"], ["8점", "8 점", "여덟"]], "answer": "약 250점 / 오랑주리 미술관에 수련 벽화 8점",
     "source": ("wiki", "수련 (모네)"), "quote_from": ["약 250점", "수련 벽화 8점"]},
    {"painter": "파블로 피카소", "question": "게르니카 폭격 때 사상자는 몇 명이었고, 어느 부대가 폭격했어?",
     "facts": [["1654", "1,654"], ["889"], ["콘도르"]], "answer": "사망 1,654명 · 부상 889명 / 나치 독일 콘도르 군단",
     "source": ("wiki", "게르니카 폭격"), "quote_from": ["콘도르 군단"]},
    {"painter": "파블로 피카소", "question": "게르니카는 지금 어느 미술관에 있고, 그림 크기는 얼마야?",
     "facts": [["레이나 소피아"], ["349"], ["776"]], "answer": "마드리드 레이나 소피아 국립 미술관 / 349.3 × 776.6cm",
     "source": ("wiki", "게르니카 (피카소)"), "quote_from": ["349.3"]},
    {"painter": "김홍도", "question": "단원 풍속도첩은 지금 몇 점으로 되어 있고, 처음 구입했을 때는 몇 점이었어?",
     "facts": [["25점", "25 점", "스물다섯"], ["27점", "27 점", "스물일곱"]], "answer": "현재 25점 / 1918년 구입 당시 27점 (군선도 2점 분리)",
     "source": ("wiki", "김홍도 필 풍속도 화첩"), "quote_from": ["국립중앙박물관의 기록에 의하면"]},
    {"painter": "이중섭", "question": "은지화는 어떤 재료와 도구로 그렸어?",
     "facts": [["은박지", "은종이"], ["철촉필", "철필"]], "answer": "양담배갑 속 은박지 / 철촉필로 눌러 윤곽을 그리고 물감·먹물을 문지름",
     "source": ("mmca", "물고기와 아이들(은지화)"), "quote_from": ["은지화는 양담배갑"]},
    {"painter": "진환", "question": "진환은 1941년에 어떤 미술 단체를, 누구와 함께 결성했어?",
     "facts": [["조선신미술가협회"], ["이중섭"]], "answer": "조선신미술가협회 / 문학수 · 이중섭 · 이쾌대 등과 함께",
     "source": ("mmca", "연기와 소"), "quote_from": ["1941년에"]},
    {"painter": "파블로 피카소", "question": "피카소가 가장 좋아한 음식은 뭐야?",
     "facts": [], "answer": "수집한 문서에 없음 → '확인되지 않는다'고 답해야 정답",
     "source": None, "quote_from": []},
]

UNKNOWN = re.compile(r"확인되지\s*않|확인할 수 없|알 수 없|알려져 있지 않|알려지지 않|모르|정보가 없|나와 있지 않")

# ---------------------------------------------------------------------------
# RAG 없이 답하기: 같은 모델·같은 도슨트 역할, 근거만 없음
# ---------------------------------------------------------------------------
NO_RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "너는 미술관의 친절한 도슨트야. 관람객의 질문에 한국어로, 3~6문장 정도로 쉽고 친절하게 답해."),
    ("human", "[화가]\n{painter}\n\n[질문]\n{question}"),
])

# 채점: 원문 발췌와 대조해 주장마다 '맞음 / 원문과 다름 / 원문에 없음'
JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """너는 엄격한 사실 검증 담당자야. [원문 발췌]만을 기준으로 [답변]을 채점해.

[할 일]
1. 답변에서 사실 주장(숫자, 이름, 장소, 날짜, 사건 등)을 하나씩 뽑아.
2. 주장마다 판정해:
   - "맞음": 원문 발췌의 내용과 일치
   - "틀림": 원문 발췌의 내용과 어긋남 (숫자·이름·장소가 다름)
   - "확인 불가": 원문 발췌에 그 내용이 없음
3. "제공된 문서에서는 확인되지 않습니다" 같은 '모른다'는 말은 주장이 아니므로 뽑지 마.

[출력 형식]
설명 없이 JSON 배열 하나만. 주장이 없으면 [].
[{{"claim": "주장 요약", "verdict": "맞음|틀림|확인 불가", "why": "짧은 이유"}}]"""),
    ("human", "[원문 발췌]\n{reference}\n\n[답변]\n{answer}"),
])


def reference_text(q: dict) -> str:
    """정답 원문 발췌: 수집한 원문 문서에서 정답이 나오는 부분 앞뒤를 잘라 온다 (채점 기준)."""
    if not q["source"]:
        return "(이 질문에 대한 내용은 수집한 문서 어디에도 없음)"
    kind, title = q["source"]
    if kind == "wiki":
        docs = [json.loads(p.read_text(encoding="utf-8")) for p in config.WIKI_DOCS_DIR.glob("*.json")]
        text = next(d["text"] for d in docs if d["title"] == title)
    else:
        text = next(w["description"] for w in json.loads(config.MMCA_DOCS_PATH.read_text(encoding="utf-8")) if w["title"] == title)
    parts = []
    for anchor in q["quote_from"]:
        i = text.find(anchor)
        parts.append(re.sub(r"\s+", " ", text[max(0, i - 250): i + 450]))
    return f"(출처: {'위키백과' if kind == 'wiki' else '국립현대미술관 소장품'} · {title})\n" + "\n…\n".join(parts)


def fact_hits(q: dict, answer: str) -> list[bool]:
    """필수 사실마다, 답변에 그 사실의 키워드(여러 표기 중 하나)가 있는지"""
    flat = answer.replace(",", "")
    return [any(k.replace(",", "") in flat for k in alts) for alts in q["facts"]]


def judge(reference: str, answer: str, llm) -> list[dict]:
    claims = extract_json_array((JUDGE_PROMPT | llm).invoke({"reference": reference, "answer": answer}).content)
    return [c for c in claims if isinstance(c, dict) and c.get("verdict") in ("맞음", "틀림", "확인 불가")]


def score(q: dict, answer: str, cited: int, reference: str, judge_llm) -> dict:
    hits = fact_hits(q, answer)
    claims = judge(reference, answer, judge_llm)
    count = lambda v: sum(1 for c in claims if c["verdict"] == v)
    return {
        "answer": answer,
        "facts_hit": sum(hits), "facts_total": len(hits),
        "claims": claims, "right": count("맞음"), "wrong": count("틀림"), "unverifiable": count("확인 불가"),
        "cited": cited, "has_cite_mark": bool(re.search(r"\[근거\s*\d", answer)),
        "says_unknown": bool(UNKNOWN.search(answer)),
    }


def run() -> list[dict]:
    painters = load_painters(config.PAINTERS_PATH)
    answer_llm = get_llm(config.ANSWER_MODEL)
    judge_llm = get_llm(JUDGE_MODEL)
    results = []
    for n, q in enumerate(QUESTIONS, 1):
        painter = find_painter(painters, q["painter"])
        reference = reference_text(q)
        print(f"[{n}/{len(QUESTIONS)}] {painter['name']} · {q['question']}")

        t = time.time()
        plain = (NO_RAG_PROMPT | answer_llm).invoke({"painter": painter["name"], "question": q["question"]}).content.strip()
        t_plain = time.time() - t

        t = time.time()
        state = ask(painter, q["question"])   # 수집 → 색인 → 검색 → 답변 (이미 물은 질문이면 캐시)
        t_rag = time.time() - t

        results.append({
            **{k: q[k] for k in ("painter", "question", "answer")}, "reference": reference,
            "no_rag": {**score(q, plain, 0, reference, judge_llm), "seconds": round(t_plain, 1)},
            "rag": {**score(q, state["answer"], len(state["cited"]), reference, judge_llm), "seconds": round(t_rag, 1),
                    "sources": [f"[근거 {e['no']}] {e['source_type']} · {e['title']}" for e in state["cited"]]},
        })
        r = results[-1]
        print(f"   RAG 없이: 필수 사실 {r['no_rag']['facts_hit']}/{r['no_rag']['facts_total']} · 틀림 {r['no_rag']['wrong']} · 모른다 {r['no_rag']['says_unknown']}")
        print(f"   RAG    : 필수 사실 {r['rag']['facts_hit']}/{r['rag']['facts_total']} · 틀림 {r['rag']['wrong']} · 인용 {r['rag']['cited']} · 모른다 {r['rag']['says_unknown']}")
    return results


# ---------------------------------------------------------------------------
# 문서 만들기
# ---------------------------------------------------------------------------
def mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def write_report(results: list[dict]) -> None:
    def total(side, key):
        return sum(r[side][key] for r in results)

    answerable = [r for r in results if r["no_rag"]["facts_total"]]
    unanswerable = [r for r in results if not r["no_rag"]["facts_total"]]
    facts_all = sum(r["no_rag"]["facts_total"] for r in answerable)
    full = lambda side: sum(1 for r in answerable if r[side]["facts_hit"] == r[side]["facts_total"])

    lines = [
        "# F2015 RAG 사용 전후 품질 비교",
        "",
        f"- 작성: {datetime.now():%Y-%m-%d %H:%M} · 실행: `python -m evaluation.rag_vs_norag`",
        f"- 답변 모델: 두 쪽 모두 `{config.ANSWER_MODEL}` (같은 '미술관 도슨트' 역할). 다른 점은 **근거 문서를 주느냐**뿐",
        f"- 채점 모델: `{JUDGE_MODEL}` — 답변 모델과 다른 더 큰 모델이 **수집한 원문 발췌**와 대조해 주장마다 판정",
        "- 질문 선정: 근거 없이 답하면 틀리기 쉬운 구체적 사실(숫자·이름·크기) 5개 + 한국 화가의 덜 알려진 사실 1개 + 문서에 답이 없는 질문 1개",
        "",
        "## 1. 비교 기준 (명세서 4.6)",
        "",
        "| 기준 | 어떻게 쟀나 |",
        "|---|---|",
        "| ① 사실 정확성 | **필수 사실 포함**: 정답 키워드(예: 250, 8점, 1654)가 답에 있는지 코드가 확인 / **틀린 주장**: 채점 모델이 원문과 어긋난다고 판정한 주장 수 |",
        "| ② 근거 제시 여부 | 답변의 `[근거 N]` 표기와, 화면에 표시되는 인용 근거(제목·원문 링크) 수 |",
        "| ③ 모르면 모른다고 답하는지 | \"확인되지 않\", \"알 수 없\" 같은 표현이 있는지 — 특히 문서에 답이 없는 7번 질문에서 |",
        "",
        "## 2. 결과 요약",
        "",
        "| 항목 | RAG 없이 | RAG 사용 |",
        "|---|---|---|",
        f"| 필수 사실 포함 (전체 {facts_all}개) | {sum(r['no_rag']['facts_hit'] for r in answerable)}개 | {sum(r['rag']['facts_hit'] for r in answerable)}개 |",
        f"| 필수 사실을 모두 맞힌 질문 ({len(answerable)}개 중) | {full('no_rag')}개 | {full('rag')}개 |",
        f"| 원문과 어긋난 주장 (틀림) | {total('no_rag', 'wrong')}개 | {total('rag', 'wrong')}개 |",
        f"| 원문으로 확인 안 되는 주장 | {total('no_rag', 'unverifiable')}개 | {total('rag', 'unverifiable')}개 |",
        f"| 근거 제시 ([근거 N] 표기) | {sum(r['no_rag']['has_cite_mark'] for r in results)}/{len(results)} 질문 | {sum(r['rag']['has_cite_mark'] for r in results)}/{len(results)} 질문 (인용 근거 {total('rag', 'cited')}개) |",
        f"| 답이 없는 질문에 '모른다' ({len(unanswerable)}개 중) | {sum(r['no_rag']['says_unknown'] for r in unanswerable)}개 | {sum(r['rag']['says_unknown'] for r in unanswerable)}개 |",
        f"| 평균 응답 시간 | {sum(r['no_rag']['seconds'] for r in results) / len(results):.1f}초 | {sum(r['rag']['seconds'] for r in results) / len(results):.1f}초 |",
        "",
        "### 채점 방식의 한계",
        "",
        "- '필수 사실 포함'은 정답 키워드(예: 349, 776)를 기계적으로 찾기 때문에, **단위를 바꿔 어림한 답은 놓칠 수 있다** (예: \"약 7.8m × 3.5m\"). 표의 RAG 없이 점수는 그만큼 낮게 잡혔을 수 있다.",
        "- 채점 모델의 '확인 불가'는 *틀렸다*는 뜻이 아니라 *원문 발췌로 확인할 수 없다*는 뜻이다. 원문 발췌는 정답 부분만 잘라 왔으므로, 맞는 일반 상식도 '확인 불가'로 셀 수 있다.",
        "",
        "## 3. 질문별 비교",
        "",
        "| # | 질문 | 정답 | RAG 없이: 필수 사실 · 틀림 · 모른다 | RAG: 필수 사실 · 틀림 · 인용 · 모른다 |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        a, b = r["no_rag"], r["rag"]
        fa = f"{a['facts_hit']}/{a['facts_total']}" if a["facts_total"] else "-"
        fb = f"{b['facts_hit']}/{b['facts_total']}" if b["facts_total"] else "-"
        lines.append(f"| {i} | {r['painter']} · {r['question']} | {r['answer']} | {fa} · {a['wrong']} · {mark(a['says_unknown'])} | {fb} · {b['wrong']} · {b['cited']} · {mark(b['says_unknown'])} |")

    lines += ["", "## 4. 답변 원문 (나란히)", ""]
    for i, r in enumerate(results, 1):
        lines += [f"### {i}. {r['painter']} — {r['question']}", "", f"**정답**: {r['answer']}", "",
                  "| RAG 없이 | RAG 사용 |", "|---|---|",
                  f"| {r['no_rag']['answer'].replace(chr(10), ' ')} | {r['rag']['answer'].replace(chr(10), ' ')} |", ""]
        if r["rag"]["sources"]:
            lines.append("**RAG 인용 근거**: " + " / ".join(r["rag"]["sources"]))
            lines.append("")
        wrong = [c for side in ("no_rag", "rag") for c in r[side]["claims"] if c["verdict"] == "틀림"]
        for side, label in (("no_rag", "RAG 없이"), ("rag", "RAG 사용")):
            bad = [c for c in r[side]["claims"] if c["verdict"] == "틀림"]
            if bad:
                lines.append(f"- ❌ {label}의 틀린 주장: " + "; ".join(f"{c['claim']} ({c['why']})" for c in bad))
        if wrong:
            lines.append("")
        lines += ["<details><summary>채점 기준 원문 발췌</summary>", "", r["reference"], "", "</details>", ""]

    OUT_MD.parent.mkdir(exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✅ 문서 저장: {OUT_MD}\n   원자료: {OUT_JSON}")


if __name__ == "__main__":
    write_report(run())
