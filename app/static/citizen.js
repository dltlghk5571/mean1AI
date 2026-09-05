(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  let toastTimer;
  const toast = (message) => {
    const node = $('[data-toast]');
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 4500);
  };

  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', async () => {
      const value = $(`[data-${button.dataset.copy}]`).textContent;
      try {
        await navigator.clipboard.writeText(value);
        toast(button.dataset.copy === 'lookup-code' ? '조회 코드를 복사했어요.' : '접수번호를 복사했어요.');
      } catch {
        toast('복사 권한을 확인하거나, 내용을 선택해 직접 복사해 주세요.');
      }
    });
  });

  const clearErrors = (form) => {
    const alert = $('[data-form-alert]');
    alert.hidden = true;
    form.querySelectorAll('[aria-invalid]').forEach((field) => field.removeAttribute('aria-invalid'));
    form.querySelectorAll('[data-error-for]').forEach((node) => { node.textContent = ''; });
  };
  const showError = (form, message, errors = {}) => {
    const alert = $('[data-form-alert]');
    alert.textContent = message;
    alert.hidden = false;
    Object.entries(errors).forEach(([name, text]) => {
      const field = form.elements.namedItem(name);
      if (field instanceof HTMLElement) field.setAttribute('aria-invalid', 'true');
      const note = [...form.querySelectorAll('[data-error-for]')].find((node) => node.dataset.errorFor === name);
      if (note) note.textContent = text;
    });
    alert.focus();
  };
  const send = async (form, path, data) => {
    const response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Citizen-CSRF': form.elements.csrf_token.value },
      body: JSON.stringify(data),
      cache: 'no-store',
    });
    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error('서버 응답을 확인할 수 없어요. 입력 내용은 유지되니 잠시 후 다시 시도해 주세요.');
    }
    if (!response.ok) {
      const error = new Error(result.message || '요청을 완료하지 못했어요. 다시 시도해 주세요.');
      error.fields = result.errors || {};
      throw error;
    }
    return result;
  };
  const setBusy = (form, busy) => {
    form.setAttribute('aria-busy', String(busy));
    form.querySelectorAll('button, input:not([type="hidden"]), textarea').forEach((control) => {
      control.disabled = busy;
    });
  };

  const form = $('[data-citizen-form]');
  if (form) {
    let stage = 1;
    let busy = false;
    let dirty = false;
    let completed = false;
    const writePanel = $('[data-write-panel]');
    const reviewPanel = $('[data-review-panel]');
    const count = () => {
      $('[data-content-count]').textContent = `${form.elements.content.value.length.toLocaleString('ko-KR')} / 20,000`;
    };
    const changeStage = (next) => {
      stage = next;
      writePanel.hidden = next !== 1;
      reviewPanel.hidden = next !== 2;
      document.querySelectorAll('[data-step]').forEach((step) => {
        step.removeAttribute('aria-current');
        if (Number(step.dataset.step) === next) step.setAttribute('aria-current', 'step');
        step.classList.toggle('complete', Number(step.dataset.step) < next);
      });
      (next === 1 ? form.elements.title : $('#review-heading')).focus();
    };
    const payload = () => ({
      title: form.elements.title.value,
      content: form.elements.content.value,
      location_text: form.elements.location_text.value,
      request_key: form.elements.request_key.value,
      consent: form.elements.consent.checked ? 'yes' : '',
    });
    form.addEventListener('input', () => { dirty = true; count(); });
    window.addEventListener('beforeunload', (event) => {
      if (dirty && !completed) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
    $('[data-fill-example]').addEventListener('click', () => {
      if (form.elements.title.value || form.elements.content.value || form.elements.location_text.value) {
        toast('작성 중인 내용을 유지했어요. 예시는 빈 양식에서 넣을 수 있어요.');
        return;
      }
      form.elements.title.value = '[합성 예시] 공원 산책로의 가로등이 꺼져 있어요';
      form.elements.location_text.value = '데모공원 정문 앞 산책로 (가상 장소)';
      form.elements.content.value = '시연을 위한 합성 민원입니다. 데모공원 정문에서 산책로로 들어가는 길의 가로등 두 개가 저녁에도 켜지지 않습니다. 어두워서 길을 걷기 불편합니다. 조명 상태를 확인해 주세요.';
      dirty = true;
      count();
      form.elements.title.focus();
      toast('가상의 장소와 상황으로 만든 예시를 넣었어요.');
    });
    $('[data-edit]').addEventListener('click', () => {
      clearErrors(form);
      form.elements.consent.checked = false;
      changeStage(1);
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy || stage !== 1) return;
      clearErrors(form);
      busy = true;
      setBusy(form, true);
      const button = $('[data-preview-submit]');
      const original = button.textContent;
      button.textContent = '내용을 확인하고 있어요…';
      try {
        const result = await send(form, '/minwon/preview', payload());
        $('[data-preview-title]').textContent = result.title;
        $('[data-preview-location]').textContent = result.location_text || '별도로 작성하지 않았어요.';
        $('[data-preview-content]').textContent = result.content;
        changeStage(2);
      } catch (error) {
        showError(form, error instanceof TypeError ? '연결을 확인해 주세요. 작성한 내용은 유지됩니다.' : error.message, error.fields);
      } finally {
        busy = false;
        setBusy(form, false);
        button.textContent = original;
      }
    });
    $('[data-final-submit]').addEventListener('click', async () => {
      if (busy || stage !== 2) return;
      clearErrors(form);
      if (!form.elements.consent.checked) {
        showError(form, '데모 접수 안내를 확인하고 동의해 주세요.', { consent: '접수하려면 안내 확인이 필요합니다.' });
        form.elements.consent.focus();
        return;
      }
      busy = true;
      setBusy(form, true);
      const button = $('[data-final-submit]');
      const original = button.textContent;
      button.textContent = '민원을 접수하고 있어요…';
      try {
        const result = await send(form, '/minwon/submit', payload());
        completed = true;
        window.location.assign(result.redirect);
      } catch (error) {
        if (error.fields && ['title', 'content', 'location_text'].some((key) => error.fields[key])) changeStage(1);
        showError(form, error instanceof TypeError ? '연결을 확인할 수 없어요. 작성한 내용은 유지되니 다시 시도해 주세요.' : error.message, error.fields);
        busy = false;
        setBusy(form, false);
        button.textContent = original;
      }
    });
    count();
  }

  const lookup = $('[data-lookup-form]');
  if (lookup) {
    let busy = false;
    lookup.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy) return;
      clearErrors(lookup);
      busy = true;
      setBusy(lookup, true);
      try {
        const result = await send(lookup, '/minwon/lookup', {
          receipt_number: lookup.elements.receipt_number.value,
          lookup_code: lookup.elements.lookup_code.value,
        });
        window.location.assign(result.redirect);
      } catch (error) {
        showError(lookup, error instanceof TypeError ? '연결을 확인해 주세요. 잠시 후 다시 시도해 주세요.' : error.message);
        busy = false;
        setBusy(lookup, false);
      }
    });
  }
})();
