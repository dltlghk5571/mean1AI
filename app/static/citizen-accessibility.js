(() => {
  'use strict';
  const key = 'seongnam.citizen.text-size.v1';
  const sizes = {standard: '기본', large: '크게', extra: '아주 크게'};
  const valid = (value) => Object.hasOwn(sizes, value) ? value : 'standard';
  let selected = 'standard';
  let canRemember = true;
  try { selected = valid(localStorage.getItem(key)); } catch { canRemember = false; }
  // Apply before the first paint. Only a display preference is read or stored.
  document.documentElement.dataset.textSize = selected;

  document.addEventListener('DOMContentLoaded', () => {
    const tools = document.querySelector('[data-reading-tools]');
    const status = tools.querySelector('[data-reading-status]');
    const help = document.querySelector('#reading-help');
    const radios = [...tools.querySelectorAll('input[type="radio"]')];
    const sync = () => {
      document.documentElement.dataset.textSize = selected;
      radios.forEach((radio) => { radio.checked = radio.value === selected; });
      help.textContent = canRemember ? '이 브라우저에 글자 크기를 기억해요.' : '현재 화면에 적용돼요. 브라우저에서 설정을 저장할 수 없어요.';
    };
    sync();
    tools.hidden = false;
    radios.forEach((radio) => radio.addEventListener('change', () => {
      if (!radio.checked) return;
      selected = valid(radio.value);
      try { localStorage.setItem(key, selected); canRemember = true; } catch { canRemember = false; }
      sync();
      status.textContent = `글자 크기: ${sizes[selected]}.${canRemember ? '' : ' 현재 화면에 적용했어요.'}`;
    }));
    window.addEventListener('storage', (event) => {
      if (event.key !== key && event.key !== null) return;
      selected = valid(event.newValue);
      sync();
    });
  });
})();
