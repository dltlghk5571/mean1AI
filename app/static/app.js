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
  const searchEmpty = document.querySelector("[data-search-empty]");
  const visibleCount = document.querySelector(".section-count strong");

  queueSearch.addEventListener("input", () => {
    const query = queueSearch.value.trim().toLocaleLowerCase("ko-KR");
    let matches = 0;
    for (const item of queueItems) {
      const searchable = (item.dataset.searchValue || "").toLocaleLowerCase("ko-KR");
      const visible = !query || searchable.includes(query);
      item.hidden = !visible;
      if (visible) matches += 1;
    }
    if (searchEmpty) searchEmpty.hidden = matches !== 0;
    if (visibleCount) visibleCount.textContent = String(matches);
  });
}

const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
const sidebarClose = document.querySelector("[data-sidebar-close]");

function setSidebar(open) {
  document.body.classList.toggle("sidebar-open", open);
  sidebarToggle?.setAttribute("aria-expanded", String(open));
}

sidebarToggle?.addEventListener("click", () => {
  setSidebar(!document.body.classList.contains("sidebar-open"));
});
sidebarClose?.addEventListener("click", () => setSidebar(false));
window.addEventListener("resize", () => {
  if (window.innerWidth > 900) setSidebar(false);
});

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
    button.classList.toggle("is-selected", button.dataset.departmentChoice === departmentSelect.value);
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
  const actorInput = document.querySelector("[data-actor-input]");
  const departmentCopy = document.querySelector("[data-confirm-department]");
  const actorCopy = document.querySelector("[data-confirm-actor]");
  if (departmentCopy) departmentCopy.textContent = selectedOption?.textContent?.trim() || "—";
  if (actorCopy) actorCopy.textContent = actorInput?.value.trim() || "—";
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
