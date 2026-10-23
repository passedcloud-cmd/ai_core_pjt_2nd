# 🎨 화가 도슨트 — 근거를 보여주는 화가 RAG 서비스

> SSAFY AI 코어 프로젝트 2회차 · 도메인: **화가**

화가를 골라 질문하면, **수집한 문서만 근거로** 답하고 그 근거(출처 종류 · 제목 · 원문 링크)를 함께 보여주는 RAG 기반 지식 서비스입니다.
문서에 없는 내용은 지어내지 않고 **"제공된 문서에서는 확인되지 않습니다"** 라고 답합니다.

| | |
|---|---|
| 대상 | 한국어 위키백과에 문서가 있는 화가 **1,343명** (국적·시대 제한 없음) |
| 근거 출처 | 화가 메타데이터(Wikidata) · 한국어 위키백과 · 국립현대미술관 소장작품 설명 |
| 핵심 기술 | LangGraph(`retrieve → generate`) · ChromaDB 컬렉션 2개 · 프롬프트 3개 · SSAFY GMS(OpenAI) |
| 화면 | Streamlit (필수) / FastAPI + HTML·CSS·JS (심화 F2013) |

**질문 예시** — "모네의 수련 연작은 몇 점이고 어디 있어?" → *약 250점이 남아 있고 `[근거 3]` … 오랑주리 미술관에 수련 벽화 8점이 상설 전시되고 있어요 `[근거 2]`* + 📖 위키백과 · 수련 (모네)

---

## 📁 문서

| 문서 | 내용 |
|---|---|
| [요구사항 정의서](docs/요구사항_정의서.md) | 도메인 조건 충족 근거, 기능·비기능 요구사항 표(F2001~F2017, NF2001~NF2007), 수용 기준 |
| [RAG 파이프라인 설계문서](docs/RAG_파이프라인_설계문서.md) | 컬렉션 구조, 수집·청킹·색인 흐름, 병합·중복 제거, 프롬프트, 설정 실험, 근거 표시, 겪은 문제와 해결 |
| [RAG 사용 전후 비교 (F2015)](docs/F2015_RAG_전후_비교.md) | 대표 질문 7개를 RAG 없이/있게 답한 결과와 비교 |
| [검증 결과](docs/검증_결과.md) | 요구사항 대조표, 테스트 결과표(자동 22개 + 수동 6개), 심화–필수 비교, 제출 전 점검 목록 |

---

## 🚀 실행 방법

### 1) 준비

- Python **3.10 이상** (개발 환경: 3.12)
- SSAFY GMS API 키 (임베딩 `text-embedding-3-small`, 대화 `gpt-4o-mini` · `gpt-4.1-mini` 사용)

```bash
# 가상환경 만들기 (Windows)
python -m venv venv
venv\Scripts\activate          # Git Bash: source venv/Scripts/activate  /  macOS·Linux: source venv/bin/activate

# 라이브러리 설치 (명세서 지정 버전)
pip install -r requirements.txt
```

`env.example`을 복사해 `.env`로 이름을 바꾸고 값을 채웁니다.

| 변수 | 필수 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | SSAFY GMS에서 발급받은 키 |
| `OPENAI_BASE_URL` | ✅ | `https://gms.ssafy.io/gmsapi/api.openai.com/v1` (끝에 `/chat/completions`를 붙이지 않음) |
| `WIKI_CONTACT_EMAIL` | ✅ | 위키미디어 API 요청에 넣는 연락처 (위키미디어 이용 규칙) |
| `MMCA_API_KEY` | 선택 | 문화공공데이터광장 '국립현대미술관_소장작품' 서비스 키 (F2016에서만 사용) |

### 2) 데이터 준비와 색인

`data/` 폴더(화가 메타데이터 · 수집한 문서 · 작품 이미지 정보)는 함께 제공됩니다. **Vector DB(`chroma_db/`)는 제출물에 없으므로 처음 한 번 색인**해야 합니다.

```bash
python index_meta.py      # ① 화가 메타데이터 1,343명 → painter_meta 컬렉션 (30초 안팎)
python index_wiki.py      # ② 이미 수집해 둔 위키백과 문서 → painter_wiki 컬렉션
python index_mmca.py      # ③ (선택, MMCA_API_KEY 필요) 국립현대미술관 소장작품 설명 → painter_wiki 컬렉션
```

> 새로 하는 질문의 위키백과 문서는 **질문할 때 자동으로 수집·색인**됩니다. ②는 이미 모아 둔 문서를 다시 넣는 단계입니다.
> `python index_wiki.py --reset`을 하면 같은 컬렉션의 국립현대미술관 청크도 지워지므로, 그 뒤에는 ③을 다시 실행하세요.

<details>
<summary>데이터를 처음부터 다시 모으려면 (선택)</summary>

```bash
python collect_painters.py          # 위키데이터에서 화가 메타데이터 수집 → data/painters.json (--top 500 처럼 상위 N명만도 가능)
python index_meta.py --reset        # 화가 수가 바뀌었으면 반드시 --reset
python -u collect_images.py         # 작품 이미지 정보 수집 → data/painter_images.json (위키미디어 예절 때문에 15분 안팎)
```
</details>

### 3) 실행

```bash
streamlit run app.py                       # Streamlit 화면 → http://localhost:8501
```

```bash
uvicorn api.server:app --port 8600         # 직접 구현한 웹 화면(F2013) → http://localhost:8600 · API 설명서 → /docs
```

API 키가 없거나 색인이 비어 있으면 화면에 안내가 표시됩니다 (F2012).

### 4) 기능별 확인 명령 (검증용)

```bash
python -m data_prep.painters                                                            # F2001 화가 수와 색인 텍스트 1건
python -m collector.query_extractor --painter "파블로 피카소" --question "게르니카는 어떤 사건을 그린 거야?"   # F2003 검색어
python -m collector.collect_docs   --painter "파블로 피카소" --question "게르니카는 어떤 사건을 그린 거야?"   # F2004 색인/버린 문서 (두 번 → 캐시)
python index_wiki.py --search "게르니카는 어떤 사건을 그린 거야?" --painter "피카소"                     # F2005 청크 검색
python -m rag.graph --show-graph                                                        # F2007 그래프 구조 (노드 2개)
python -m rag.graph --painter "클로드 모네" --question "수련 연작은 몇 점이고 어디 있어?"               # F2006~F2008 답변과 근거
python -m services.compare --a "김홍도" --b "신윤복" --focus "풍속화의 소재와 분위기"                   # F2010 화가 비교
python -m services.painter_search "19세기 프랑스 인상주의 풍경화가"                                  # F2011 조건 탐색
python -m rag.search_compare --painter "클로드 모네" --question "수련 연작은 몇 점이고 어디 있어?" --answer  # F2014 유사도 vs MMR
python -m evaluation.rag_vs_norag                                                       # F2015 RAG 전후 비교 → docs/
python -m evaluation.verify_all                                                          # 명세서 4장 자동 검증 → docs/검증_결과.md
```

---

## ✨ 기능

### 필수 기능 (F2001 ~ F2012)

| 번호 | 기능 | 화가 도메인에서 |
|---|---|---|
| F2001 | 항목 메타데이터 적재 | 화가 1명 → 이름·국적·생애·**활동 시기(세기)**·사조·장르·대표작 등을 담은 색인 텍스트 1개 |
| F2002 | 메타데이터 색인 | `painter_meta` 컬렉션, 청크 ID = 위키데이터 ID (다시 색인해도 1,343개 그대로) |
| F2003 | 검색어 추출 (프롬프트 1) | 미술사 큐레이터 역할 → 위키백과 검색어 JSON. **화가 본인 문서는 코드가 항상 포함** |
| F2004 | 외부 문서 수집 (프롬프트 2) | 후보의 제목·요약만 보고 선별 → 고른 문서만 본문 수집. 같은 질문·검색어는 다시 수집하지 않음 |
| F2005 | 외부 문서 색인 | 1,000자 / 200자 중복 청킹 → `painter_wiki`, 청크 ID = `화가ID:문서ID:순번` |
| F2006 | 근거 검색 및 병합 | 화가 메타데이터 1건(항상) + 그 화가의 문서 청크 Top-5 → 병합 · 중복 제거 |
| F2007 | 근거 기반 답변 (프롬프트 3) | LangGraph `retrieve → generate`, 미술관 도슨트 역할, 근거 밖 지식 금지 |
| F2008 | 근거 표시 | 답변의 `[근거 N]` → 실제로 인용된 근거만 출처 종류 · 제목 · 원문 링크로 |
| F2009 | 항목 선택 · 질의응답 | 1,343명 중 이름으로 찾아 선택, **화가별로 대화 기록 분리** |
| F2010 | 도메인 특화 기능 | **화가 비교** — 두 화가를 각자의 근거로 비교표 + 공통점 · 차이점 |
| F2011 | 조건 기반 항목 탐색 | "조선 시대 화가" 같은 자연어 → 조건 해석 · 정확한 필터 · LLM 검증 → 카드 → 대화로. "모네"처럼 화가 이름을 넣으면 이름 검색 |
| F2012 | 진행 상태 및 안내 | 수집 → 검색 → 답변 단계 표시, 키 누락 · 색인 없음 · 오류 안내 (면책 안내는 위험 도메인이 아니라 생략) |

### 심화 기능 (F2013 ~ F2016, 모두 선택)

| 번호 | 기능 | 적용 요약 |
|---|---|---|
| F2013 | 프론트엔드 직접 구현 | FastAPI(`api/`) + HTML·CSS·JS(`web/`). 요청·응답 형식은 `api/schemas.py` → `/docs`. 질문 응답은 단계별로 실시간 전송 |
| F2014 | MMR 검색 | 후보 20 → 5개 다양성 선택. 화면에서 유사도/MMR 선택 + 두 방식 근거 비교. 실험 결과 사실형 질문엔 유사도가 더 정확해 기본값은 유사도 |
| F2015 | RAG 전후 비교 | 같은 모델로 근거 유무만 바꿔 7문항 비교 — 필수 사실 6/14 → 14/14, 틀린 주장 4 → 0, 답이 없는 질문에 "모른다" 0/1 → 1/1 |
| F2016 | 여러 문서 출처 결합 | 한국어 위키백과 + 국립현대미술관 소장작품 설명을 함께 색인, 근거에 📖 / 🏛️ 로 구분. 미술관 설명은 작품 1점 = 청크 1개 |

### 추가 기능

| 번호 | 기능 | 내용 |
|---|---|---|
| F2017 | 작품 이미지 표시 | 화가별 대표작(위키데이터, 유명한 순 최대 40점)과 위키백과 문서 속 이미지 갤러리, 원본 페이지 링크. 저작권 보호 작품은 이미지를 가져오지 않고 **작품 더 보기** 링크(국립현대미술관 작품 페이지 · e뮤지엄 · Google Arts & Culture · 이미지 검색)로 연결. 이미지는 답변 근거로 쓰지 않음 |

---

## 🗂️ 데이터 준비와 색인 절차 요약

```text
① 화가 메타데이터     Wikidata SPARQL (화가 + 한국어 위키백과 문서) 2,054명
                      → 비화가(배우·가수 등) 거르기 → 1,343명 → data/painters.json
                      → 색인 텍스트 → painter_meta (1명 = 청크 1개)

② 위키백과 문서       질문할 때마다: 프롬프트 1 검색어 → 후보(제목+요약) → 프롬프트 2 선별
                      → 본문 수집 data/wiki_docs/ → 1,000/200자 청킹 → painter_wiki
                      (캐시 data/collect_cache.json: 같은 질문·검색어는 다시 수집 안 함)

③ 국립현대미술관      API 전체 112건 → 중복 제거 108건 → 우리 화가와 연결(이름 + 출생 연도 확인) 8명 45작품
                      → 작품 1점 = 청크 1개 → painter_wiki (source_type로 구분)

④ 작품 이미지         Wikidata 작품(작가 = 화가, 이미지 있음) + 위키백과 문서 속 이미지
                      → 주소만 data/painter_images.json (이미지 파일은 저장하지 않음)
```

조정 값(모델, 청크 크기 · 중복, Top-k, 검색 방식, 후보 수 등)은 모두 [`config.py`](config.py)에 있습니다.

---

## 📜 데이터 · 문서 출처와 이용 조건

| 데이터 | 출처 | 이용 조건 | 기록과 표시 |
|---|---|---|---|
| 화가 메타데이터 | [Wikidata](https://www.wikidata.org) | **CC0 1.0** | 화가마다 Wikidata 항목 주소 저장, 화면에 출처 표시 |
| 외부 문서 | [한국어 위키백과](https://ko.wikipedia.org) | **CC BY-SA 4.0** | 문서마다 제목 · 원문 URL · 수집일 · 관련 화가 저장, 근거마다 원문 링크 |
| 작품 설명 | [문화공공데이터광장](https://www.culture.go.kr) '국립현대미술관_소장작품' API | 문화공공데이터광장 오픈 API 이용약관 | 작품마다 국립현대미술관 상세 페이지 링크, 근거에 "국립현대미술관 소장품"으로 표시 |
| 작품 이미지 | Wikidata · [Wikimedia Commons](https://commons.wikimedia.org) · 위키백과 | 파일마다 다름 (원본 페이지에 표시) | 이미지마다 원본 파일 페이지 링크, 이미지 파일은 저장하지 않고 주소만 사용 |

위키미디어 API는 연락처를 담은 User-Agent, 요청 간격 1초, 429·5xx 재시도(최대 3회) 규칙을 지켜 호출합니다.

---

## 🧱 프로젝트 구조

```text
├── config.py               모든 조정 값 + API 키 확인 (키는 .env에서만 읽음)
├── collect_painters.py     위키데이터 → 화가 메타데이터
├── collect_images.py       작품 이미지 정보 수집
├── index_meta.py           F2002 화가 메타데이터 색인
├── index_wiki.py           F2005 위키백과 문서 청킹 · 색인
├── index_mmca.py           F2016 국립현대미술관 소장작품 수집 · 색인
├── data_prep/              [데이터 준비 · 도메인] 화가 색인 텍스트, 이미지 정보
├── collector/              [수집] 프롬프트 1·2 실행, 위키미디어 · 국립현대미술관 API
├── vectorstore/            [Vector Store · 도메인 무관] 컬렉션 생성 · 조회 · 삭제
├── rag/                    [RAG] LangGraph(retrieve → generate), 프롬프트 6개, MMR 비교
├── services/               [서비스] 화가 비교(F2010), 조건 탐색(F2011)
├── evaluation/             [평가] RAG 전후 비교(F2015), 자동 검증(verify_all)
├── app.py                  [UI] Streamlit 화면
├── api/  ·  web/           [UI] FastAPI 서버 + HTML · CSS · JS 화면 (F2013)
├── data/                   화가 메타데이터 · 수집 문서 · 캐시 · 이미지 정보
└── docs/                   요구사항 정의서 · 설계문서 · F2015 비교 문서
```

---

## ⚠️ 알려진 한계

- 큰 일반 문서(예: 6.25 전쟁)가 근거 자리를 많이 차지할 수 있음 — 문서당 청크 수 제한이 개선안
- 위키데이터 정보가 빈 화가(특히 조선 화가의 장르·사조)는 조건 탐색에서 확인할 수 없음 → 0명일 때 이유를 안내
- 현대 화가의 작품 이미지는 저작권 때문에 공개 이미지가 거의 없음
- SSAFY GMS 응답 시간이 가끔 크게 늘어남 → 제한 시간 후 재요청으로 대응

자세한 내용은 [설계문서 9 · 11장](docs/RAG_파이프라인_설계문서.md)을 참고하세요.
