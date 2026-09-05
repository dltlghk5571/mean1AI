const examples = {
  streetlight: {
    title: "데모 공원 입구 가로등 고장",
    content:
      "데모 공원 동문 앞 가로등 두 개의 불이 꺼져 밤길이 위험합니다. 합성 연락처는 010-0000-1234입니다.",
    location: "데모 공원 동문 앞",
  },
  road: {
    title: "데모 사거리 인근 포트홀 신고",
    content:
      "데모 사거리 방향 2차로에 큰 포트홀이 생기고 도로가 패여 차량이 급하게 피하고 있습니다.",
    location: "데모 사거리 2차로",
  },
  welfare: {
    title: "복지 지원 대상 문의",
    content:
      "기초생활 지원 대상인지 자동으로 결정해 달라는 문의입니다. 합성 이메일은 citizen@example.com입니다.",
    location: "",
  },
  urgent: {
    title: "가스 누출 의심 긴급 신고",
    content:
      "데모 건물 앞 배관 근처에서 가스 냄새가 매우 심하고 누출되는 것 같습니다. 지금도 냄새가 납니다.",
    location: "서현동 데모 건물 앞",
  },
};

const intakeDialog = document.querySelector("#intake-dialog");
const intakeForm = document.querySelector("[data-intake-form]");

function updateCharacterCount(input) {
  const field = input.closest(".field-group");
  const counter = field?.querySelector("[data-character-count]");
  if (!counter) return;
  const maximum = Number(input.getAttribute("maxlength")) || 0;
  counter.textContent = `${input.value.length.toLocaleString("ko-KR")} / ${maximum.toLocaleString("ko-KR")}`;
}

for (const input of document.querySelectorAll("[data-counted-input]")) {
  updateCharacterCount(input);
  input.addEventListener("input", () => updateCharacterCount(input));
}

function fillExample(exampleKey) {
  const example = examples[exampleKey];
  if (!example || !intakeForm) return;

  const title = intakeForm.querySelector("#title");
  const content = intakeForm.querySelector("#content");
  const location = intakeForm.querySelector("#location_text");
  title.value = example.title;
  content.value = example.content;
  location.value = example.location;
  updateCharacterCount(content);
  title.focus();

  for (const button of intakeForm.querySelectorAll("[data-example]")) {
    button.classList.toggle("is-selected", button.dataset.example === exampleKey);
  }
}

function openIntake(exampleKey) {
  if (!intakeDialog) return;
  if (!intakeDialog.open) intakeDialog.showModal();
  if (exampleKey) fillExample(exampleKey);
  if (window.location.hash !== "#new-complaint") {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#new-complaint`);
  }
}

function closeIntake() {
  if (!intakeDialog?.open || intakeForm?.dataset.submitting === "true") return;
  intakeDialog.close();
  if (window.location.hash === "#new-complaint") {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }
}

for (const button of document.querySelectorAll("[data-open-intake]")) {
  button.addEventListener("click", () => openIntake(button.dataset.exampleLaunch));
}

for (const button of document.querySelectorAll("[data-close-intake]")) {
  button.addEventListener("click", closeIntake);
}

for (const button of document.querySelectorAll("[data-example]")) {
  button.addEventListener("click", () => fillExample(button.dataset.example));
}

if (intakeDialog) {
  intakeDialog.addEventListener("click", (event) => {
    if (event.target !== intakeDialog) return;
    const bounds = intakeDialog.getBoundingClientRect();
    const clickedInside =
      event.clientX >= bounds.left &&
      event.clientX <= bounds.right &&
      event.clientY >= bounds.top &&
      event.clientY <= bounds.bottom;
    if (!clickedInside) closeIntake();
  });

  intakeDialog.addEventListener("close", () => {
    if (window.location.hash === "#new-complaint") {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    }
  });

  if (window.location.hash === "#new-complaint") openIntake();
}

if (intakeForm) {
  intakeForm.addEventListener("submit", (event) => {
    if (intakeForm.dataset.submitting === "true") return;
    event.preventDefault();
    if (!intakeForm.reportValidity()) return;

    intakeForm.dataset.submitting = "true";
    const processingView = document.querySelector("[data-processing-view]");
    const dialogSteps = document.querySelectorAll(".dialog-steps span");
    intakeForm.hidden = true;
    if (processingView) processingView.hidden = false;
    dialogSteps[0]?.classList.add("is-complete");
    dialogSteps[1]?.classList.add("is-active");

    const stages = [...document.querySelectorAll("[data-processing-stage]")];
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const interval = reducedMotion ? 40 : 360;

    stages.forEach((stage, index) => {
      window.setTimeout(() => {
        for (const item of stages) item.classList.remove("is-active");
        for (let completed = 0; completed < index; completed += 1) {
          stages[completed].classList.add("is-complete");
        }
        stage.classList.add("is-active");
      }, interval * index);
    });

    window.setTimeout(
      () => {
        for (const stage of stages) {
          stage.classList.remove("is-active");
          stage.classList.add("is-complete");
        }
        dialogSteps[1]?.classList.remove("is-active");
        dialogSteps[1]?.classList.add("is-complete");
        dialogSteps[2]?.classList.add("is-active");
        HTMLFormElement.prototype.submit.call(intakeForm);
      },
      interval * stages.length + (reducedMotion ? 30 : 280),
    );
  });
}

const queueSearch = document.querySelector("[data-queue-search]");
if (queueSearch) {
  const queueItems = [...document.querySelectorAll("[data-queue-item]")];
  const queueList = document.querySelector("[data-queue-list]");
  const searchEmpty = document.querySelector("[data-search-empty]");
  const visibleCount = document.querySelector("[data-queue-count]");
  const clearSearch = document.querySelector("[data-clear-search]");
  const searchShortcut = document.querySelector("[data-search-shortcut]");
  const announcement = document.querySelector("[data-queue-announcement]");
  const queueSort = document.querySelector("[data-queue-sort]");
  const statusRank = { urgent_review: 0, needs_review: 1, received: 2, assigned: 3, reviewed: 4 };
  const originalOrder = new Map(queueItems.map((item, index) => [item, index]));
  const normalizeSearch = (value) => value.normalize("NFKC").toLocaleLowerCase("ko-KR");
  let announceTimer;

  function filterQueue(announce = true) {
    const words = normalizeSearch(queueSearch.value).trim().split(/\s+/).filter(Boolean);
    let matches = 0;
    for (const item of queueItems) {
      const searchable = normalizeSearch(item.dataset.searchValue || "");
      const visible = words.every((word) => searchable.includes(word));
      item.hidden = !visible;
      if (visible) matches += 1;
    }
    if (searchEmpty) searchEmpty.hidden = matches !== 0;
    if (visibleCount) visibleCount.textContent = String(matches);
    if (clearSearch) clearSearch.hidden = queueSearch.value.length === 0;
    if (searchShortcut) searchShortcut.hidden = queueSearch.value.length !== 0;
    window.clearTimeout(announceTimer);
    if (announce && announcement) {
      announceTimer = window.setTimeout(() => {
        announcement.textContent = `현재 목록 ${queueItems.length}건 중 ${matches}건 표시, ${queueSort?.selectedOptions[0]?.textContent || "최신 접수순"}.`;
      }, 180);
    }
  }

  function resetSearch() {
    queueSearch.value = "";
    filterQueue();
    queueSearch.focus();
  }

  queueSearch.addEventListener("input", () => filterQueue());
  clearSearch?.addEventListener("click", resetSearch);
  document.querySelector("[data-reset-search]")?.addEventListener("click", resetSearch);
  queueSearch.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) {
      event.preventDefault();
      resetSearch();
    }
  });
  queueSort?.addEventListener("change", () => {
    if (!queueList) return;
    const sorted = [...queueItems].sort((a, b) => {
      const latestFirst = originalOrder.get(a) - originalOrder.get(b);
      if (queueSort.value === "oldest") return -latestFirst;
      if (queueSort.value === "priority") {
        return (statusRank[a.dataset.queueStatus] ?? 5) - (statusRank[b.dataset.queueStatus] ?? 5) || latestFirst;
      }
      return latestFirst;
    });
    queueList.append(...sorted);
    filterQueue();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    if (!(event.target instanceof Element)) return;
    if (event.target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])")) return;
    if (document.querySelector("dialog[open]") || document.body.classList.contains("sidebar-open")) return;
    event.preventDefault();
    queueSearch.focus();
  });
  filterQueue(false);
}

const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
const sidebarClose = document.querySelector("[data-sidebar-close]");
const sidebar = document.querySelector("#app-sidebar");
const mainPanel = document.querySelector(".app-main");
const mobileMenu = window.matchMedia("(max-width: 900px)");

function setSidebar(open, restoreFocus = true) {
  open = open && mobileMenu.matches;
  document.body.classList.toggle("sidebar-open", open);
  sidebarToggle?.setAttribute("aria-expanded", String(open));
  sidebarToggle?.setAttribute("aria-label", open ? "메뉴 닫기" : "메뉴 열기");
  if (sidebar) sidebar.inert = mobileMenu.matches && !open;
  if (mainPanel) mainPanel.inert = open;
  if (open) sidebar?.querySelector("a")?.focus();
  else if (restoreFocus) sidebarToggle?.focus();
}

sidebarToggle?.addEventListener("click", () => {
  setSidebar(!document.body.classList.contains("sidebar-open"));
});
sidebarClose?.addEventListener("click", () => setSidebar(false));
mobileMenu.addEventListener("change", () => setSidebar(false, false));
setSidebar(false, false);

document.addEventListener("keydown", (event) => {
  if (!document.body.classList.contains("sidebar-open")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    setSidebar(false);
  } else if (event.key === "Tab") {
    const controls = [...sidebar.querySelectorAll("a[href], button:not([disabled])")];
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
});

const sectionLinks = [...document.querySelectorAll(".detail-nav a")];
function syncSectionNavigation() {
  const current = sectionLinks.find((link) => link.hash === window.location.hash) || sectionLinks[0];
  for (const link of sectionLinks) {
    if (link === current) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
}
window.addEventListener("hashchange", syncSectionNavigation);
syncSectionNavigation();

const currentDate = document.querySelector("[data-current-date]");
if (currentDate) {
  currentDate.textContent = new Intl.DateTimeFormat("ko-KR", {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
}

function showToast(message) {
  const region = document.querySelector(".toast-region");
  if (!region) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  const icon = document.createElement("span");
  icon.textContent = "✓";
  const copy = document.createElement("strong");
  copy.textContent = message;
  toast.append(icon, copy);
  region.append(toast);
  window.setTimeout(() => toast.remove(), 2600);
}

const departmentSelect = document.querySelector("[data-department-select]");
const candidateButtons = [...document.querySelectorAll("[data-department-choice]")];

function syncCandidateSelection() {
  if (!departmentSelect) return;
  for (const button of candidateButtons) {
    const selected = button.dataset.departmentChoice === departmentSelect.value;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
}

for (const button of candidateButtons) {
  button.addEventListener("click", () => {
    if (!departmentSelect) return;
    departmentSelect.value = button.dataset.departmentChoice;
    syncCandidateSelection();
    showToast("담당 후보를 최종 선택에 반영했습니다.");
  });
}

departmentSelect?.addEventListener("change", syncCandidateSelection);
syncCandidateSelection();

const previewDialog = document.querySelector("#preview-dialog");
const draftInput = document.querySelector("[data-draft-input]");
const previewCopy = document.querySelector("[data-preview-copy]");

document.querySelector("[data-preview-draft]")?.addEventListener("click", () => {
  if (!previewDialog || !draftInput || !previewCopy) return;
  previewCopy.textContent = draftInput.value;
  previewDialog.showModal();
});

for (const button of document.querySelectorAll("[data-close-preview]")) {
  button.addEventListener("click", () => previewDialog?.close());
}

const reviewForm = document.querySelector("[data-review-form]");
const approvalDialog = document.querySelector("#approval-dialog");

reviewForm?.addEventListener("submit", (event) => {
  if (reviewForm.dataset.confirmed === "true") return;
  event.preventDefault();
  if (!reviewForm.reportValidity() || !approvalDialog) return;

  const selectedOption = departmentSelect?.options[departmentSelect.selectedIndex];
  const departmentCopy = document.querySelector("[data-confirm-department]");
  if (departmentCopy) departmentCopy.textContent = selectedOption?.textContent?.trim() || "—";
  approvalDialog.showModal();
});

document.querySelector("[data-close-approval]")?.addEventListener("click", () => {
  approvalDialog?.close();
});

document.querySelector("[data-confirm-approval]")?.addEventListener("click", () => {
  if (!reviewForm) return;
  reviewForm.dataset.confirmed = "true";
  approvalDialog?.close();
  reviewForm.requestSubmit();
});

document.querySelector("[data-reprocess-form]")?.addEventListener("submit", (event) => {
  const shouldContinue = window.confirm(
    "현재 규칙 설정으로 다시 분석할까요? 기존 담당자 검토 상태는 초기화됩니다.",
  );
  if (!shouldContinue) event.preventDefault();
});

for (const dialog of document.querySelectorAll(".confirm-dialog, .preview-dialog")) {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    const clickedInside =
      event.clientX >= bounds.left &&
      event.clientX <= bounds.right &&
      event.clientY >= bounds.top &&
      event.clientY <= bounds.bottom;
    if (!clickedInside) dialog.close();
  });
}

if (document.querySelector(".alert-success")) {
  window.setTimeout(() => showToast("담당자 검토 기록이 안전하게 저장되었습니다."), 250);
}
