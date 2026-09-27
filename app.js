(() => {
  const tasks = window.WIDESWE_TASKS || [];
  const search = document.querySelector('#search');
  const type = document.querySelector('#type');
  const repoCount = document.querySelector('#repo-count');
  const list = document.querySelector('#task-list');
  const previous = document.querySelector('#previous');
  const next = document.querySelector('#next');
  const pageSize = 10;
  let page = 0;
  function render() {
    const query = search.value.trim().toLowerCase();
    const filtered = tasks.filter(task =>
      (!query || `${task.ecosystem} ${task.repositories} ${task.id}`.toLowerCase().includes(query)) &&
      (!type.value || task.category === type.value) &&
      (!repoCount.value || task.count === Number(repoCount.value)));
    const pages = Math.ceil(filtered.length / pageSize);
    page = Math.max(0, Math.min(page, pages - 1));
    list.replaceChildren();
    for (const task of filtered.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = document.createElement('a');
      row.className = 'task-row';
      row.href = `https://github.com/by2003/WideSWE/tree/main/cases/${encodeURIComponent(task.ecosystem)}/${encodeURIComponent(task.id)}`;
      row.setAttribute('aria-label', `${task.ecosystem}: ${task.repositories}, ${task.category}. Open task on GitHub.`);
      for (const [className, content] of [['ecosystem', task.ecosystem], ['repositories', task.repositories], [`task-type ${task.category}`, task.category === 'bugfix' ? 'Bugfix' : 'Feature'], ['repo-count', `${task.count} repos`]]) {
        const span = document.createElement('span');
        span.className = className;
        span.textContent = content;
        row.append(span);
      }
      const icon = document.createElement('img');
      icon.src = 'assets/arrow-up-right.svg'; icon.alt = ''; icon.width = 16; icon.height = 16;
      row.append(icon);
      list.append(row);
    }
    if (!filtered.length) {
      const empty = document.createElement('p');
      empty.className = 'empty'; empty.textContent = 'No matching tasks.'; list.append(empty);
    }
    document.querySelector('#task-count').textContent = `${filtered.length} of ${tasks.length} tasks`;
    document.querySelector('#page-number').textContent = pages ? `${page + 1} / ${pages}` : '0 / 0';
    previous.disabled = page === 0;
    next.disabled = page + 1 >= pages;
  }
  for (const input of [search, type, repoCount]) input.addEventListener('input', () => { page = 0; render(); });
  function changePage(delta) { page += delta; render(); document.querySelector('#tasks').scrollIntoView({block:'start'}); }
  previous.addEventListener('click', () => changePage(-1));
  next.addEventListener('click', () => changePage(1));
  document.querySelector('#copy-citation').addEventListener('click', async () => {
    const code = document.querySelector('#bibtex');
    const status = document.querySelector('#copy-status');
    try {
      await navigator.clipboard.writeText(code.textContent);
      status.textContent = 'BibTeX copied.';
    } catch {
      const range = document.createRange(); range.selectNodeContents(code);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      status.textContent = 'Citation selected. Copy with your browser or keyboard.';
    }
  });
  render();
})();
