/* =========================================================================
   tour.js — 처음 온 사람을 위한 단계별 사용 안내 (튜토리얼)
   -------------------------------------------------------------------------
   설명할 버튼만 밝게 남기고 나머지 화면은 어둡게 가린 뒤, 화살표 말풍선으로 설명한다.

   어둡게 가리는 방법: 강조할 칸(구멍)의 위·아래·왼쪽·오른쪽을 어두운 판 4장으로 덮는다.
     → 구멍 안의 버튼만 누를 수 있고, 다른 곳은 눌리지 않아서 안내 도중 길을 잃지 않는다.

   단계가 넘어가는 방법 (advance)
     "next"  : 말풍선의 [다음] 버튼
     "click" : 사용자가 강조된 버튼을 직접 누르면
     "event" : 앱에서 신호가 오면 (화가 선택 완료, 질문 보냄, 답변 완료 — app.js의 signal())

   처음 접속한 사람에게만 자동으로 시작하고(브라우저에 '봤음' 기록), 상단 [?] 버튼으로 언제든 다시 볼 수 있다.
   ========================================================================= */

(function () {
  const DONE_KEY = "tour_done_v1";
  const EXAMPLE_Q = { Q296: "수련 연작은 몇 점이고 어디 있어?" };   // 이미 수집해 둔 질문이라 빨리 답함 (모네)
  const $ = (sel) => document.querySelector(sel);
  const lastTurn = () => [...document.querySelectorAll("#messages .turn")].pop() || null;
  // app.js의 state는 const라 window.state로는 안 보인다 → 그냥 state로 접근 (없을 때 대비 typeof 확인)
  const appState = () => (typeof state !== "undefined" ? state : null);
  const currentName = () => (appState() && appState().detail ? appState().detail.name : "화가");

  /* ------------------------------ 안내 단계 ------------------------------ */
  const STEPS = [
    {
      title: "화가 도슨트에 오신 걸 환영해요",
      body: "화가를 골라 질문하면, <b>수집한 문서만 근거로</b> 답하고 그 근거를 함께 보여주는 서비스예요.<br>1분이면 둘러볼 수 있어요.",
      advance: "next", nextLabel: "둘러보기 시작",
      before: () => { if (location.hash !== "#chat" && location.hash !== "") location.hash = "#chat"; },
    },
    {
      target: () => $("#open-picker"), place: "bottom",
      title: "먼저 대화할 화가를 골라요",
      body: "1,343명의 화가 중에서 고를 수 있어요.<br><b>👆 이 버튼을 눌러 보세요.</b>",
      advance: "click",
    },
    {
      target: () => $("#picker .picker"), place: "right",
      before: () => waitFor(() => !$("#picker").hidden),
      title: "이름으로 찾아요",
      body: "한글이나 영어 이름을 입력하면 바로 걸러져요.<br><b>👆 '모네'를 입력하고 클로드 모네를 골라 보세요.</b><br><small>(다른 화가를 골라도 괜찮아요)</small>",
      advance: "event", event: "tour:painter-selected",
      // 화가를 고르지 않고 창만 닫으면 이전 단계로. 단, 목록에서 화가를 누른 경우엔 창이 먼저 닫히고
      // 화가 정보는 조금 뒤에 오므로 되돌아가지 않고 기다린다 (처음엔 이 순서 때문에 2단계로 되돌아가는 문제가 있었음)
      backIf: () => $("#picker").hidden && !picking,
      watchClick: "#picker-list",
    },
    {
      target: () => $(".hero .copy"), place: "top",
      before: () => window.scrollTo({ top: 0, behavior: "instant" }), noScroll: true,   // 맨 위로만 (가운데 맞추기와 겹치면 중간에 멈춤)
      title: () => `${currentName()}의 첫 화면`,
      body: "화면 가득 깔린 그림은 이 화가의 <b>대표작</b>이에요.<br>국적·사조·생애와 작품 수를 한눈에 볼 수 있어요.",
      advance: "next",
    },
    {
      target: () => $("#gallery-tabs") && $("#gallery-tabs").parentElement, place: "left",
      title: "작품 갤러리",
      body: "<b>작품</b> 탭은 위키데이터의 작품, <b>관련 이미지</b> 탭은 위키백과 문서에 실린 이미지예요. 이미지를 누르면 크게 볼 수 있어요.<br>저작권 때문에 이미지가 없는 화가는 <b>작품 더 보기</b>에서 미술관 페이지로 갈 수 있어요.",
      advance: "next",
    },
    {
      target: () => $("#ask-form"), place: "top",
      title: "이제 질문해 볼까요?",
      body: () => `궁금한 걸 자유롭게 물어보세요.<br><b>👆 질문을 입력하고 Enter를 누르세요.</b>`,
      extra: { label: "예시 질문 넣기", run: () => {
        const input = $("#ask-input");
        input.value = EXAMPLE_Q[appState() && appState().current] || `${currentName()}의 대표작은 뭐야?`;
        input.focus();
      } },
      advance: "event", event: "tour:asked",
    },
    {
      target: lastTurn, place: "top",
      title: "근거를 모으고 있어요",
      body: "처음 하는 질문이면 위키백과에서 문서를 찾아 모으고, 그 문서에서 근거를 골라 답을 써요.<br><span class=\"tour-wait\">답변을 기다리는 중…</span>",
      advance: "event", event: "tour:answered", hidePrev: true,
    },
    {
      target: () => lastTurn() && lastTurn().querySelector(".answer > div:first-child"), place: "top",
      title: "답변 속 번호를 보세요",
      body: "문장 끝의 <span class=\"tour-cite\">2</span> 같은 번호가 <b>그 내용이 나온 근거</b>예요.<br>문서에서 확인되지 않는 내용은 지어내지 않고 <b>\"확인되지 않습니다\"</b>라고 답해요.",
      advance: "next",
      skipIf: () => !lastTurn() || !lastTurn().querySelector(".answer p:not(.error)"),
    },
    {
      target: () => lastTurn() && lastTurn().querySelector(".sources"), place: "left",
      title: "이 답변의 근거",
      body: "실제로 인용된 근거만 보여줘요. 📇 화가 메타데이터 · 📖 위키백과 · 🏛️ 국립현대미술관처럼 <b>출처 종류</b>가 붙고, 제목을 누르면 <b>원문</b>이 열려요.",
      advance: "next",
      skipIf: () => !lastTurn() || !lastTurn().querySelector(".sources"),
    },
    {
      target: () => $("header.top nav"), place: "bottom",
      before: () => window.scrollTo({ top: 0, behavior: "instant" }), noScroll: true,   // 맨 위로만 (가운데 맞추기와 겹치면 중간에 멈춤)
      title: "다른 기능도 있어요",
      body: "<b>조건으로 찾기</b> — \"조선 시대 화가\"처럼 조건이나 이름으로 화가 찾기<br><b>화가 비교</b> — 두 화가를 근거와 함께 비교표로<br><b>근거 검색</b> — 유사도 / MMR 검색 방식 바꾸기<br><br>이 안내는 오른쪽 위 <b>?</b> 버튼으로 언제든 다시 볼 수 있어요.",
      advance: "next", nextLabel: "끝내기",
    },
  ];

  /* ------------------------------ 화면 부품 ------------------------------ */
  const layer = document.createElement("div");
  layer.className = "tour";
  layer.hidden = true;
  layer.innerHTML = `
    <div class="tour-block"></div><div class="tour-block"></div><div class="tour-block"></div><div class="tour-block"></div>
    <div class="tour-ring"></div>
    <div class="tour-pop" role="dialog" aria-modal="true" aria-labelledby="tour-title">
      <div class="tour-count label"></div>
      <h3 id="tour-title"></h3>
      <div class="tour-body"></div>
      <div class="tour-nav">
        <button type="button" class="tour-skip label">건너뛰기</button>
        <span class="tour-gap"></span>
        <button type="button" class="tour-extra label" hidden></button>
        <button type="button" class="tour-prev label">이전</button>
        <button type="button" class="tour-next label">다음</button>
      </div>
    </div>`;
  document.body.appendChild(layer);
  const blocks = [...layer.querySelectorAll(".tour-block")];
  const ring = layer.querySelector(".tour-ring");
  const pop = layer.querySelector(".tour-pop");

  let index = -1, current = null, cleanup = [], raf = 0, watcher = 0, picking = false;
  const PAD = 8;   // 강조 칸 여백

  /* ------------------------------ 진행 ------------------------------ */
  async function show(i) {
    teardownStep();
    if (i < 0) i = 0;
    if (i >= STEPS.length) return finish();
    index = i;
    current = STEPS[i];
    if (current.before) await current.before();
    if (current.skipIf && current.skipIf()) return show(i + 1);   // 강조할 게 없으면 건너뜀

    const el = current.target ? current.target() : null;
    if (current.target && !el) return show(i + 1);
    if (el && !current.noScroll) {
      el.scrollIntoView({ behavior: "instant", block: el.getBoundingClientRect().height > innerHeight * 0.7 ? "start" : "center" });
    }
    if (el) await sleep(150);   // 바로 이동 (부드러운 스크롤은 창이 가려지면 멈춰서 강조 칸이 어긋났음)
    fill(current);
    layer.hidden = false;
    position();

    // 넘어가는 방법별 준비
    if (current.advance === "click" && el) {
      const onClick = () => setTimeout(() => show(index + 1), 150);   // 앱이 먼저 반응하게 잠깐 기다림
      el.addEventListener("click", onClick, { once: true });
      cleanup.push(() => el.removeEventListener("click", onClick));
    }
    if (current.advance === "event") {
      const onEvent = () => setTimeout(() => show(index + 1), 300);
      document.addEventListener(current.event, onEvent, { once: true });
      cleanup.push(() => document.removeEventListener(current.event, onEvent));
    }
    // 화면이 움직이면 강조 칸이 따라가게, 조건이 바뀌면 뒤로 가게 계속 확인
    const loop = () => { position(); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    picking = false;
    if (current.watchClick) {   // 목록에서 무언가를 골랐는지 기억 (backIf에서 사용)
      const box = $(current.watchClick);
      const onPick = (e) => { if (e.target.closest("button")) picking = true; };
      box.addEventListener("click", onPick, true);
      cleanup.push(() => box.removeEventListener("click", onPick, true));
    }
    if (current.backIf) watcher = setInterval(() => { if (current.backIf()) show(index - 1); }, 300);
  }

  function fill(step) {
    const val = (v) => (typeof v === "function" ? v() : v);
    pop.querySelector(".tour-count").textContent = `${index + 1} / ${STEPS.length}`;
    pop.querySelector("#tour-title").textContent = val(step.title);
    pop.querySelector(".tour-body").innerHTML = val(step.body);   // 안내 문구는 이 파일에 고정된 글이라 HTML로 넣어도 안전
    const next = pop.querySelector(".tour-next");
    next.hidden = step.advance !== "next";
    next.textContent = step.nextLabel || "다음";
    pop.querySelector(".tour-prev").hidden = index === 0 || !!step.hidePrev;   // 답변 기다리는 중엔 뒤로 가기 없음 (건너뛰기는 항상 가능)
    const extra = pop.querySelector(".tour-extra");
    extra.hidden = !step.extra;
    if (step.extra) extra.textContent = step.extra.label;
    pop.dataset.waiting = step.advance === "next" ? "" : "true";   // 직접 해 볼 차례면 강조 칸이 깜빡임
    ring.classList.toggle("pulse", step.advance === "click" || step.advance === "event");
  }

  // 강조 칸(구멍)과 어두운 판 4장, 말풍선 위치 계산
  function position() {
    const el = current && current.target ? current.target() : null;
    const W = innerWidth, H = innerHeight;
    if (!el) {   // 강조할 곳이 없으면 화면 전체를 어둡게 + 말풍선은 가운데
      setBox(blocks[0], 0, 0, W, H); [1, 2, 3].forEach((n) => setBox(blocks[n], 0, 0, 0, 0));
      ring.style.display = "none";
      pop.dataset.place = "center";
      pop.style.left = `${(W - pop.offsetWidth) / 2}px`;
      pop.style.top = `${(H - pop.offsetHeight) / 2}px`;
      return;
    }
    const r = el.getBoundingClientRect();
    const x = Math.max(0, r.left - PAD), y = Math.max(0, r.top - PAD);
    const x2 = Math.min(W, r.right + PAD), y2 = Math.min(H, r.bottom + PAD);
    setBox(blocks[0], 0, 0, W, y);              // 위
    setBox(blocks[1], 0, y2, W, H - y2);        // 아래
    setBox(blocks[2], 0, y, x, y2 - y);         // 왼쪽
    setBox(blocks[3], x2, y, W - x2, y2 - y);   // 오른쪽
    ring.style.display = "block";
    setBox(ring, x, y, x2 - x, y2 - y);
    placePopover({ left: x, top: y, right: x2, bottom: y2 }, current.place || "bottom");
  }

  // 말풍선을 강조 칸 옆에 두고, 자리가 모자라면 반대쪽 → 화면 안쪽으로 끌어온다. 화살표는 강조 칸을 가리키게
  function placePopover(r, prefer) {
    const W = innerWidth, H = innerHeight, pw = pop.offsetWidth, ph = pop.offsetHeight, gap = 16;
    const fits = {
      bottom: r.bottom + gap + ph <= H, top: r.top - gap - ph >= 0,
      right: r.right + gap + pw <= W, left: r.left - gap - pw >= 0,
    };
    const order = { bottom: ["bottom", "top", "right", "left"], top: ["top", "bottom", "right", "left"],
                    right: ["right", "left", "bottom", "top"], left: ["left", "right", "bottom", "top"] }[prefer];
    let place = order.find((p) => fits[p]) || "inside";
    let left, top;
    if (place === "bottom" || place === "top") {
      left = (r.left + r.right) / 2 - pw / 2;
      top = place === "bottom" ? r.bottom + gap : r.top - gap - ph;
    } else if (place === "left" || place === "right") {
      left = place === "right" ? r.right + gap : r.left - gap - pw;
      top = (r.top + r.bottom) / 2 - ph / 2;
    } else {   // 강조 칸이 화면을 거의 다 덮어서 옆에 자리가 없을 때: 화면 오른쪽 아래 구석에 둔다
      // (가운데에 두면 화가 목록 같은 내용을 가려서 누를 수 없었음. 목록은 위에서부터 채워지므로 아래 구석이 덜 가림)
      left = W - pw - 16; top = H - ph - 16;
    }
    left = Math.min(Math.max(12, left), W - pw - 12);
    top = Math.min(Math.max(12, top), H - ph - 12);
    pop.dataset.place = place;
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    // 화살표가 강조 칸의 가운데를 가리키도록 위치 조정
    if (place === "bottom" || place === "top") pop.style.setProperty("--arrow", `${Math.min(Math.max(20, (r.left + r.right) / 2 - left), pw - 20)}px`);
    else pop.style.setProperty("--arrow", `${Math.min(Math.max(20, (r.top + r.bottom) / 2 - top), ph - 20)}px`);
  }

  function setBox(node, x, y, w, h) {
    node.style.left = `${x}px`; node.style.top = `${y}px`;
    node.style.width = `${Math.max(0, w)}px`; node.style.height = `${Math.max(0, h)}px`;
  }

  function teardownStep() {
    cleanup.forEach((fn) => fn()); cleanup = [];
    cancelAnimationFrame(raf); clearInterval(watcher); watcher = 0;
  }

  function finish() {
    teardownStep();
    layer.hidden = true;
    index = -1; current = null;
    try { localStorage.setItem(DONE_KEY, "1"); } catch {}
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function waitFor(cond, timeout = 4000) {
    const t = Date.now();
    while (!cond() && Date.now() - t < timeout) await sleep(80);
  }

  /* ------------------------------ 버튼 · 키보드 ------------------------------ */
  pop.querySelector(".tour-next").addEventListener("click", () => show(index + 1));
  pop.querySelector(".tour-prev").addEventListener("click", () => {
    // 직접 해 보는 단계로 되돌아가면 상태가 꼬이지 않게, 화가 선택 창은 닫아 둔다
    if (!$("#picker").hidden) $("#picker").hidden = true;
    show(index - 1);
  });
  pop.querySelector(".tour-skip").addEventListener("click", finish);
  pop.querySelector(".tour-extra").addEventListener("click", () => current && current.extra && current.extra.run());
  document.addEventListener("keydown", (e) => {
    if (layer.hidden) return;
    if (e.key === "Escape") finish();
    if (e.key === "ArrowRight" && current && current.advance === "next") show(index + 1);
  });

  // 상단 [?] 버튼 + 처음 온 사람이면 자동 시작
  window.startTour = () => show(0);
  const helpBtn = document.getElementById("tour-btn");
  if (helpBtn) helpBtn.addEventListener("click", () => window.startTour());
  document.addEventListener("app:ready", () => {
    let done = false;
    try { done = localStorage.getItem(DONE_KEY) === "1"; } catch {}
    if (!done) setTimeout(() => window.startTour(), 600);
  }, { once: true });
})();
