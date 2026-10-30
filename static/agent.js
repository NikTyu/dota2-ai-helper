'use strict';
// Вкладка «Спросить агента». Использует общие функции из app.js: $, esc, toast, picture, initials, openItem.

const EXAMPLES = [
  'Я играю на Phoenix против Huskar и PA, линию проиграл. Что собрать?',
  'Чем Juggernaut пробить уклонение Phantom Assassin?',
  'Я саппорт Rubick, у врагов Riki и Clinkz. Что купить?',
  'Против кого силён Tinker и что он обычно собирает?',
];
const STAGES = ['Ищу карточки в векторной базе…', 'Собираю факты из графа знаний…', 'Модель выбирает инструменты…',
  'Вызываю инструменты через MCP…', 'Проверяю предметы по базе…', 'Модель пишет ответ…'];

const agentState = { answers: [], busy: false, selected: null, status: null };

/* ---------- Вкладки ---------- */

function showView(view) {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  $('#view-draft').hidden = view !== 'draft';
  $('#view-agent').hidden = view !== 'agent';
  try { history.replaceState(null, '', view === 'agent' ? '#agent' : '#'); } catch { /* превью без истории */ }
  if (view === 'agent') updateDraftSummary();
}

/* ---------- Состояние агента ---------- */

async function pollAgent() {
  try {
    const s = await (await fetch('/api/agent')).json();
    agentState.status = s;
    renderAgentStatus();
    if (s.status === 'loading') setTimeout(pollAgent, 2500);
  } catch {
    setTimeout(pollAgent, 5000);
  }
}

function renderAgentStatus() {
  const s = agentState.status;
  const box = $('#agent-status');
  if (!s) return;
  if (s.status === 'loading') {
    box.innerHTML = `<div class="idx-row"><div class="spinner tiny"></div><span>Загружаю индексы… ${esc(s.log.at(-1) || '')}</span></div>`;
  } else if (s.status !== 'ready') {
    box.innerHTML = `<div class="error-box" style="margin:0">${esc(s.error)}</div>`;
  } else {
    const nodes = s.graph.nodes || {};
    const edges = Object.values(s.graph.edges || {}).reduce((a, b) => a + b, 0);
    box.innerHTML = `<div class="idx-grid">
      <div class="idx"><span>Векторная БД</span><b>${s.vectors}</b><small>карточек · ${esc(s.embedder)}</small></div>
      <div class="idx"><span>Граф знаний</span><b>${(nodes.hero || 0) + (nodes.item || 0)}</b><small>узлов · ${edges} связей</small></div>
      <div class="idx"><span>Модель</span><b class="${s.llm_ready ? 'ok' : 'warn'}">${s.llm_ready ? 'готова' : 'нет'}</b><small>${esc(s.model)}</small></div>
    </div>`;
  }
  $('#agent-model').textContent = s.status === 'ready'
    ? `${s.model} · RAG (${s.embedder}) · инструменты MCP`
    : 'Локальная модель + база знаний';
}

/* ---------- Драфт для агента ---------- */

function currentDraft() {
  const hero = state.slots.me[0];
  if (!hero) return null;
  return { hero, allies: state.slots.allies.filter(Boolean), enemies: state.slots.enemies.filter(Boolean), position: state.position };
}

function updateDraftSummary() {
  const d = currentDraft();
  $('#draft-summary').textContent = d
    ? `${d.hero}${d.enemies.length ? ' против ' + d.enemies.join(', ') : ''}`
    : 'драфт пуст — выбери героя на вкладке «Драфт»';
  $('#use-draft').disabled = !d;
  if (!d) $('#use-draft').checked = false;
}

/* ---------- Вопрос ---------- */

async function askAgent(question) {
  if (agentState.busy || !question.trim()) return;
  if (agentState.status && agentState.status.status !== 'ready') {
    toast(agentState.status.status === 'loading' ? 'Агент ещё загружает индексы' : 'Агент недоступен');
    return;
  }
  agentState.busy = true;
  $('#ask').disabled = true;
  const draft = $('#use-draft').checked ? currentDraft() : null;
  const pending = { id: Date.now(), question, pending: true, draft };
  agentState.answers.unshift(pending);
  renderAnswers();
  let stage = 0;
  const timer = setInterval(() => {
    stage = Math.min(stage + 1, STAGES.length - 1);
    const el = document.querySelector(`[data-answer="${pending.id}"] .stage`);
    if (el) el.textContent = STAGES[stage];
  }, 4500);
  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, draft }),
    });
    const data = await res.json();
    Object.assign(pending, data, { pending: false });
    if (data.ok === false) pending.error = data.error;
  } catch {
    Object.assign(pending, { pending: false, error: 'Сервер недоступен.' });
  } finally {
    clearInterval(timer);
    agentState.busy = false;
    $('#ask').disabled = false;
    agentState.selected = pending.id;
    renderAnswers();
    renderTrace(pending);
  }
}

/* ---------- Отрисовка ответа ---------- */

function inline(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
}

// Простая разметка: заголовки, жирный, списки, абзацы — то, что обычно пишет модель.
function renderMarkdown(text) {
  const out = [];
  let list = null;
  const close = () => { if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; } };
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    const head = line.match(/^#{1,4}\s+(.*)$/);
    if (item) { (list = list || []).push(`<li>${inline(item[1])}</li>`); continue; }
    close();
    if (!line) continue;
    if (head) out.push(`<h4>${inline(head[1])}</h4>`);
    else if (/^\*\*[^*]+\*\*:?$/.test(line)) out.push(`<h4>${inline(line.replace(/:$/, ''))}</h4>`);
    else out.push(`<p>${inline(line)}</p>`);
  }
  close();
  return out.join('');
}

function itemChecks(items) {
  if (!items || !items.length) return '';
  return `<div class="checks">${items.map((it) => `
    <button class="check-item ${it.ok ? 'ok' : 'bad'}" data-item="${esc(it.name)}" title="${esc(it.reason)}">
      <span class="item-icon t-core">${esc(initials(it.name, 3))}${picture(it.img)}</span>
      <span>${esc(it.name)}</span><i>${it.ok ? '✓' : '!'}</i>
    </button>`).join('')}</div>`;
}

function renderAnswers() {
  const box = $('#answers');
  if (!agentState.answers.length) return;
  box.innerHTML = agentState.answers.map((a) => {
    // До ответа показываем драфт из интерфейса, после — что агент распознал в вопросе.
    const label = a.pending ? 'драфт' : 'распознано';
    const draft = a.draft && a.draft.hero
      ? `<span class="tag">${label}: ${esc(a.draft.hero)}${a.draft.enemies.length ? ' vs ' + esc(a.draft.enemies.join(', ')) : ''}</span>` : '';
    let body;
    if (a.pending) {
      body = `<div class="thinking"><div class="spinner tiny"></div><span class="stage">${STAGES[0]}</span></div>`;
    } else if (a.error) {
      body = `<div class="error-box" style="margin:0">${esc(a.error)}</div>`;
    } else {
      const mode = a.mode === 'agent'
        ? `<span class="tag mode-agent">агент · ${esc(a.model)}</span>`
        : '<span class="tag mode-fallback">без LLM</span>';
      body = `<div class="md">${renderMarkdown(a.answer)}</div>
        ${itemChecks(a.items)}
        <div class="answer-meta">${mode}<span class="tag">${a.elapsed} с</span>
          <span class="tag">${a.trace.filter((s) => s.type === 'tool').length} вызова инструментов</span></div>`;
    }
    return `<article class="answer fade-in ${agentState.selected === a.id ? 'selected' : ''}" data-answer="${a.id}">
      <div class="q">${esc(a.question)} ${draft}</div>${body}</article>`;
  }).join('');
}

/* ---------- Ход работы агента ---------- */

function sourceChip(c) {
  const kind = c.kind === 'hero' ? 'hero' : 'item';
  const pic = kind === 'hero'
    ? `<span class="src-pic hero-pic">${picture(c.img)}</span>`
    : `<span class="src-pic">${picture(c.img)}</span>`;
  return `<span class="src">${pic}${esc(c.name)}${c.score != null ? `<small>${c.score.toFixed(2)}</small>` : '<small>по имени</small>'}</span>`;
}

function renderTrace(a) {
  const box = $('#trace-body');
  if (!a || a.pending || a.error) {
    box.innerHTML = a && a.error ? `<div class="error-box" style="margin:0">${esc(a.error)}</div>` : '';
    return;
  }
  const steps = [];
  steps.push(`<li class="step rag"><div class="step-head"><b>1. RAG — поиск в базе знаний</b><small>модуль 2</small></div>
    <div class="step-sub">Векторная БД: карточки, близкие к вопросу</div>
    <div class="srcs">${a.sources.map(sourceChip).join('')}</div>
    ${a.facts.length ? `<div class="step-sub">Граф знаний: факты о распознанных героях</div>
    <ul class="facts">${a.facts.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}
  </li>`);
  const mcp = a.trace.find((s) => s.type === 'mcp');
  if (mcp) {
    steps.push(`<li class="step"><div class="step-head"><b>2. MCP — подключение инструментов</b><small>модуль 3</small></div>
      <div class="tools">${mcp.tools.map((t) => `<code>${esc(t)}</code>`).join('')}</div></li>`);
  }
  a.trace.filter((s) => s.type === 'tool').forEach((s, i) => {
    let pretty = s.result;
    try { pretty = JSON.stringify(JSON.parse(s.result), null, 2); } catch { /* обрезанный JSON показываем как есть */ }
    steps.push(`<li class="step tool ${s.error ? 'err' : ''}"><div class="step-head"><b>Вызов: ${esc(s.name)}</b><small>шаг ${i + 1}</small></div>
      <pre class="args">${esc(JSON.stringify(s.args).replace(/,"/g, ', "').replace(/":/g, '": '))}</pre>
      <details><summary>Результат инструмента</summary><pre>${esc(pretty)}</pre></details></li>`);
  });
  const val = a.trace.find((s) => s.type === 'validation');
  if (val) {
    steps.push(`<li class="step ${val.removed.length ? 'warn' : 'ok'}"><div class="step-head"><b>Проверка ответа по базе</b></div>
      <div class="step-sub">${val.removed.length ? `Модель переписала ответ без: ${esc(val.removed.join(', '))}. ${esc(val.note)}` : esc(val.note)}</div></li>`);
  }
  if (a.mode === 'fallback') {
    steps.push('<li class="step warn"><div class="step-head"><b>Ответ без LLM</b></div><div class="step-sub">Модель недоступна — подбор собран напрямую из базы.</div></li>');
  }
  box.innerHTML = `<ol class="timeline">${steps.join('')}</ol>`;
}

/* ---------- Инициализация ---------- */

function initAgent() {
  $('#examples').innerHTML = EXAMPLES.map((q) => `<button type="button" class="chip example">${esc(q)}</button>`).join('');
  $('#examples').addEventListener('click', (e) => {
    const b = e.target.closest('.example');
    if (b) { $('#question').value = b.textContent; $('#question').focus(); }
  });
  document.querySelector('.tabs').addEventListener('click', (e) => {
    const tab = e.target.closest('.tab');
    if (tab) showView(tab.dataset.view);
  });
  $('#ask-form').addEventListener('submit', (e) => {
    e.preventDefault();
    askAgent($('#question').value);
  });
  $('#question').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); askAgent($('#question').value); }
  });
  $('#answers').addEventListener('click', (e) => {
    const item = e.target.closest('[data-item]');
    if (item) return openItem(item.dataset.item);
    const card = e.target.closest('.answer');
    if (!card) return;
    const a = agentState.answers.find((x) => String(x.id) === card.dataset.answer);
    agentState.selected = a.id;
    document.querySelectorAll('.answer').forEach((el) => el.classList.toggle('selected', el === card));
    renderTrace(a);
  });
  if (location.hash === '#agent') showView('agent');
  pollAgent();
}

initAgent();
