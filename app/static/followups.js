(() => {
  'use strict';
  const forms = [...document.querySelectorAll('[data-followup-form]')];
  const dirty = new Set();
  document.querySelectorAll('[data-followup-refresh]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      window.location.reload();
    });
  });
  forms.forEach((form) => {
    const body = form.elements.body;
    const consent = form.elements.confirmed;
    const error = form.querySelector('[data-followup-error]');
    const counter = form.querySelector('[data-followup-count]');
    let busy = false;
    body.addEventListener('input', () => {
      if (body.value.trim()) dirty.add(form); else dirty.delete(form);
      consent.checked = false;
      error.hidden = true;
      if (counter) counter.textContent = `${body.value.length.toLocaleString('ko-KR')} / 2,000`;
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      busy = true;
      error.hidden = true;
      const button = form.querySelector('button[type="submit"]');
      const label = button.textContent;
      const controls = [...form.querySelectorAll('input, textarea, button')];
      const citizen = form.dataset.mode === 'citizen';
      const payload = { body: body.value, confirmed: consent.checked ? 'yes' : '' };
      if (citizen) payload.request_key = form.elements.request_key.value;
      const csrf = form.elements.csrf_token.value;
      controls.forEach((control) => { control.disabled = true; });
      form.setAttribute('aria-busy', 'true');
      button.textContent = '저장하고 있어요…';
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch(form.action, {
          method: 'POST', credentials: 'same-origin', cache: 'no-store',
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json', [citizen ? 'X-Citizen-CSRF' : 'X-CSRF-Token']: csrf },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || '세션이나 공개 권한을 확인해 주세요. 입력 내용은 유지됩니다.');
        dirty.delete(form);
        if (citizen) window.location.assign(result.redirect);
        else window.location.reload();
      } catch (failure) {
        error.textContent = failure instanceof SyntaxError || failure.name === 'AbortError' || failure instanceof TypeError
          ? '저장 결과를 확인하지 못했어요. 내용은 유지되니 같은 내용으로 다시 시도해 주세요.'
          : failure.message;
        error.hidden = false;
        error.focus();
        controls.forEach((control) => { control.disabled = false; });
        button.textContent = label;
        form.setAttribute('aria-busy', 'false');
        busy = false;
      } finally { clearTimeout(timeout); }
    });
  });
  window.addEventListener('beforeunload', (event) => {
    if (!dirty.size) return;
    event.preventDefault();
    event.returnValue = '';
  });
  const target = document.getElementById(window.location.hash.slice(1));
  if (target?.classList.contains('followup-thread')) target.focus({ preventScroll: true });
})();
