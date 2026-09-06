(() => {
  const root = document.querySelector('[data-catalog-page]');
  if (!root) return;
  let pending = null;
  let busy = false;
  const message = root.querySelector('[data-catalog-message]');
  const report = (text, error = false) => { message.textContent = text; message.hidden = false; message.classList.toggle('is-error', error); };
  const controls = () => root.querySelectorAll('button,input,textarea').forEach((item) => {
    if (!item.hasAttribute('data-original-disabled')) item.dataset.originalDisabled = String(item.disabled);
    item.disabled = busy || item.dataset.originalDisabled === 'true';
  });
  async function api(path, body) {
    const response = await fetch(path, {method: body ? 'POST' : 'GET', credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf}, ...(body ? {body:JSON.stringify(body)} : {})});
    if (!response.ok) throw new Error('catalog_request_failed');
    return response.json();
  }
  function preview(data) {
    if (!data || typeof data.version !== 'string' || !Array.isArray(data.services) || !Array.isArray(data.documents)) throw new Error('invalid');
    pending = data;
    root.querySelector('[data-catalog-preview-text]').textContent = `${data.version} · 업무 ${data.services.length}개 · 출처 ${data.documents.length}개. 등록 후 상세 내용을 검토해 주세요.`;
    root.querySelector('[data-catalog-preview]').hidden = false;
  }
  async function run(action) {
    if (busy) return;
    const focused = document.activeElement;
    busy = true; controls();
    try { await action(); } catch { report('요청을 완료하지 못했습니다. 로그인 권한, JSON 형식, 이용 조건과 검토 날짜를 확인해 주세요.', true); }
    finally { busy = false; controls(); if (focused instanceof HTMLElement && focused.isConnected && !focused.disabled) focused.focus(); }
  }
  root.querySelectorAll('[data-candidate]').forEach((button) => button.addEventListener('click', () => run(async () => { preview(await api(`/api/v1/service-catalogs/candidates/${button.dataset.candidate}`)); report('자료를 불러왔습니다. 아직 서버에 등록하거나 공개하지 않았습니다.'); })));
  root.querySelector('[data-catalog-file]')?.addEventListener('change', (event) => run(async () => {
    pending = null;
    root.querySelector('[data-catalog-preview]').hidden = true;
    const file = event.target.files[0];
    if (!file || file.size > 2000000) throw new Error('invalid');
    preview(JSON.parse(await file.text()));
    report('파일을 읽었습니다. 등록 후 출처와 내용을 확인해 주세요.');
  }));
  root.querySelector('[data-catalog-import]')?.addEventListener('click', () => run(async () => {
    if (!pending) return;
    const result = await api('/api/v1/service-catalogs', pending);
    window.location.assign(`/staff/service-catalogs/${encodeURIComponent(result.version)}`);
  }));
  root.querySelector('[data-catalog-review]')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.target;
    const decision = event.submitter?.value;
    if (!['approved','withdrawn'].includes(decision) || !form.reportValidity()) return;
    if (decision === 'approved' && !form.elements.review_due_at.value) { report('공개 전 재검토 예정일을 선택해 주세요.', true); form.elements.review_due_at.focus(); return; }
    const body = {content_hash:form.dataset.hash, decision, reason:form.elements.reason.value, review_due_at:decision === 'approved' ? form.elements.review_due_at.value : null};
    run(async () => { await api(`/api/v1/service-catalogs/${encodeURIComponent(form.dataset.version)}/review`, body); window.location.reload(); });
  });
})();
