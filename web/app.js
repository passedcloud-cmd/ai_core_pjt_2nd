/* =========================================================================
   화가 도슨트 화면 (F2013: HTML·CSS·JavaScript로 직접 구현한 화면)
   서버(api/server.py)와는 JSON으로만 주고받는다. 주고받는 모양은 api/schemas.py 참고.

   화면 3개:  #chat 화가와 대화 / #explore 조건으로 찾기 / #compare 화가 비교
   대화 기록은 화가별로 따로 보관한다 (F2009). 브라우저 탭을 닫기 전까지 유지.
   ========================================================================= */

const $ = (id) => document.getElementById(id);
const state = {
  painters: [], byId: {}, current: null, detail: null,
  histories: loadHistories(),   // { 화가ID: [ {q, status, progress, answer, cited, evidence, error, mode} ] }
  mode: "similarity",            // 근거 검색 방식 (F2014)
  galleryTab: "works", galleryOpen: false,
};
const SOURCE_ICON = { "화가 메타데이터": "📇", "위키백과": "📖", "국립현대미술관 소장품": "🏛️" };
const MODE_NAME = { similarity: "유사도", mmr: "MMR" };

/* ------------------------------ 도우미 ------------------------------ */
// LLM이 만든 글을 화면에 넣을 때는 반드시 글자로만 (HTML로 해석되지 않게)
function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// '[근거 2]', '[근거 1, 3]' → 골드 번호 배지 (F2008)
function citeBadges(html) {
  return html.replace(/\[근거\s*([\d\s,]+)\]/g, (_, nums) =>
    nums.split(/[\s,]+/).filter(Boolean).map((n) => `<sup class="cite">${n}</sup>`).join(""));
}
// 받침에 따라 조사 고르기: '모네와' / '신사임당과'. '정선 (화가)'처럼 괄호 설명은 빼고 마지막 글자로 판단
function josa(name, withoutBatchim, withBatchim) {
  const base = name.replace(/\s*\(.*\)\s*$/, "");
  const code = base.charCodeAt(base.length - 1) - 0xac00;
  const hasBatchim = code >= 0 && code <= 11171 && code % 28 !== 0;
  return base + (hasBatchim ? withBatchim : withoutBatchim);
}
function renderAnswer(text) {
  return citeBadges(esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"));
}
async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!res.ok) {
    let msg = "처리 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.";
    try { const j = await res.json(); msg = j.error || (typeof j.detail === "string" ? j.detail : msg); } catch {}
    throw new Error(msg);
  }
  return res;
}
function loadHistories() { try { return JSON.parse(sessionStorage.getItem("histories") || "{}"); } catch { return {}; } }
function saveHistories() { try { sessionStorage.setItem("histories", JSON.stringify(state.histories)); } catch {} }
function showNotice(msg) { $("notice").textContent = msg; $("notice").hidden = !msg; }

/* ------------------------------ 화면 전환 ------------------------------ */
function route() {
  const view = (location.hash || "#chat").slice(1).split("-")[0];
  const name = ["chat", "explore", "compare"].includes(view) ? view : "chat";
  for (const v of ["chat", "explore", "compare"]) $(`view-${v}`).hidden = v !== name;
  document.querySelectorAll("header nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === name));
  if (location.hash !== "#ask-box") window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

/* ------------------------------ 근거 검색 방식 (F2014) ------------------------------ */
$("mode-btn").addEventListener("click", () => { $("mode-pop").hidden = !$("mode-pop").hidden; });
document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => {
  state.mode = r.value;
  $("mode-btn").textContent = `근거 검색 · ${MODE_NAME[r.value]}`;
  $("mode-pop").hidden = true;
}));

/* ------------------------------ 화가 선택 창 ------------------------------ */
function openPicker() { $("picker").hidden = false; $("picker-input").value = ""; renderPicker(""); $("picker-input").focus(); }
function renderPicker(q) {
  const key = q.trim().toLowerCase();
  const found = state.painters.filter((p) => !key || p.name.toLowerCase().includes(key) || p.name_en.toLowerCase().includes(key));
  $("picker-list").innerHTML = found.slice(0, 60).map((p) => `
    <button type="button" data-id="${esc(p.id)}">
      ${p.thumb ? `<img src="${esc(p.thumb)}" alt="" loading="lazy">` : `<span class="ph"></span>`}
      <span><b>${esc(p.name)}</b><small>${esc(p.label)}${p.name_en ? " · " + esc(p.name_en) : ""}</small></span>
    </button>`).join("") + (found.length > 60 ? `<div class="more">${found.length}명 중 60명만 보여요. 이름을 더 입력해 보세요.</div>` : "")
    + (found.length === 0 ? `<div class="more">찾는 화가가 없어요.</div>` : "");
}
$("open-picker").addEventListener("click", openPicker);
$("picker-input").addEventListener("input", (e) => renderPicker(e.target.value));
$("picker-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-id]");
  if (btn) { $("picker").hidden = true; selectPainter(btn.dataset.id); location.hash = "#chat"; }
});
document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => { b.closest(".overlay").hidden = true; }));
document.querySelectorAll(".overlay").forEach((o) => o.addEventListener("click", (e) => { if (e.target === o) o.hidden = true; }));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll(".overlay").forEach((o) => (o.hidden = true)); });

/* ------------------------------ 크게 보기 ------------------------------ */
function openImage(img) {
  $("lb-img").src = img.large; $("lb-cap").textContent = [img.title, img.sub].filter(Boolean).join(" · ");
  $("lb-page").href = img.page || "#"; $("lb-page").hidden = !img.page; $("lightbox").hidden = false;
}
function figure(img, cls = "") {
  return `<figure class="${cls}" data-img='${esc(JSON.stringify(img))}'>
    <img src="${esc(img.thumb)}" alt="${esc(img.title)}" loading="lazy">
    <figcaption>${esc(img.title)}${img.sub ? `<span>${esc(img.sub)}</span>` : ""}</figcaption></figure>`;
}
document.addEventListener("click", (e) => {
  const f = e.target.closest("figure[data-img]");
  if (f) openImage(JSON.parse(f.dataset.img));
});

/* ------------------------------ ① 화가와 대화 ------------------------------ */
async function selectPainter(id) {
  state.current = id;
  try { localStorage.setItem("painter", id); } catch {}
  state.galleryTab = "works"; state.galleryOpen = false;
  const d = await (await api(`/api/painters/${encodeURIComponent(id)}`)).json();
  state.detail = d;
  renderPainter();
  renderMessages();
}

function renderPainter() {
  const d = state.detail;
  const fact = (k) => (d.facts.find((f) => f.label === k) || {}).value || "";
  // 첫 화면: 대표작을 화면 가득 (없으면 네이비 바탕만)
  $("hero-img").hidden = !d.hero;
  if (d.hero) { $("hero-img").src = d.hero.large; $("hero-img").alt = d.hero.title; }
  $("hero-cap").innerHTML = d.hero ? `${esc(d.hero.title)}${d.hero.sub ? "<br>" + esc(d.hero.sub) : ""}` : "";
  $("hero-label").textContent = [fact("국적").split(",")[0], fact("사조").split(",")[0], fact("생애")].filter(Boolean).join(" · ");
  $("hero-title").innerHTML = esc(d.name) + (d.name_en ? `<span class="en"><em class="l">${esc(d.name_en)}</em></span>` : "");
  $("hero-count").textContent = `작품 ${d.works.length}점 · 관련 이미지 ${d.related.length}장`;
  $("hero-index").textContent = `유명도 ${d.rank}위`;

  $("intro-title").innerHTML = `${esc(d.name)}의<br><em class="k">작품과 생애</em>`;
  $("intro-desc").textContent = d.description || "위키데이터에 등록된 짧은 소개가 없어요. 아래 정보와 질문으로 알아봐요.";
  $("facts").innerHTML = d.facts.map((f) => `<div><span>${esc(f.label)}</span><span>${esc(f.value)}</span></div>`).join("");
  $("wiki-link").href = d.wiki_url; $("wiki-link").hidden = !d.wiki_url;
  $("chat-title").innerHTML = `${esc(d.name)}에게 <em class="k">묻다</em>`;
  $("ask-input").placeholder = `${d.name}에 대해 궁금한 점을 입력하세요`;
  document.title = `${d.name} — 화가 도슨트`;
  renderGallery();
}

function renderGallery() {
  const d = state.detail;
  const tabs = [["works", `작품 ${d.works.length}`], ["related", `관련 이미지 ${d.related.length}`]];
  $("gallery-tabs").innerHTML = tabs.map(([k, t]) => `<button type="button" class="label ${state.galleryTab === k ? "on" : ""}" data-tab="${k}">${t}</button>`).join("");
  const list = d[state.galleryTab];
  if (!list.length) {
    $("gallery").innerHTML = `<div class="empty" style="grid-column:1/-1">${state.galleryTab === "works"
      ? "위키데이터에 등록된 작품 이미지가 없어요.<br>최근 화가는 저작권 때문에 공개된 이미지가 거의 없어요. '관련 이미지' 탭도 확인해 보세요."
      : "위키백과 문서에 실린 이미지가 없어요."}</div>`;
    $("gallery-more").hidden = true;
    return;
  }
  const shown = state.galleryOpen ? list : list.slice(0, 5);
  $("gallery").innerHTML = shown.map((img, i) => figure(img, i === 0 ? "tall" : "")).join("");
  $("gallery-more").hidden = list.length <= 5;
  $("gallery-more").textContent = state.galleryOpen ? "접기 ↑" : `${list.length}장 모두 보기 ↓`;
}
$("gallery-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) { state.galleryTab = b.dataset.tab; state.galleryOpen = false; renderGallery(); }
});
$("gallery-more").addEventListener("click", () => { state.galleryOpen = !state.galleryOpen; renderGallery(); });

function sourcesHtml(cited, evidence, light = false) {
  const items = cited.length ? cited.map((e) => `
      <div class="source"><b>${e.no}</b><div>
        <div class="t">${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}</div>
        <div class="k">${SOURCE_ICON[e.source_type] || "📄"} ${esc(e.source_type)}${e.painter_name ? `<span class="who">${esc(e.painter_name)}의 근거</span>` : ""}</div>
      </div></div>`).join("")
    : `<div class="no-cite">ℹ️ 이 답변에는 인용된 근거가 없어요. 수집된 문서에서 확인된 내용이 없다는 뜻이에요.</div>`;
  const citedNos = new Set(cited.map((e) => e.no));
  const all = evidence.length ? `<details class="all"><summary>검색된 근거 전체 ${evidence.length}개 (인용 안 된 것 포함)</summary><ul>${
    evidence.map((e) => `<li>근거 ${e.no} · ${esc(e.source_type)} · ${esc(e.title)}${e.painter_name ? ` · ${esc(e.painter_name)}의 근거` : ""} · ${citedNos.has(e.no) ? "✅ 인용됨" : "— 미인용"}
      <span>${esc(e.text.slice(0, 180))}${e.text.length > 180 ? "…" : ""}</span></li>`).join("")}</ul></details>` : "";
  return `<div class="sources"><div class="label">이 답변의 근거</div>${items}${all}</div>`;
}

function turnHtml(t) {
  let left;
  if (t.status === "pending") {
    left = `<div class="progress">${t.progress.map((p, i) =>
      `<div>${i === t.progress.length - 1 ? '<span class="spin"></span>' : "✓ "}${esc(p.text)}${p.detail ? `<small>${esc(p.detail)}</small>` : ""}</div>`).join("")}</div>`;
  } else if (t.status === "error") {
    left = `<p class="error">⚠️ ${esc(t.error)}</p>`;
  } else {
    left = `<p>${renderAnswer(t.answer)}</p>`;
  }
  const right = t.status === "done" ? sourcesHtml(t.cited, t.evidence) : "";
  return `<div class="turn"><div class="bubble-q">“${esc(t.q)}”</div>
    <div class="answer"><div>${left}${t.status === "done" ? `<p class="fine dim">근거 검색 · ${MODE_NAME[t.mode] || t.mode}</p>` : ""}</div><div>${right}</div></div></div>`;
}
function renderMessages() {
  const list = state.histories[state.current] || [];
  $("messages").innerHTML = list.map(turnHtml).join("");
  $("examples").hidden = list.length > 0;
}

$("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("ask-input").value.trim();
  if (!q || !state.current) return;
  const pid = state.current;
  const turn = { q, status: "pending", progress: [{ text: "질문을 보내는 중이에요…" }], answer: "", cited: [], evidence: [], mode: state.mode };
  (state.histories[pid] ||= []).push(turn);
  $("ask-input").value = ""; $("ask-input").disabled = true; e.submitter && (e.submitter.disabled = true);
  renderMessages();
  $("messages").lastElementChild?.scrollIntoView({ behavior: "smooth", block: "center" });

  try {
    const res = await api("/api/ask", { painter_id: pid, question: q, search_type: state.mode });
    // 서버가 단계마다 한 줄씩 보내는 결과를 받는 대로 화면에 반영 (F2012 진행 상태)
    const reader = res.body.getReader(); const decoder = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "status") turn.progress = [{ text: ev.message }];
        if (ev.type === "retrieved") {
          turn.progress[0].detail = `${ev.cached.length ? "이미 수집한 검색어 재사용 · " : ""}근거 문서: ${ev.documents.join(", ") || "없음"}`;
          turn.progress.push({ text: ev.message, detail: `근거 ${ev.evidence_count}개 검색 완료` });
        }
        if (ev.type === "answer") Object.assign(turn, { status: "done", answer: ev.answer, cited: ev.cited, evidence: ev.evidence });
        if (ev.type === "error") Object.assign(turn, { status: "error", error: ev.message });
        if (state.current === pid) renderMessages();
      }
    }
    if (turn.status === "pending") Object.assign(turn, { status: "error", error: "답변을 끝까지 받지 못했어요. 다시 시도해 주세요." });
  } catch (err) {
    Object.assign(turn, { status: "error", error: err.message });
  }
  saveHistories();
  $("ask-input").disabled = false; e.submitter && (e.submitter.disabled = false);
  if (state.current === pid) { renderMessages(); $("ask-input").focus(); }
  refreshStatus();
});

/* ------------------------------ ② 조건으로 찾기 (F2011) ------------------------------ */
$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const condition = $("search-input").value.trim();
  if (!condition) return;
  $("parsed").hidden = true;
  $("results").innerHTML = `<div class="status"><span class="spin"></span>조건을 해석하고 화가를 찾는 중이에요… (10초 정도 걸려요)</div>`;
  try {
    const out = await (await api("/api/search", { condition })).json();
    $("parsed").hidden = false;
    $("parsed").innerHTML = `
      <div class="conds"><span class="label muted">해석된 조건</span>${out.parsed.map((f) => `<span class="cond"><i>${esc(f.label)}</i>${esc(f.value)}</span>`).join("") || '<span class="cond">필터 조건 없음 · 의미 검색만</span>'}</div>
      <div class="steps"><span><b>${out.filtered}</b>명 조건 필터 통과</span><span>→</span><span><b>${out.candidates}</b>명 LLM 검증</span><span>→</span><span class="last"><b>${out.results.length}</b>명 일치</span></div>`;
    if (!out.results.length) {
      $("results").innerHTML = `<div class="empty">조건에 맞는 화가를 찾지 못했어요.${out.empty_reasons.map((r) => `<br>ℹ️ ${esc(r)}`).join("")}<br>조건을 조금 넓혀서 다시 찾아보세요.</div>`;
      return;
    }
    $("results").innerHTML = out.results.map((r, i) => `
      <div class="card">
        ${r.image ? `<img src="${esc(r.image.thumb)}" alt="${esc(r.image.title)}" loading="lazy">` : `<div class="ph"></div>`}
        <div class="top"><h3>${esc(r.painter.name)}</h3><span class="no">${String(i + 1).padStart(2, "0")}</span></div>
        <div class="desc">${esc(r.painter.label)} · 유명도 ${r.painter.rank}위</div>
        ${r.image ? `<div class="work">${esc([r.image.title, r.image.sub].filter(Boolean).join(" · "))}</div>` : ""}
        <div class="why">${esc(r.reason)}</div>
        <button type="button" class="label" data-chat="${esc(r.painter.id)}">${esc(josa(r.painter.name, "와", "과"))} 대화하기 ↗</button>
      </div>`).join("");
  } catch (err) {
    $("results").innerHTML = `<div class="empty">⚠️ ${esc(err.message)}</div>`;
  }
});
$("results").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-chat]");
  if (b) { selectPainter(b.dataset.chat); location.hash = "#chat"; }
});

/* ------------------------------ ③ 화가 비교 (F2010) ------------------------------ */
const nameToId = {};
function fillPainterNames() {
  $("painter-names").innerHTML = state.painters.map((p) => {
    const key = nameToId[p.name] ? `${p.name} (${p.label})` : p.name;   // 동명이인은 설명을 붙여 구분
    nameToId[key] = p.id;
    return `<option value="${esc(key)}">${esc(p.label)}</option>`;
  }).join("");
}
$("compare-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const a = nameToId[$("cmp-a").value.trim()], b = nameToId[$("cmp-b").value.trim()];
  const out = $("compare-out");
  if (!a || !b) { out.innerHTML = `<div class="cmp-body"><div class="empty">목록에 있는 화가 이름을 골라 주세요.</div></div>`; return; }
  if (a === b) { out.innerHTML = `<div class="cmp-body"><div class="empty">서로 다른 두 화가를 골라 주세요.</div></div>`; return; }
  out.innerHTML = `<div class="cmp-body"><div class="status"><span class="spin"></span>두 화가의 근거 문서를 모으고 비교표를 만드는 중이에요… (처음이면 20~30초)</div></div>`;
  try {
    const r = await (await api("/api/compare", { a, b, focus: $("cmp-focus").value.trim() })).json();
    const side = (cp, tag) => `<div class="side"><div class="label muted">Painter ${tag}</div><h2>${esc(cp.painter.name)}</h2>
      <div class="sub">${esc(cp.painter.label)}</div>
      ${cp.images.length ? `<div class="imgs">${cp.images.map((img) => figure(img)).join("")}</div>` : `<div class="empty">공개된 작품 이미지가 없어요.</div>`}</div>`;
    const md = DOMPurify.sanitize(marked.parse(r.answer));   // LLM이 쓴 마크다운 → 안전한 HTML
    out.innerHTML = `<section class="duo">${side(r.painters[0], "A")}${side(r.painters[1], "B")}</section>
      <section class="cmp-body"><div class="md"><p class="label muted" style="margin-bottom:18px">비교 관점 · ${esc(r.focus)}</p>${citeBadges(md)}</div>
      ${sourcesHtml(r.cited, r.evidence)}</section>`;
    refreshStatus();
  } catch (err) {
    out.innerHTML = `<div class="cmp-body"><div class="empty">⚠️ ${esc(err.message)}</div></div>`;
  }
});

/* ------------------------------ 시작 ------------------------------ */
async function refreshStatus() {
  try {
    const s = await (await api("/api/status")).json();
    showNotice(s.message);   // 색인 없음·키 누락 안내 (F2012)
  } catch (err) { showNotice("서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요."); }
}

(async function start() {
  route();
  await refreshStatus();
  try {
    state.painters = await (await api("/api/painters")).json();
    state.painters.forEach((p) => (state.byId[p.id] = p));
    fillPainterNames();
    let first = null;
    try { first = localStorage.getItem("painter"); } catch {}
    if (!state.byId[first]) first = state.byId.Q296 ? "Q296" : state.painters[0]?.id;   // 처음엔 모네
    await selectPainter(first);
    $("cmp-a").value = state.byId[first]?.name || "";
  } catch (err) {
    showNotice(err.message);
  }
})();
