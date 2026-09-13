(() => {
  const root = document.querySelector('[data-source-report]');
  if (!root) return;
  const $ = (name) => root.querySelector(`[data-${name}]`);
  const groups = {all: '전체', missing: '누락·오류', department: '부서 표기', duplicate: '중복·변경', source: '출처·정책'};
  const kinds = {saved_html: '저장한 HTML', rendered_dom: '브라우저 DOM', http: 'HTTP 수집'};
  let data = null, group = 'all', offset = 0, busy = false;
  let generation = 0, controller = null, pageSearch = [];
  const pageSize = 12;
  const make = (tag, text = '', className = '') => {
    const item = document.createElement(tag);
    item.textContent = text;
    if (className) item.className = className;
    return item;
  };
  const message = (text, error = false) => {
    const target = $('report-message');
    target.textContent = text;
    target.hidden = !text;
    target.classList.toggle('is-error', error);
    target.setAttribute('role', error ? 'alert' : 'status');
    if (error) target.focus();
  };
  const controls = () => {
    $('report-file').disabled = busy;
    $('report-example').disabled = busy;
    $('report-export').disabled = busy || !data;
    root.setAttribute('aria-busy', String(busy));
  };
  function clear() {
    data = null;
    pageSearch = [];
    $('report-result').hidden = true;
    $('report-empty').hidden = false;
    $('task-list').replaceChildren();
    $('evidence-body').replaceChildren();
    $('evidence-select').replaceChildren();
    $('task-search').value = '';
    group = 'all'; offset = 0;
    controls();
  }
  function sourceLink(url) {
    if (!url) return null;
    try {
      const value = new URL(url);
      if (value.protocol !== 'https:' || value.hostname !== 'www.seongnam.go.kr' || value.username || value.password) return null;
      const link = make('a', '원본 페이지 열기 ↗', 'sr-link');
      link.href = value.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.referrerPolicy = 'no-referrer';
      return link;
    } catch { return null; }
  }
  function rowList(parent, values, render) {
    let shown = 0;
    const content = make('div', '', 'sr-evidence-rows');
    const more = make('button', '', 'sr-secondary');
    more.type = 'button';
    const append = (focus = false) => {
      const next = values.slice(shown, shown + 25).map(render);
      content.append(...next);
      shown += next.length;
      more.hidden = shown >= values.length;
      more.textContent = `다음 ${Math.min(25, values.length - shown)}개 보기 (${shown}/${values.length})`;
      if (focus && next[0]) { next[0].tabIndex = -1; next[0].focus(); }
    };
    more.addEventListener('click', () => append(true));
    parent.append(content, more);
    append();
  }
  function renderEvidence(index, focus = false) {
    const page = data.pages[index];
    const target = $('evidence-body');
    target.replaceChildren();
    if (!page) {
      target.append(make('p', '확보된 페이지가 없습니다. 왼쪽에서 입력 오류를 확인해 주세요.', 'sr-muted'));
      $('evidence-select').disabled = true;
      return;
    }
    $('evidence-select').disabled = false;
    $('evidence-select').value = String(index);
    const heading = make('div', '', 'sr-evidence-heading');
    heading.append(make('h3', page.title));
    if (page.source_code) heading.append(make('span', page.source_code, 'sr-code'));
    const link = sourceLink(page.source_url);
    if (link) heading.append(link);
    target.append(heading, make('p', `${kinds[page.input_kind]} · 항목 ${page.records_seen}개 중 ${page.records_extracted}개 추출`, 'sr-muted'));
    if (page.page_kind === 'welfare_detail') {
      const comparison = make('div', '', 'sr-comparison');
      const contact = make('div');
      contact.append(make('h4', '본문 문의처'), make('p', page.body_contacts.join('\n') || '확인하지 못함'));
      const footer = make('div');
      footer.append(make('h4', '하단 부서 표시'), make('p', page.footer_department || '확인하지 못함'));
      comparison.append(contact, footer);
      target.append(comparison, make('p', '표시된 부서의 실제 담당·관리 역할은 원문과 조직도로 확인해 주세요.', 'sr-muted'));
    }
    if (page.policy_year_mentions.length) target.append(make('p', `본문에 등장한 연도: ${page.policy_year_mentions.join(', ')}년 · 적용 기간 확인 필요`, 'sr-year'));
    if (page.listing_items.length) {
      target.append(make('h4', `목록 항목 ${page.listing_items.length}개`));
      rowList(target, page.listing_items, (item) => {
        const row = make('article');
        row.append(make('h5', item.title), make('span', item.source_code, 'sr-code'), make('p', item.summary));
        row.append(make('p', `목록 부서: ${item.displayed_department || '확인하지 못함'}`, 'sr-muted'));
        return row;
      });
    }
    if (page.work_rows.length) {
      target.append(make('h4', `팀별 업무 ${page.work_rows.length}행`));
      rowList(target, page.work_rows, (row) => {
        const item = make('article');
        item.append(make('h5', `${row.unit_name} · ${row.row_number}행`), make('p', row.duty));
        return item;
      });
    }
    if (page.body) {
      const details = make('details', '', 'sr-body');
      details.append(make('summary', '추출 본문 보기'), make('p', page.body));
      target.append(details);
    }
    const trace = make('details', '', 'sr-provenance');
    trace.append(make('summary', '페이지 처리 시각·해시'), make('p', `처리 시각: ${page.processed_at}`), make('p', `입력 파일 SHA-256: ${page.input_sha256}`));
    target.append(trace);
    if (focus) {
      document.getElementById('sr-evidence-title').focus({preventScroll: true});
      root.querySelector('.sr-evidence').scrollIntoView({block: 'start'});
    }
  }
  function renderTasks() {
    const term = $('task-search').value.trim().normalize('NFKC').toLowerCase();
    const filtered = data.tasks.filter((task) => {
      const text = [task.title, task.reason, task.source_code || '', pageSearch[task.page_index] || ''].join(' ').normalize('NFKC').toLowerCase();
      return (group === 'all' || task.kind === group) && (!term || text.includes(term));
    });
    offset = Math.min(offset, Math.max(0, Math.ceil(filtered.length / pageSize) - 1));
    const target = $('task-list');
    target.replaceChildren();
    for (const task of filtered.slice(offset * pageSize, (offset + 1) * pageSize)) {
      const row = make('article', '', `sr-task sr-kind-${task.kind}`);
      row.append(make('span', groups[task.kind] || '추가 확인', 'sr-task-kind'), make('h3', task.title), make('p', task.reason));
      if (task.source_code) row.append(make('span', task.source_code, 'sr-code'));
      if (task.page_index !== null) {
        const button = make('button', '페이지 근거 보기', 'sr-text-button');
        button.type = 'button';
        button.setAttribute('aria-controls', 'sr-evidence-body');
        button.addEventListener('click', () => renderEvidence(task.page_index, true));
        row.append(button);
      }
      const link = sourceLink(task.source_url);
      if (link) row.append(link);
      target.append(row);
    }
    if (!filtered.length) target.append(make('p', '이 조건에 맞는 항목이 없습니다. 검색어나 종류를 바꿔보세요.', 'sr-no-results'));
    $('filter-count').textContent = `${groups[group]} ${filtered.length}개 · 전체 확인할 일 ${data.tasks.length}개`;
    $('task-page').textContent = `${filtered.length ? offset + 1 : 0} / ${Math.ceil(filtered.length / pageSize)}`;
    $('task-prev').disabled = offset === 0;
    $('task-next').disabled = (offset + 1) * pageSize >= filtered.length;
    $('task-filters').querySelectorAll('button').forEach((item) => item.setAttribute('aria-pressed', String(item.dataset.group === group)));
  }
  function show(view, filename) {
    data = view;
    pageSearch = data.pages.map((page) => [page.title, page.source_code || '', page.footer_department || '', ...page.body_contacts, ...page.work_rows.map((row) => `${row.unit_name} ${row.duty}`), ...page.listing_items.map((item) => `${item.title} ${item.source_code} ${item.displayed_department || ''}`)].join(' '));
    $('report-empty').hidden = true;
    $('report-result').hidden = false;
    $('report-title').textContent = data.source_label;
    $('report-origin').textContent = `${data.synthetic ? '합성 예시 · 실제 출처 연결 없음' : '입력한 보고서 · 원본 대조 필요'}${filename ? ` · ${filename}` : ''}`;
    $('report-boundary').textContent = data.completed ? '입력 자료의 추출·대조 조건을 충족했습니다. 이용 조건·정책·실제 담당 부서의 검수와 공개 승인은 별도입니다.' : '아직 확보하거나 확인할 자료가 있습니다. 아래 항목부터 살펴보세요. 시민 검색에는 공개되지 않습니다.';
    $('report-mismatch').hidden = data.summary_matches;
    $('report-hash').textContent = data.report_sha256;
    const stats = [['확보한 페이지', data.pages.length], ['본문 문서', data.documents], ['목록 항목', data.summary.unique_listing_items], ['확인할 일', data.tasks.length]];
    const statRoot = $('report-stats');
    statRoot.replaceChildren(...stats.map(([label, number]) => {
      const card = make('div'); card.append(make('span', label), make('strong', String(number))); return card;
    }));
    $('task-count').textContent = `${data.tasks.length}개`;
    const filters = $('task-filters'); filters.replaceChildren();
    for (const [key, label] of Object.entries(groups)) {
      const count = key === 'all' ? data.tasks.length : data.tasks.filter((item) => item.kind === key).length;
      const button = make('button', `${label} ${count}`); button.type = 'button'; button.dataset.group = key;
      button.addEventListener('click', () => { group = key; offset = 0; renderTasks(); });
      filters.append(button);
    }
    const select = $('evidence-select'); select.replaceChildren();
    data.pages.forEach((page, index) => { const option = make('option', `${index + 1}. ${page.title}${page.source_code ? ` (${page.source_code})` : ''}`); option.value = String(index); select.append(option); });
    renderTasks(); renderEvidence(0);
    $('report-title').focus();
  }
  const errors = {
    report_too_large: '파일이 8MB를 넘습니다. 입력 HTML 묶음을 나누어 보고서를 만든 뒤 다시 열어주세요.',
    report_contains_direct_identifiers: '전화번호·이메일·주민등록번호 형식이 포함되어 있습니다. 수집기의 마스킹 결과를 확인한 뒤 다시 열어주세요.',
    report_source_unknown: '등록된 수집 출처의 보고서가 아닙니다.',
    report_document_reference_invalid: '본문 문서의 참조가 빠졌거나 중복되었습니다. 원래 보고서 파일을 다시 확인해 주세요.',
    report_url_not_allowed: '등록 범위를 벗어난 출처 주소가 있습니다. 원래 보고서의 URL을 확인해 주세요.',
  };
  async function load(file = null) {
    if (busy) return;
    const token = ++generation;
    clear();
    if (file && file.size > 8000000) { message(errors.report_too_large, true); $('report-file').value = ''; return; }
    busy = true; controls(); message('보고서의 페이지·문서와 누락 항목을 확인하고 있습니다.');
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 20000);
    try {
      const response = await fetch(file ? '/api/v1/source-reports/preview' : '/api/v1/source-reports/example', {
        method: file ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}, ...(file ? {body: file} : {}),
      });
      if (response.status === 401 || response.status === 403) throw new Error('session_expired');
      const result = await response.json();
      if (!response.ok) throw new Error(errors[result.detail] ? result.detail : 'invalid_report');
      if (token !== generation) return;
      show(result, file ? file.name.slice(0, 120) : '화면 확인용 예시');
      message('보고서를 열었습니다. 확인할 일을 선택해 페이지 근거를 살펴보세요.');
    } catch (error) {
      if (token !== generation) return;
      const text = error.name === 'AbortError' ? '확인 시간이 초과되었습니다. 잠시 후 다시 열어주세요.' : error.message === 'session_expired' ? '로그인 상태를 확인할 수 없습니다. 다시 로그인한 뒤 파일을 열어주세요.' : errors[error.message] || '보고서를 열지 못했습니다. 수집기 보고서 v2인지, 파일이 변경되거나 손상되지 않았는지 확인해 주세요.';
      message(text, true);
    } finally {
      clearTimeout(timeout);
      if (token === generation) { busy = false; controller = null; $('report-file').value = ''; controls(); }
    }
  }
  $('report-example').addEventListener('click', () => load());
  $('report-file').addEventListener('change', (event) => { const file = event.target.files[0]; if (file) load(file); });
  $('report-clear').addEventListener('click', () => { generation += 1; controller?.abort(); busy = false; clear(); message('보고서를 닫았습니다. 저장된 검수 결과는 없습니다.'); $('report-example').focus(); });
  $('task-search').addEventListener('input', () => { offset = 0; renderTasks(); });
  $('evidence-select').addEventListener('change', (event) => renderEvidence(Number(event.target.value)));
  $('task-prev').addEventListener('click', () => { offset -= 1; renderTasks(); });
  $('task-next').addEventListener('click', () => { offset += 1; renderTasks(); });
  $('report-export').addEventListener('click', () => {
    if (!data) return;
    const pages = data.pages.map(({index, title, source_code, source_url, input_sha256}) => ({index, title, source_code, source_url, input_sha256}));
    const result = {schema_version: '1', report_sha256: data.report_sha256, source_id: data.source_id, review_status: 'pending', exported_at: new Date().toISOString(), note: '확인할 일 목록입니다. 검수 완료나 공개 승인이 아닙니다.', pages, tasks: data.tasks};
    const href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type: 'application/json;charset=utf-8'}));
    const link = make('a'); link.href = href; link.download = `source-review-${data.source_id}-${data.report_sha256.slice(0, 12)}.json`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(href), 1000);
  });
  window.addEventListener('pagehide', () => controller?.abort());
})();
