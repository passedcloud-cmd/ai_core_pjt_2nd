"""
app.py — Streamlit 화면  (요구사항 F2009 항목 선택·질의응답 / F2008 근거 표시 / F2012 진행 상태·안내)
=====================================================================================================
[실행 방법]
  streamlit run app.py

화면 구성:
  왼쪽 사이드바: 색인 현황(항상 표시) · 화가 선택 · 화가 정보 · 대화 지우기
  가운데:        [💬 화가와 대화] 선택한 화가와의 채팅 (답변 아래에 실제로 인용된 근거만 표시)
                 [🔎 조건으로 화가 찾기] 자연어 조건 → 맞는 화가 카드 → 누르면 대화로 이동 (F2011)
                 [⚖️ 화가 비교] 두 화가 → 근거 기반 비교표 + 공통점·차이점 (F2010)

대화 기록은 화가별로 따로 보관한다. (st.session_state["chats"][화가ID])
  → 모네와 이야기하다 피카소로 바꿔도 대화가 섞이지 않고, 다시 모네로 오면 그대로 남아 있다.
"""

import logging

import streamlit as st

import config

st.set_page_config(page_title="화가 RAG 도슨트", page_icon="🎨", layout="wide")
logger = logging.getLogger("painter_rag")


def friendly_error(error: Exception) -> str:
    """
    오류를 화면용 안내 문구로 바꾼다. (NF2004 내부 정보 비노출)
    오류 원문에는 폴더 경로·요청 주소 같은 내부 정보가 섞일 수 있어서 화면에는 보여주지 않고,
    자세한 내용(추적 정보 포함)은 터미널 로그에만 남긴다. → 개발자는 터미널에서 원인을 볼 수 있음
    """
    logger.exception("처리 중 오류 발생")
    name = type(error).__name__
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "🔑 API 키가 올바르지 않거나 만료된 것 같아요. `.env`의 `OPENAI_API_KEY`를 확인해 주세요."
    if name == "RateLimitError":
        return "⏳ 요청이 몰려서 잠시 처리할 수 없어요. 잠시 후 다시 시도해 주세요."
    if name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout"):
        return "🌐 네트워크 연결에 문제가 있어요. 인터넷 연결을 확인하고 잠시 후 다시 시도해 주세요."
    return "⚠️ 처리 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요. (자세한 내용은 실행 중인 터미널에 기록돼요)"


# ---------------------------------------------------------------------------
# 0) 실행 전 점검: API 키 → 색인 (F2012 안내)
# ---------------------------------------------------------------------------
if not (config.OPENAI_API_KEY and config.OPENAI_BASE_URL):
    st.error(
        "🔑 **API 키 설정이 없어요.**\n\n"
        "프로젝트 폴더의 `.env` 파일에 `OPENAI_API_KEY`와 `OPENAI_BASE_URL`을 넣고 다시 실행해 주세요. "
        "(`env.example` 참고)"
    )
    st.stop()

# API 키가 확인된 뒤에 불러온다 (불러오는 순간 임베딩 객체를 만들기 때문)
from data_prep.images import painter_images, thumb, work_caption  # noqa: E402
from data_prep.painters import build_index_text, load_painters  # noqa: E402
from rag.graph import build_graph, retrieve  # noqa: E402
from rag.search_compare import diversity  # noqa: E402
from vectorstore import store as vs  # noqa: E402


@st.cache_data
def get_painters() -> list[dict]:
    """화가 목록은 바뀌지 않으니 한 번만 읽어 둔다."""
    return load_painters(config.PAINTERS_PATH)


@st.cache_resource
def get_graph():
    return build_graph()


def index_counts() -> tuple[int, int]:
    """(화가 메타데이터 청크 수, 위키 청크 수). 매번 새로 센다 → 질문 후 늘어난 위키 청크도 바로 반영."""
    return (
        vs.count(vs.get_collection(config.META_COLLECTION)),
        vs.count(vs.get_collection(config.WIKI_COLLECTION)),
    )


meta_count, wiki_count = index_counts()
if meta_count == 0:
    st.error(
        "📭 **화가 메타데이터 색인이 비어 있어요.**\n\n"
        "터미널에서 아래 명령을 먼저 실행한 뒤 이 화면을 새로고침해 주세요.\n\n"
        "```\npython index_meta.py\n```"
    )
    st.stop()

painters = get_painters()
painter_by_id = {p["id"]: p for p in painters}

if "chats" not in st.session_state:
    st.session_state["chats"] = {}  # {화가ID: [메시지, ...]}


# ---------------------------------------------------------------------------
# 1) 사이드바: 색인 현황 · 화가 선택 · 화가 정보
# ---------------------------------------------------------------------------
def painter_label(painter_id: str) -> str:
    """선택 목록에 보일 이름: '클로드 모네 · 프랑스 · 1840–1926'"""
    p = painter_by_id[painter_id]
    nation = ", ".join(p.get("nationality", [])[:1])
    years = f"{p.get('birth_year') or '?'}–{p.get('death_year') or ''}"
    return " · ".join(part for part in [p["name"], nation, years] if part)


with st.sidebar:
    st.subheader("📊 색인 현황")
    col1, col2 = st.columns(2)
    col1.metric("화가 메타데이터", f"{meta_count:,}")
    col2.metric("위키 청크", f"{wiki_count:,}")

    st.divider()
    st.subheader("🎨 화가 선택")
    # 유명한 순(rank)으로 정렬된 목록. 칸에 이름을 입력하면 검색된다.
    # key를 두면 다른 기능(F2011 조건 탐색)에서 이 값을 바꿔 화가를 고를 수 있다.
    painter_id = st.selectbox(
        "질문할 화가",
        options=[p["id"] for p in painters],
        format_func=painter_label,
        key="painter_id",
        help="이름을 입력하면 목록에서 찾을 수 있어요.",
    )
    painter = painter_by_id[painter_id]

    portrait = painter_images(painter_id)["portrait"]
    if portrait:  # 화가 초상 (클릭하면 원본 페이지)
        st.image(thumb(portrait, 500), width="stretch", link=portrait.get("page") or None)

    with st.expander("📇 화가 정보 (화가 메타데이터)", expanded=False):
        st.text(build_index_text(painter))
        if painter.get("wiki_url"):
            st.markdown(f"[한국어 위키백과 문서]({painter['wiki_url']})")

    st.divider()
    st.subheader("🔍 근거 검색 방식")
    # F2014: 유사도 검색 / MMR 검색 선택. 기본값은 config.SEARCH_TYPE (실험 결과 사실형 질문엔 유사도가 더 정확)
    SEARCH_LABELS = {"similarity": "유사도 검색 (기본)", "mmr": "MMR 검색 (다양성)"}
    search_type = st.radio(
        "근거 검색 방식",
        list(SEARCH_LABELS),
        index=list(SEARCH_LABELS).index(config.SEARCH_TYPE),
        format_func=SEARCH_LABELS.get,
        key="search_type",
        label_visibility="collapsed",
        help="유사도: 질문과 가장 비슷한 청크 순 / MMR: 비슷한 청크끼리 겹치지 않게 골고루",
    )

    if st.button("🗑️ 이 화가와의 대화 지우기", use_container_width=True):
        st.session_state["chats"][painter_id] = []
        st.rerun()

    st.caption(
        "출처: 화가 메타데이터 — Wikidata (CC0 1.0) · 외부 문서 — 한국어 위키백과 (CC BY-SA 4.0)"
    )


# ---------------------------------------------------------------------------
# 2) 근거 표시 (F2008: 답변에 실제로 인용된 근거만, 제목 + 원문 출처)
# ---------------------------------------------------------------------------
SOURCE_ICON = {"화가 메타데이터": "📇", "위키백과": "📖", config.MMCA_SOURCE_TYPE: "🏛️"}  # 출처 종류 구분 (F2016)


# ---------------------------------------------------------------------------
# 작품 이미지 (보여주기용 — 답변 근거와는 별개)
# ---------------------------------------------------------------------------
def image_grid(images: list[dict], caption_of, columns: int = 4) -> None:
    """이미지를 바둑판으로 보여준다. 누르면 원본 페이지(고해상도·출처)가 열린다."""
    cols = st.columns(columns)
    for i, img in enumerate(images):
        with cols[i % columns]:
            st.image(thumb(img, 500), caption=caption_of(img), width="stretch", link=img.get("page") or None)


def card_image_html(painter_id: str) -> str:
    """조건 탐색 카드 맨 위 이미지 (높이를 맞춰 잘라서 카드 줄이 가지런하게). 대표작 → 없으면 초상."""
    from html import escape

    imgs = painter_images(painter_id)
    img = (imgs["works"] or [None])[0] or imgs["portrait"] or (imgs["article_images"] or [None])[0]
    if not img:
        return '<div style="height:170px;border-radius:6px;background:rgba(128,128,128,.12);display:flex;align-items:center;justify-content:center;color:gray">이미지 없음</div>'
    return (
        f'<img src="{escape(thumb(img, 500))}" alt="{escape(work_caption(img))}" '
        'style="width:100%;height:170px;object-fit:cover;border-radius:6px">'
    )


def show_gallery(painter: dict, expanded: bool) -> None:
    """화가의 작품 갤러리: [작품] 탭(위키데이터, 제목·연도·소장처) / [관련 이미지] 탭(위키백과 문서 속 이미지)"""
    imgs = painter_images(painter["id"])
    works, extra = imgs["works"], imgs["article_images"]
    if not works and not extra:
        st.caption("🖼️ 이 화가의 작품 이미지를 찾지 못했어요.")
        return
    with st.expander(f"🎨 {painter['name']}의 작품 {len(works)}점 · 관련 이미지 {len(extra)}장", expanded=expanded):
        tab_works, tab_extra = st.tabs([f"작품 {len(works)}", f"관련 이미지 {len(extra)}"])
        with tab_works:
            if works:
                image_grid(works, work_caption)
            else:
                st.caption("위키데이터에 등록된 작품 이미지가 없어요. '관련 이미지' 탭을 확인해 보세요.")
        with tab_extra:
            if extra:
                st.caption("※ 위키백과 문서에 실린 이미지라 작품 말고도 화가 사진·건물 사진 등이 섞여 있을 수 있어요.")
                image_grid(extra, lambda x: x["caption"])
            else:
                st.caption("위키백과 문서에 실린 이미지가 없어요.")
        st.caption("이미지를 누르면 원본 페이지(고해상도·출처)가 열려요 · 출처: Wikidata, Wikimedia Commons, 위키백과")


def show_citations(message: dict) -> None:
    cited = message.get("cited", [])
    if cited:
        st.markdown("**📎 이 답변의 근거**")
        for e in cited:
            icon = SOURCE_ICON.get(e["source_type"], "📄")
            title = f"[{e['title']}]({e['url']})" if e["url"] else e["title"]
            whose = f" · _{e['painter_name']}의 근거_" if e.get("painter_name") else ""  # 화가 비교에서만 있음
            st.markdown(f"- `근거 {e['no']}` {icon} **{e['source_type']}** · {title}{whose}")
    else:
        st.info("ℹ️ 이 답변에는 인용된 근거가 없어요. 수집된 문서에서 확인된 내용이 없다는 뜻이에요.")

    if message.get("alt"):
        show_search_comparison(message)

    # 검증용: retrieve가 찾은 근거 전체 (인용 안 된 것 포함)
    evidence = message.get("evidence", [])
    if evidence:
        with st.expander(f"🔍 검색된 근거 전체 {len(evidence)}개 (인용 안 된 것 포함)"):
            cited_nos = {e["no"] for e in cited}
            for e in evidence:
                mark = "✅ 인용됨" if e["no"] in cited_nos else "— 미인용"
                whose = f" · {e['painter_name']}의 근거" if e.get("painter_name") else ""
                st.markdown(f"**근거 {e['no']}** · {e['source_type']} · {e['title']}{whose} · {mark}")
                st.caption(e["text"][:300] + ("…" if len(e["text"]) > 300 else ""))


def show_search_comparison(message: dict) -> None:
    """
    F2014: 같은 질문을 다른 검색 방식으로 찾았다면 근거가 어떻게 달랐는지 나란히 보여준다.
    (답변을 만든 직후에 다른 방식으로 '검색만' 한 번 더 해서 저장해 둔 것 → LLM 비용 없음)
    """
    names = {"similarity": "유사도 검색", "mmr": "MMR 검색"}
    used, alt = message["search_type"], message["alt"]["search_type"]
    lists = {used: message["evidence"], alt: message["alt"]["evidence"]}
    metrics = {used: message["metrics"], alt: message["alt"]["metrics"]}

    with st.expander(f"🔀 유사도 vs MMR 근거 비교 (이 답변은 {names[used]} 사용)"):
        cols = st.columns(2)
        for col, mode in zip(cols, ["similarity", "mmr"]):
            other_ids = {e["chunk_id"] for e in lists["mmr" if mode == "similarity" else "similarity"]}
            with col:
                st.markdown(f"**{names[mode]}**" + (" ← 답변에 사용" if mode == used else ""))
                for e in lists[mode]:
                    if e["source_type"] == "화가 메타데이터":  # 두 방식 모두 항상 들어가는 1건은 생략
                        continue
                    only = " ★" if e["chunk_id"] not in other_ids else ""  # 이 방식에만 있는 근거
                    st.markdown(f"- {e['title']} `#{e['chunk_id'].rsplit(':', 1)[-1]}`{only}")
                m = metrics[mode]
                sim = f"{m['avg_sim']:.3f}" if m["avg_sim"] is not None else "-"
                st.caption(f"문서 {m['doc_count']}개 · 한 문서 최대 {m['max_share']}개 · 근거끼리 평균 유사도 {sim}")
        st.caption("★ = 이 방식에서만 뽑힌 근거 · 평균 유사도가 낮을수록 근거가 다양해요 (화가 메타데이터 1건은 두 방식 모두 항상 포함)")


# ---------------------------------------------------------------------------
# 3) 메뉴: 화가와 대화 / 조건으로 화가 찾기 / 화가 비교
# ---------------------------------------------------------------------------
CHAT, EXPLORE, COMPARE = "💬 화가와 대화", "🔎 조건으로 화가 찾기", "⚖️ 화가 비교"
MODES = [CHAT, EXPLORE, COMPARE]
st.session_state.setdefault("mode", CHAT)
st.session_state.setdefault("mode_version", 0)

# 메뉴 버튼의 key에 '버전 번호'를 붙인다.
# 코드에서 메뉴를 바꿀 때(카드의 [대화하기]) 버전을 올리면 버튼이 새로 만들어져서
# 화면에 남아 있던 옛 선택 표시가 사라진다. (key를 그대로 두면 표시가 안 바뀌는 문제가 있었음)
mode_key = f"mode_radio_{st.session_state['mode_version']}"


def on_mode_change() -> None:
    st.session_state["mode"] = st.session_state[mode_key]


st.radio(
    "메뉴",
    MODES,
    index=MODES.index(st.session_state["mode"]),
    key=mode_key,
    on_change=on_mode_change,
    horizontal=True,
    label_visibility="collapsed",
)


def go_chat(target_id: str) -> None:
    """카드의 [대화하기]를 누르면: 그 화가를 선택하고 대화 화면으로 이동 (F2011 → F2009)"""
    st.session_state["painter_id"] = target_id
    st.session_state["mode"] = CHAT
    st.session_state["mode_version"] += 1


def render_explore() -> None:
    """조건 기반 화가 탐색 화면 (F2011)"""
    from services.painter_search import describe_condition, search_painters

    st.title("🔎 조건으로 화가 찾기")
    st.caption("국적·시대·사조·장르 같은 조건을 자연어로 적어 주세요. 조건에 맞는 화가만 골라서 보여드려요.")

    with st.form("explore_form"):
        condition = st.text_input(
            "찾고 싶은 화가 조건",
            placeholder="예: 19세기 프랑스 인상주의 풍경화가 / 조선 시대 화가 / 빛을 잘 표현한 네덜란드 화가",
        )
        submitted = st.form_submit_button("화가 찾기", type="primary")

    if submitted and condition.strip():
        with st.status("🔎 조건을 해석하고 화가를 찾는 중…", expanded=False) as status:
            try:
                st.session_state["explore"] = {"query": condition.strip(), **search_painters(condition.strip())}
                status.update(label="✅ 찾기 완료", state="complete")
            except Exception as error:
                status.update(label="❌ 처리 중 오류가 발생했어요", state="error", expanded=True)
                st.error(friendly_error(error))
                return

    # 버튼을 눌러 화면이 다시 그려져도 결과가 남아 있도록 session_state에 보관
    out = st.session_state.get("explore")
    if not out:
        return

    st.markdown(f"**조건:** {out['query']}")
    st.markdown(
        f"① 해석 → {describe_condition(out['condition'])}  \n"
        f"② 조건 필터 통과 **{out['filtered']}명** → 후보 **{out['candidates']}명**을 LLM이 하나씩 검증  \n"
        f"③ 조건에 맞는 화가 **{len(out['results'])}명** (유명한 순)"
    )

    if not out["results"]:
        st.warning("조건에 맞는 화가를 찾지 못했어요.")
        for reason in out.get("empty_reasons", []):
            st.info(f"ℹ️ {reason} 조건을 조금 넓혀서 다시 찾아보세요.")
        return

    columns = st.columns(3)
    for i, (found, reason) in enumerate(out["results"]):
        with columns[i % 3].container(border=True):
            st.markdown(card_image_html(found["id"]), unsafe_allow_html=True)
            st.markdown(f"**{found['name']}**")
            st.caption(f"{found.get('description') or '화가'} · 유명도 {found.get('rank')}위")
            st.markdown(f"✅ {reason}")
            st.button(
                "💬 대화하기",
                key=f"chat_{found['id']}",
                on_click=go_chat,
                args=(found["id"],),
                use_container_width=True,
            )

    st.caption("※ 결과는 화가 메타데이터(Wikidata)에 적힌 정보로만 판단해요. 정보가 비어 있는 화가는 조건 확인이 안 돼서 빠질 수 있어요.")


def render_compare() -> None:
    """화가 비교 화면 (F2010 도메인 특화 기능)"""
    from services.compare import compare

    st.title("⚖️ 화가 비교")
    st.caption("두 화가를 고르면, 수집된 문서만 근거로 비교표와 공통점·차이점을 만들어 드려요. 칸마다 근거 번호가 붙어요.")

    ids = [p["id"] for p in painters]
    with st.form("compare_form"):
        col_a, col_b = st.columns(2)
        # 화가 A는 사이드바에서 고른 화가로 시작
        a_id = col_a.selectbox("화가 A", ids, index=ids.index(painter_id), format_func=painter_label)
        b_id = col_b.selectbox("화가 B", ids, index=1 if ids[0] == painter_id else 0, format_func=painter_label)
        focus = st.text_input(
            "비교 관점 (비워 두면 기본 관점)",
            placeholder=f"기본: {config.COMPARE_DEFAULT_FOCUS}  /  예: 풍속화의 소재와 분위기, 빛과 색채 표현",
        )
        submitted = st.form_submit_button("비교하기", type="primary")

    if submitted:
        if a_id == b_id:
            st.warning("서로 다른 두 화가를 골라 주세요.")
            return
        with st.status("📚 근거 문서를 확인하는 중…", expanded=True) as status:
            try:
                def progress(text: str) -> None:  # 화가별 수집 → 비교표 작성 단계를 화면에 표시 (F2012)
                    st.write(text)
                    status.update(label=text)

                result = compare(painter_by_id[a_id], painter_by_id[b_id], focus, on_progress=progress)
                for c in result["collected"]:
                    titles = ", ".join(d["title"] for d in c.get("kept", [])) or "없음"
                    st.write(f"📖 {c['painter']['name']} 근거 문서: {titles}")
                st.session_state["compare"] = {"a": a_id, "b": b_id, **result}
                status.update(label="✅ 비교 완료", state="complete", expanded=False)
            except Exception as error:
                status.update(label="❌ 처리 중 오류가 발생했어요", state="error", expanded=True)
                st.error(friendly_error(error))
                return

    out = st.session_state.get("compare")
    if not out:
        return
    st.subheader(f"{painter_by_id[out['a']]['name']} vs {painter_by_id[out['b']]['name']}")
    st.caption(f"비교 관점: {out['focus']}")
    for col, pid in zip(st.columns(2), [out["a"], out["b"]]):
        with col.container(border=True):
            imgs = painter_images(pid)
            st.markdown(f"**{painter_by_id[pid]['name']}**")
            # 위키데이터 작품이 4개보다 적으면(한국 화가 등) 위키백과 문서 속 이미지로 채운다
            shown = (imgs["works"] + imgs["article_images"])[:4]
            if shown:
                image_grid(shown, lambda x: x.get("title") or x.get("caption"), columns=2)
            else:
                st.caption("작품 이미지를 찾지 못했어요.")
    st.markdown(out["answer"])
    show_citations(out)


if st.session_state["mode"] == EXPLORE:
    render_explore()
    st.stop()
if st.session_state["mode"] == COMPARE:
    render_compare()
    st.stop()


# ---------------------------------------------------------------------------
# 4) 메인: 화가별 채팅 (F2009)
# ---------------------------------------------------------------------------
st.title(f"🎨 {painter['name']}에게 물어보세요")
st.caption("답변은 수집된 위키백과 문서와 화가 메타데이터만 근거로 해요. 문서에 없는 내용은 '확인되지 않는다'고 답해요.")

history = st.session_state["chats"].setdefault(painter_id, [])
show_gallery(painter, expanded=not history)  # 대화 전에는 펼쳐 두고, 대화가 시작되면 접어 둔다

if not history:
    st.markdown(
        "예시 질문: *대표작은 뭐야?* · *어떤 사조에 속해?* · *말년에는 어디서 살았어?*  \n"
        "처음 하는 질문은 위키백과에서 문서를 모으느라 10~20초 정도 걸릴 수 있어요."
    )

for message in history:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            show_citations(message)

question = st.chat_input(f"{painter['name']}에 대해 궁금한 점을 입력하세요")

if question:
    history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        final: dict = {}
        # 진행 상태 표시 (F2012): 그래프를 노드 단위로 실행하면서 단계마다 안내를 갱신
        with st.status("📚 근거 문서를 확인하는 중… (처음 하는 질문이면 위키백과에서 수집해요)", expanded=True) as status:
            try:
                inputs = {"painter": painter, "question": question, "collect": True, "search_type": search_type}
                for update in get_graph().stream(inputs, stream_mode="updates"):
                    for node, output in update.items():
                        final.update(output)
                        if node == "retrieve":
                            collected = output.get("collect_result", {})
                            titles = [d["title"] for d in collected.get("kept", [])]
                            if collected.get("cached"):
                                st.write(f"♻️ 이미 수집한 검색어 재사용: {', '.join(collected['cached'])}")
                            st.write(f"🔑 검색어: {', '.join(collected.get('queries', []))}")
                            st.write(f"📖 근거 문서: {', '.join(titles) if titles else '없음'}")
                            st.write(f"🔍 근거 {len(output.get('evidence', []))}개 검색 완료")
                            status.update(label="✍️ 근거를 읽고 답변을 쓰는 중…")
                status.update(label="✅ 답변 완료", state="complete", expanded=False)
            except Exception as error:  # 네트워크·API 오류 등 (F2012 오류 안내)
                status.update(label="❌ 처리 중 오류가 발생했어요", state="error", expanded=True)
                st.error(friendly_error(error))

        if "answer" in final:
            message = {
                "role": "assistant",
                "content": final["answer"],
                "cited": final.get("cited", []),
                "evidence": final.get("evidence", []),
                "search_type": search_type,
                "metrics": diversity(final.get("evidence", [])),
            }
            # F2014: 다른 검색 방식이었다면 근거가 어땠는지 (검색만, 수집·LLM 호출 없음)
            other = "mmr" if search_type == "similarity" else "similarity"
            try:
                alt = retrieve({"painter": painter, "question": question, "collect": False, "search_type": other})
                message["alt"] = {"search_type": other, "evidence": alt["evidence"], "metrics": diversity(alt["evidence"])}
            except Exception:  # 비교는 보조 기능이라 실패해도 답변은 그대로 보여줌
                logger.exception("검색 방식 비교 실패")
            st.markdown(message["content"])
            show_citations(message)
            history.append(message)

    # 새로 수집·색인된 위키 청크 수가 사이드바에 반영되도록 화면을 다시 그린다
    # (오류가 났을 때는 다시 그리지 않음 → 오류 안내가 사라지지 않게)
    if "answer" in final:
        st.rerun()
