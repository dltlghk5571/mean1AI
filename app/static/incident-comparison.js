document.querySelector('[data-comparison-form]')?.addEventListener('submit', (event) => {
  const form = event.currentTarget;
  if (form.dataset.pending === 'true') {
    event.preventDefault();
    return;
  }
  form.dataset.pending = 'true';
  form.querySelector('button').disabled = true;
  form.querySelector('[data-comparison-progress]').hidden = false;
  form.setAttribute('aria-busy', 'true');
});
window.addEventListener('pageshow', () => {
  const form = document.querySelector('[data-comparison-form]');
  if (form?.dataset.pending === 'true') {
    form.dataset.pending = 'false';
    form.querySelector('button').disabled = false;
    form.querySelector('[data-comparison-progress]').hidden = true;
    form.setAttribute('aria-busy', 'false');
  }
});
