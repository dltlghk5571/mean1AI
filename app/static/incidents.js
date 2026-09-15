/* Keep reviewed text available after validation, stale revisions, or uncertain network results. */
document.querySelectorAll('[data-incident-form]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (form.dataset.saving === 'true') return;
    const error = form.querySelector('[data-incident-error]');
    const button = form.querySelector('button[type="submit"]');
    const body = new FormData(form);
    form.dataset.saving = 'true';
    button.disabled = true;
    error.hidden = true;
    form.setAttribute('aria-busy', 'true');
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: JSON.stringify(Object.fromEntries(body)), credentials: 'same-origin',
        headers: {Accept: 'application/json', 'Content-Type': 'application/json'}, signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) {
        error.textContent = data.message || data.detail || '권한과 로그인 상태를 확인해 주세요.';
        error.hidden = false;
        error.focus();
        return;
      }
      if (typeof data.redirect !== 'string' || !/^\/staff\/incidents\/[0-9a-f-]{36}$/.test(data.redirect)) {
        throw new Error('Unexpected incident response');
      }
      window.location.assign(data.redirect);
    } catch {
      error.textContent = '저장 결과를 확인하지 못했습니다. 입력 내용은 유지됩니다. 다른 탭에서 현재 연결·공개 상태를 확인한 뒤 다시 시도해 주세요.';
      error.hidden = false;
      error.focus();
    } finally {
      window.clearTimeout(timeout);
      form.dataset.saving = 'false';
      button.disabled = false;
      form.setAttribute('aria-busy', 'false');
    }
  });
});
