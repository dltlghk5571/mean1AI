(() => {
  "use strict";
  const root = document.querySelector("[data-chat]");
  if (!root) return;
  const q = (name) => root.querySelector(`[data-chat-${name}]`);
  const csrf = root.querySelector('[name="csrf_token"]').value;
  const input = root.querySelector("#chat-message");
  const composer = q("composer");
  const history = q("history");
  const editForm = q("edit-form");
  let state = null;
  let busy = false;
  let pending = null;
  let sessionExpired = false;
  let displayedMessages = "";

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  async function api(path, data = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(path, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Citizen-CSRF": csrf },
        body: JSON.stringify(data),
        signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) {
        const error = new Error(result.message || "요청을 완료하지 못했어요.");
        error.status = response.status;
        error.fields = result.errors;
        error.urgent = result.urgent;
        throw error;
      }
      return result;
    } finally {
      clearTimeout(timeout);
    }
  }

  function controls() {
    const blocked = busy || !state || !!pending || sessionExpired;
    const canType = state && ["welcome", "intent", "description", "location", "information"].includes(state.stage);
    input.disabled = blocked || !canType;
    composer.querySelector("button").disabled = blocked || !canType || !input.value.trim();
    q("reset").disabled = blocked;
    q("confirm").disabled = blocked || !q("consent").checked;
    q("consent").disabled = blocked;
    q("edit").disabled = blocked;
    q("choices").querySelectorAll("button").forEach((button) => { button.disabled = blocked; });
    editForm.querySelectorAll("input, textarea, button").forEach((field) => { field.disabled = blocked; });
    q("retry").disabled = busy;
    q("reload").disabled = busy;
    q("busy").hidden = !busy;
    history.setAttribute("aria-busy", String(busy));
  }

  function choice(label, action, href) {
    const button = element(href ? "a" : "button", label);
    if (href) button.href = href;
    else {
      button.type = "button";
      button.addEventListener("click", () => send(action));
    }
    q("choices").append(button);
  }

  function render(next, announce = false) {
    const changed = !state || next.revision !== state.revision;
    state = next;
    const messageKey = JSON.stringify(state.messages);
    if (displayedMessages !== messageKey) {
      const wasAtBottom = history.scrollHeight - history.scrollTop - history.clientHeight < 70;
      history.replaceChildren();
      state.messages.forEach((message) => {
        const article = element("div", null, `chat-message chat-message-${message.role}`);
        article.append(element("span", message.role === "user" ? "나" : "생활민원 도우미", "chat-message-label"));
        article.append(element("p", message.text));
        history.append(article);
      });
      displayedMessages = messageKey;
      if (wasAtBottom || announce) history.scrollTop = history.scrollHeight;
    }
    q("choices").replaceChildren();
    if (["welcome", "intent", "information"].includes(state.stage)) {
      choice(state.stage === "welcome" ? "생활 불편 알리기" : "민원으로 접수할게요", "complaint");
      if (state.stage !== "information") choice("복지·생활정보 알아보기", "information");
      choice("내 민원 확인", null, "/minwon/lookup");
    } else if (state.stage === "location") {
      choice("정확한 장소를 모르겠어요", "skip_location");
    } else if (state.stage === "submitted") {
      choice("접수 결과 확인하기", null, state.redirect);
    }
    q("sources").replaceChildren();
    q("sources").hidden = !state.sources.length;
    if (state.sources.length) {
      q("sources").append(element("p", "공식 사이트에서 확인해 주세요 · 새 탭으로 열립니다"));
      state.sources.forEach((source) => {
        const link = element("a", `${source.title} ↗`);
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        q("sources").append(link);
      });
    }
    q("urgent").hidden = !state.urgent;
    const cards = state.service_cards || [];
    q("service-cards").replaceChildren();
    q("service-cards").hidden = !cards.length;
    cards.forEach((card) => {
      const article = element("article");
      article.append(element("span", card.synthetic ? "합성 자료 · 시연용" : "검수된 공식 자료", "chat-service-label"));
      article.append(element("h3", card.title));
      article.append(element("p", card.summary));
      article.append(element("small", `출처: ${card.source_title} · 재검수 예정 ${card.review_due_at}`));
      if (card.source_url) {
        const link = element("a", "공식 원문 확인 ↗");
        link.href = card.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        article.append(link);
      }
      if (card.requires_human_review) article.append(element("small", "개별 자격·처분·적용 여부는 담당자 확인이 필요해요."));
      q("service-cards").append(article);
    });
    q("review").hidden = state.stage !== "review";
    root.querySelector("[data-draft-title]").textContent = state.draft.title;
    root.querySelector("[data-draft-content]").textContent = state.draft.content;
    root.querySelector("[data-draft-location]").textContent = state.draft.location_text || "장소 미입력 · 담당자 확인이 필요해요";
    q("collected").hidden = !state.draft.content || ["information", "submitted"].includes(state.stage);
    q("summary-title").textContent = state.draft.title;
    q("summary-location").textContent = state.draft.location_text || "장소를 여쭤볼게요";
    root.querySelectorAll("[data-chat-step]").forEach((step) => {
      const current = state.stage === "submitted" ? "3" : state.stage === "review" ? "2" : "1";
      if (step.dataset.chatStep === current) step.setAttribute("aria-current", "step");
      else step.removeAttribute("aria-current");
    });
    input.maxLength = state.stage === "location" ? 300 : 4000;
    input.placeholder = {
      location: "예: 가상 데모공원 정문 앞 산책로",
      review: "아래 접수 내용을 확인해 주세요.",
      submitted: "접수 결과에서 접수번호와 조회 코드를 확인해 주세요.",
      information: "다른 궁금한 점이나 불편한 일을 적어 주세요.",
    }[state.stage] || "예: 데모공원 산책로 가로등이 어제부터 꺼져 있어요";
    composer.hidden = ["review", "submitted"].includes(state.stage);
    root.querySelector("#chat-input-help").hidden = composer.hidden;
    if (changed) {
      q("consent").checked = false;
      toggleEdit(false);
    }
    updateCount();
    controls();
    if (announce) {
      q("status").textContent = state.messages.at(-1).text;
      if (state.stage === "review") {
        root.querySelector("#chat-review-title").focus({ preventScroll: true });
        q("review").scrollIntoView({ block: "nearest" });
      } else if (!input.disabled) input.focus({ preventScroll: true });
    }
  }

  function showError(error) {
    if (error.urgent) q("urgent").hidden = false;
    const fieldMessages = Object.values(error.fields || {});
    q("error-text").textContent = error.message && error.status
      ? `${error.message} ${fieldMessages.join(" ")}`.trim()
      : "연결을 확인하지 못했어요. 입력한 내용은 남아 있어요. 다시 시도해 주세요.";
    q("error").hidden = false;
    sessionExpired = error.status === 403;
    q("session-link").hidden = !sessionExpired;
    if ([400, 409, 413, 422, 403].includes(error.status)) pending = null;
    q("retry").hidden = !pending || sessionExpired;
    q("reload").hidden = sessionExpired;
  }

  async function load() {
    if (busy) return;
    busy = true;
    q("error").hidden = true;
    q("busy").textContent = "대화를 불러오는 중이에요…";
    controls();
    try {
      const next = await api("/minwon/chat/open");
      pending = null;
      sessionExpired = false;
      busy = false;
      render(next);
    } catch (error) {
      showError(error);
    } finally {
      busy = false;
      controls();
    }
  }

  async function deliver() {
    if (busy || !pending) return;
    busy = true;
    const sent = pending;
    q("error").hidden = true;
    q("busy").textContent = sent.action === "confirm" ? "데모 민원을 접수하고 있어요…" : "이야기를 정리하고 있어요…";
    controls();
    try {
      const next = await api("/minwon/chat/turn", sent);
      pending = null;
      busy = false;
      if (sent.action === "say" || sent.action === "reset") input.value = "";
      render(next, true);
      if (sent.action === "confirm" && next.redirect) window.location.assign(next.redirect);
    } catch (error) {
      showError(error);
    } finally {
      busy = false;
      controls();
    }
  }

  function send(action, fields = {}) {
    if (busy || pending || !state || sessionExpired) return;
    pending = { revision: String(state.revision), request_id: crypto.randomUUID(), action, ...fields };
    deliver();
  }

  function updateCount() {
    q("count").textContent = `${input.value.length.toLocaleString("ko-KR")} / ${input.maxLength.toLocaleString("ko-KR")}`;
    controls();
  }

  function toggleEdit(editing) {
    editForm.hidden = !editing;
    q("review-details").hidden = editing;
    q("confirm-panel").hidden = editing;
    q("edit").hidden = editing;
    q("consent").checked = false;
    if (editing && state) {
      ["title", "content", "location_text"].forEach((key) => { editForm.elements[key].value = state.draft[key]; });
      editForm.elements.title.focus();
    }
    controls();
  }

  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value.trim()) send("say", { message: input.value });
  });
  input.addEventListener("input", updateCount);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  q("consent").addEventListener("change", controls);
  q("confirm").addEventListener("click", () => {
    if (q("consent").checked) send("confirm", { consent: "yes" });
  });
  q("edit").addEventListener("click", () => toggleEdit(true));
  q("edit-cancel").addEventListener("click", () => {
    toggleEdit(false);
    q("edit").focus();
  });
  editForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (editForm.reportValidity()) send("edit", Object.fromEntries(new FormData(editForm)));
  });
  q("retry").addEventListener("click", deliver);
  q("reload").addEventListener("click", load);
  q("reset").addEventListener("click", () => q("reset-dialog").showModal());
  q("reset-cancel").addEventListener("click", () => q("reset-dialog").close());
  q("reset-confirm").addEventListener("click", () => {
    q("reset-dialog").close();
    send("reset");
  });
  load();
})();
