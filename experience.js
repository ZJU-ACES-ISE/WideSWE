(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const table = document.querySelector('.result-details table');
  const sourceRows = [...table.tBodies[0].rows];
  const brand = [
    ['openai.svg', '#46A793'], ['qwen-color.svg', '#9A87D2'],
    ['claude-color.svg', '#D19A85'], ['openai.svg', '#8CBFAF'],
    ['deepseek-color.svg', '#7FA6D5'], ['zai.svg', '#A0ABC1'], ['gemini-color.svg', '#D6B977']
  ];
  // Derive every chart value from the paper's visible table, not a second score list.
  const models = sourceRows.map((row, i) => ({
    model: row.cells[0].textContent, agent: row.cells[1].textContent,
    bugfix: Number(row.cells[2].textContent), feature: Number(row.cells[3].textContent),
    overall: Number(row.cells[4].textContent), logo: brand[i][0], color: brand[i][1]
  }));
  const bars = document.querySelector('#chart-bars');
  const chartNodes = models.map(model => {
    const column = document.createElement('div');
    column.className = 'chart-column';
    column.style.setProperty('--bar-color', model.color);
    const plot = document.createElement('div'); plot.className = 'bar-plot';
    const fill = document.createElement('div'); fill.className = 'bar-fill';
    const score = document.createElement('strong'); score.className = 'bar-score';
    plot.append(fill, score);
    const label = document.createElement('div'); label.className = 'bar-label';
    const logo = document.createElement('img');
    logo.src = `assets/model-logos/${model.logo}`; logo.alt = ''; logo.width = 30; logo.height = 30;
    const name = document.createElement('span'); name.textContent = model.model;
    const agent = document.createElement('small'); agent.textContent = model.agent;
    agent.className = model.agent === 'Codex CLI' ? 'codex-label' : 'claude-label';
    label.append(logo, name, agent); column.append(plot, label); bars.append(column);
    return {column, fill, score};
  });
  let selectedMetric = 'overall';
  function updateChart(metric) {
    selectedMetric = metric;
    document.querySelectorAll('[data-metric]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.metric === metric)));
    models.forEach((model, i) => {
      const rate = model[metric];
      chartNodes[i].column.style.setProperty('--rate', `${rate * 2}%`);
      chartNodes[i].score.textContent = `${rate.toFixed(2)}%`;
      chartNodes[i].column.setAttribute('aria-label', `${model.model}, ${model.agent}: ${rate.toFixed(2)}% task success`);
    });
    const winner = models.reduce((best, model) => model[metric] > best[metric] ? model : best);
    document.querySelector('#chart-sample').textContent = metric === 'overall' ? '120 tasks' : '60 tasks';
    document.querySelector('#chart-summary').textContent = `Highest task success: ${winner.model} with ${winner.agent}, ${winner[metric].toFixed(2)}%.`;
  }
  document.querySelectorAll('[data-metric]').forEach(button => button.addEventListener('click', () => updateChart(button.dataset.metric)));
  document.querySelector('#interactive-results').hidden = false;
  document.querySelector('#static-results').hidden = true;
  updateChart('overall');

  for (const [i, header] of [...table.tHead.rows[0].cells].entries()) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'sort-button'; button.textContent = header.textContent;
    const icon = document.createElement('img'); icon.src = 'assets/arrow-up-down.svg'; icon.alt = ''; icon.width = 14; icon.height = 14;
    button.append(icon); button.title = `Sort by ${header.textContent}`;
    header.replaceChildren(button); header.setAttribute('aria-sort','none');
    button.addEventListener('click', () => {
      const ascending = header.getAttribute('aria-sort') !== 'ascending' && (i < 2 || header.getAttribute('aria-sort') === 'descending');
      for (const th of table.tHead.rows[0].cells) th.setAttribute('aria-sort','none');
      header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
      const sorted = [...sourceRows].sort((a,b) => {
        const x = a.cells[i].textContent, y = b.cells[i].textContent;
        return (i < 2 ? x.localeCompare(y) : Number(x) - Number(y)) * (ascending ? 1 : -1);
      });
      table.tBodies[0].replaceChildren(...sorted);
    });
  }

  const cases = {
    scope: {
      category:'Incomplete scope identification', title:'Sentry SDKs', agent:'Codex CLI · GPT-5.6-sol',
      request:'Implement the same strict trace-continuation policy in the Go, Python, and Ruby SDKs.',
      behavior:'The agent treats Go as the target and reads Python code as a reference, but only Go receives a patch.',
      repositories:[['Go SDK',14,14,'Required checks pass'],['Python SDK',0,22,'Unmodified'],['Ruby SDK',0,13,'Unmodified']],
      takeaway:'Reading a related repository does not ensure that its required changes are delivered.'
    },
    delivery: {
      category:'Recognized work without delivery', title:'Godot / Native', agent:'Codex CLI · GPT-5.6-sol',
      request:'Update the Native SDK API and adapt the Godot caller to use it.',
      behavior:'The agent explicitly identifies the faulty Godot call site, then implements and tests only the Native API.',
      repositories:[['Native SDK',11,11,'Required checks pass'],['Godot SDK',0,1,'Recognized, but unmodified']],
      takeaway:'Knowing where a fix is needed does not guarantee that the corresponding change is delivered.'
    },
    postedit: {
      category:'Post-edit failure', title:'Sentry configuration', agent:'Claude Code · DeepSeek V4 Pro',
      request:'Add log_flush_threshold to the PHP SDK and its Laravel and Symfony integrations. The option must accept null or a positive integer.',
      behavior:'All three repositories are modified, but Symfony uses an integer-only configuration node that rejects the required null setting.',
      repositories:[['PHP SDK',13,13,'Required checks pass'],['Laravel',4,4,'Required checks pass'],['Symfony',22,24,'Required null setting rejected']],
      takeaway:'Modifying every target repository is not enough: each adaptation must preserve the shared requirement.'
    }
  };
  const caseTabs = [...document.querySelectorAll('[data-case]')];
  const panel = document.querySelector('#case-panel');
  function selectCase(key) {
    const data = cases[key];
    for (const tab of caseTabs) {
      const active = tab.dataset.case === key;
      tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
    }
    panel.setAttribute('aria-labelledby',`${key}-tab`);
    for (const field of ['category','title','agent','request','behavior','takeaway']) document.querySelector(`#case-${field}`).textContent = data[field];
    const results = document.querySelector('#case-repositories'); results.replaceChildren();
    for (const [name, passed, total, description] of data.repositories) {
      const item = document.createElement('div'); item.className = `repo-result${passed < total ? ' failed' : ''}`;
      const label = document.createElement('span'); label.textContent = name;
      const rate = document.createElement('strong'); rate.textContent = `${passed} / ${total}`;
      const detail = document.createElement('small'); detail.textContent = description;
      item.append(label,rate,detail); results.append(item);
    }
    if (!reduceMotion.matches) panel.animate([{opacity:.45,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}], {duration:240,easing:'ease-out'});
  }
  caseTabs.forEach((tab, i) => {
    tab.addEventListener('click', () => selectCase(tab.dataset.case));
    tab.addEventListener('keydown', event => {
      let target;
      if (event.key === 'ArrowRight') target = (i + 1) % caseTabs.length;
      if (event.key === 'ArrowLeft') target = (i + caseTabs.length - 1) % caseTabs.length;
      if (event.key === 'Home') target = 0;
      if (event.key === 'End') target = caseTabs.length - 1;
      if (target !== undefined) { event.preventDefault(); caseTabs[target].focus(); selectCase(caseTabs[target].dataset.case); }
    });
  });

  const dialog = document.querySelector('#figure-dialog');
  document.querySelectorAll('[data-zoom]').forEach(link => link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || typeof dialog.showModal !== 'function') return;
    event.preventDefault();
    const img = link.querySelector('img');
    const expanded = document.querySelector('#expanded-figure');
    expanded.src = link.href; expanded.alt = img.alt;
    document.querySelector('#figure-caption').textContent = img.alt;
    dialog.showModal(); document.body.classList.add('figure-open');
  }));
  document.querySelector('#close-figure').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) {
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  }});
  dialog.addEventListener('close', () => document.body.classList.remove('figure-open'));

  const navLinks = [...document.querySelectorAll('nav a[href^="#"]')];
  if ('IntersectionObserver' in window) {
    const activeSections = new Map();
    const navObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => activeSections.set(entry.target.id, entry.isIntersecting));
      const active = navLinks.find(link => activeSections.get(link.hash.slice(1)));
      for (const link of navLinks) {
        if (link === active) link.setAttribute('aria-current','location');
        else link.removeAttribute('aria-current');
      }
    }, {rootMargin:'-12% 0px -50% 0px'});
    navLinks.forEach(link => navObserver.observe(document.querySelector(link.hash)));
  }
  // Content remains visible without JavaScript or with reduced motion enabled.
  if (!reduceMotion.matches && 'IntersectionObserver' in window) {
    document.body.classList.add('motion-enabled');
    const revealObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('revealed'); revealObserver.unobserve(entry.target);
    }), {threshold:.06});
    document.querySelectorAll('.section-heading,.task-flow,.principles,.pipeline,.construction-notes,.case-panel').forEach(el => {
      el.classList.add('reveal'); revealObserver.observe(el);
    });
    const counters = [...document.querySelectorAll('.stats dd')];
    const counterObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      counterObserver.unobserve(entry.target);
      const number = Number(entry.target.textContent); const start = performance.now();
      entry.target.setAttribute('aria-label', String(number));
      function tick(now) {
        const progress = Math.min(1, (now - start) / 900);
        entry.target.textContent = String(Math.round(number * (1 - (1 - progress) ** 3)));
        if (progress < 1 && !reduceMotion.matches) requestAnimationFrame(tick);
        else entry.target.textContent = String(number);
      }
      requestAnimationFrame(tick);
    }), {threshold:.5});
    counters.forEach(counter => counterObserver.observe(counter));
    chartNodes.forEach(node => node.column.style.setProperty('--rate','0%'));
    const chartObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { updateChart(selectedMetric); chartObserver.disconnect(); }
    }), {threshold:.2});
    chartObserver.observe(bars);
    reduceMotion.addEventListener('change', event => {
      if (event.matches) {
        document.body.classList.remove('motion-enabled');
        document.querySelectorAll('.reveal').forEach(el => el.classList.add('revealed'));
        updateChart(selectedMetric);
      }
    });
  }
})();
