'use strict';

const GROUPS = { me: 1, allies: 4, enemies: 5 };
const ATTR_CLASS = { 'Сила': 'str', 'Ловкость': 'agi', 'Интеллект': 'int', 'Универсальный': 'uni' };

const state = {
  heroes: [],
  byName: new Map(),
  slots: { me: [null], allies: Array(4).fill(null), enemies: Array(5).fill(null) },
  active: { group: 'me', idx: 0 },
  attr: 'all',
  query: '',
  situations: [],
  picked: new Set(),
  position: 'auto',
  last: null,
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function initials(name, max = 2) {
  const words = name.replace(/[-']/g, ' ').split(/\s+/).filter((w) => w && !/^(of|the)$/i.test(w));
  if (words.length === 1) return words[0].slice(0, 2);
  return words.slice(0, max).map((w) => w[0].toUpperCase()).join('');
}

function attrClass(attr) {
  return ATTR_CLASS[attr] || '';
}

// Картинка поверх инициалов: если файла нет или он не загрузился, остаются инициалы.
function picture(src) {
  return src ? `<img src="${esc(src)}" alt="" loading="lazy" onerror="this.remove()">` : '';
}

function avatar(hero) {
  const cls = hero ? attrClass(hero.primary_attr) : '';
  return `<div class="avatar ${cls ? 'a-' + cls : ''}">${esc(hero ? initials(hero.name) : '')}${picture(hero && hero.img)}</div>`;
}

function itemIcon(it, order) {
  return `<div class="item-icon ${itemTypeClass(it.type)}">${esc(initials(it.name, 3))}${picture(it.img)}${order ? `<span class="order">${order}</span>` : ''}</div>`;
}

function itemTypeClass(type) {
  const t = type || '';
  if (/потайн/i.test(t)) return 't-secret';
  if (/расходник/i.test(t)) return 't-consumable';
  if (/защит/i.test(t)) return 't-defense';
  if (/ситуатив|контр/i.test(t)) return 't-situational';
  if (/кор/i.test(t)) return 't-core';
  if (/стартов|компонент/i.test(t)) return 't-start';
  return 't-core';
}

function costText(cost) {
  const m = String(cost || '').match(/\d[\d\s]*/);
  return m ? m[0].trim() : '';
}

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), 2600);
}

/* ---------- Драфт ---------- */

function taken() {
  return new Set(Object.values(state.slots).flat().filter(Boolean));
}

function renderSlots() {
  for (const group of Object.keys(GROUPS)) {
    const row = document.querySelector(`.slot-row[data-group="${group}"]`);
    row.innerHTML = state.slots[group].map((name, idx) => {
      const hero = name && state.byName.get(name);
      const active = state.active.group === group && state.active.idx === idx;
      const label = hero ? hero.name : (group === 'me' ? 'Выбрать героя' : 'Пустой слот');
      return `<button class="slot ${hero ? 'filled' : ''} ${active ? 'active' : ''}" data-group="${group}" data-idx="${idx}" title="${esc(label)}" aria-label="${esc(label)}">
        ${hero ? `${avatar(hero)}<span class="slot-name">${esc(hero.name)}</span><span class="slot-remove" data-remove="1" aria-label="Убрать">×</span>` : '<span class="plus">+</span>'}
      </button>`;
    }).join('');
  }
  $('#build').disabled = !state.slots.me[0];
}

function renderGrid() {
  const used = taken();
  const q = state.query.trim().toLowerCase().replace(/[^a-zа-я0-9]/g, '');
  const list = state.heroes.filter((h) =>
    (state.attr === 'all' || h.primary_attr === state.attr) &&
    (!q || h.name.toLowerCase().replace(/[^a-z0-9]/g, '').includes(q)));
  $('#grid').innerHTML = list.length
    ? list.map((h) => `<button class="hero ${used.has(h.name) ? 'taken' : ''}" data-name="${esc(h.name)}" title="${esc(h.name)} — ${esc(h.role)}">
        ${avatar(h)}<span>${esc(h.name)}</span></button>`).join('')
    : '<div class="grid-empty">Герой не найден</div>';
}

/* ---------- Ситуации ---------- */

function suggestions() {
  const enemies = new Set(state.slots.enemies.filter(Boolean));
  const result = new Map();
  for (const s of state.situations) {
    const hits = s.triggers.filter((n) => enemies.has(n));
    if (s.min && hits.length >= s.min) result.set(s.id, hits);
  }
  return result;
}

function renderSituations() {
  const sug = suggestions();
  $('#sits').innerHTML = state.situations.map((s) => {
    const hits = sug.get(s.id);
    const tip = s.description + (hits ? `\nВо вражеском драфте: ${hits.join(', ')}` : '');
    return `<button class="sit ${state.picked.has(s.id) ? 'active' : ''} ${hits ? 'suggested' : ''}" data-id="${s.id}" title="${esc(tip)}" aria-pressed="${state.picked.has(s.id)}">
      ${esc(s.title)}${hits ? '<i class="sit-flag" aria-label="рекомендовано по драфту"></i>' : ''}</button>`;
  }).join('');
  const hint = [...sug].filter(([id]) => !state.picked.has(id))
    .map(([id, hits]) => `<b>${esc(state.situations.find((s) => s.id === id).title)}</b> (${hits.map(esc).join(', ')})`);
  $('#sit-hint').innerHTML = hint.length
    ? `По драфту врагов стоит учесть: ${hint.join(', ')}. <button class="link" id="sit-apply">Выбрать</button>`
    : 'Отметь, как складывается игра, — к билду добавятся предметы под каждую ситуацию.';
  document.querySelectorAll('#position button').forEach((b) => b.classList.toggle('active', b.dataset.pos === state.position));
}

let rebuildTimer;
function rebuildIfShown() {
  renderSituations();
  if (!state.last || !state.last.ok) return;
  clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(() => build({ quiet: true }), 250);
}

function nextEmpty() {
  // Сначала дозаполняем текущую группу, потом остальные по порядку.
  const order = [state.active.group, ...Object.keys(GROUPS).filter((g) => g !== state.active.group)];
  for (const group of order) {
    const idx = state.slots[group].indexOf(null);
    if (idx !== -1) return { group, idx };
  }
  return null;
}

function pick(name) {
  if (taken().has(name)) return toast(`${name} уже в драфте`);
  const { group, idx } = state.active;
  state.slots[group][idx] = name;
  state.active = nextEmpty() || state.active;
  renderSlots();
  renderGrid();
  renderSituations();
}

function setActive(group, idx) {
  state.active = { group, idx };
  renderSlots();
  $('#search').focus({ preventScroll: true });
}

function removeSlot(group, idx) {
  state.slots[group][idx] = null;
  state.active = { group, idx };
  renderSlots();
  renderGrid();
  renderSituations();
}

/* ---------- Результат ---------- */

function heroChip(name) {
  return `<span class="mini">${avatar(state.byName.get(name) || { name })}${esc(name)}</span>`;
}

function itemCard(it, order) {
  const cost = costText(it.cost);
  const against = it.against && it.against.length
    ? `<div class="item-against">${it.against.map((a) => `<span class="vs">против ${esc(a)}</span>`).join('')}</div>` : '';
  return `<button class="item" data-item="${esc(it.name)}">
    ${itemIcon(it, order)}
    <div><div class="item-name">${esc(it.name)}</div><div class="item-reason">${esc(it.reason)}</div>${against}</div>
    ${cost ? `<span class="cost"><i class="coin"></i>${esc(cost)}</span>` : '<span></span>'}
  </button>`;
}

function itemBlock(num, title, items, opts = {}) {
  const sum = items.reduce((s, it) => s + (parseInt(costText(it.cost).replace(/\s/g, ''), 10) || 0), 0);
  const note = opts.note || (sum ? `всего ${sum.toLocaleString('ru-RU')} золота` : '');
  return `<div class="block">
    <div class="block-title"><span class="num"><b>${num}</b></span>${esc(title)}<small>${note}</small></div>
    <div class="items">${items.length ? items.map((it, i) => itemCard(it, opts.ordered ? i + 1 : 0)).join('') : `<div class="mu-none">${esc(opts.empty)}</div>`}</div>
  </div>`;
}

function situationBlock(data) {
  const pos = data.position === 'support' ? 'саппорт 4–5' : 'кор 1–3';
  if (!data.situations.length) {
    return `<div class="block">
      <div class="block-title"><span class="num"><b>4</b></span>По ситуации в игре<small>позиция: ${pos}</small></div>
      <div class="mu-none">Выбери ситуацию в блоке «Ситуация в игре» — здесь появятся предметы под неё.</div>
    </div>`;
  }
  const parts = data.situations.map((s) => `<div class="sit-result">
      <div class="sit-title">${esc(s.title)}</div>
      <p class="sit-desc">${esc(s.description)}</p>
      <div class="items">${s.items.map((it) => itemCard(it)).join('') || '<div class="mu-none">Новых предметов нет.</div>'}</div>
      ${s.already.length ? `<div class="sit-already">Уже в закупе выше: ${s.already.map(esc).join(', ')}</div>` : ''}
    </div>`).join('');
  return `<div class="block">
    <div class="block-title"><span class="num"><b>4</b></span>По ситуации в игре<small>позиция: ${pos}</small></div>
    ${parts}
  </div>`;
}

function renderResult(data) {
  const box = $('#result');
  if (!data.ok) {
    box.innerHTML = `<div class="error-box fade-in">${esc(data.error)}</div>`;
    return;
  }
  const h = data.hero;
  const cls = attrClass(h.primary_attr);
  const mu = (list, kind, title) => `<div class="mu ${kind}">
      <div class="mu-head">${title}</div>
      <div class="mu-list">${list.length ? list.map(heroChip).join('') : '<span class="mu-none">не выявлены</span>'}</div>
    </div>`;
  const team = (title, list) => list.length ? `<span class="tag">${title}: ${list.map(esc).join(', ')}</span>` : '';
  const source = data.llm_used
    ? `<span class="source"><i class="dot" style="background:var(--radiant)"></i>порядок от ${esc(state.model || 'LLM')}</span>`
    : '<span class="source"><i class="dot"></i>порядок по базе</span>';

  box.innerHTML = `<div class="fade-in">
    <div class="banner" style="--a: var(--${cls || 'gold'})">
      ${avatar(h)}
      <div>
        <h3>${esc(h.name)}</h3>
        <div class="banner-meta">
          <span class="tag"><i class="attr ${cls}"></i>${esc(h.primary_attr || '—')}</span>
          <span class="tag">${esc(h.attack_type || '—')}</span>
          <span class="tag">${esc(h.role || '—')}</span>
        </div>
        <p>${esc(h.description)}</p>
      </div>
    </div>
    <div class="block">
      <div class="block-title">Матчапы<small>${team('Союзники', data.allies)} ${team('Враги', data.enemies)}</small></div>
      <div class="matchups">${mu(data.bad, 'bad', 'Опасные')}${mu(data.good, 'good', 'Удобные')}</div>
    </div>
    ${itemBlock(1, 'Стартовый закуп', data.start, { note: 'варианты, не всё сразу', empty: 'Нет данных' })}
    ${itemBlock(2, 'Основные предметы', data.core, { ordered: true, empty: 'Нет подтверждённых предметов в карточке героя.' })}
    ${itemBlock(3, 'Ситуативные', data.situational, { note: source, empty: data.enemies.length ? 'Недостаточно данных для обоснованного выбора.' : 'Добавь врагов, чтобы подобрать ситуативные предметы.' })}
    ${situationBlock(data)}
    <div class="block result-actions">
      <span class="saved">Сохранено в <code>${esc(data.file)}</code></span>
      <button class="btn small" id="copy">Копировать</button>
      <button class="btn small" id="download">Скачать .txt</button>
    </div>
  </div>`;
}

async function build({ quiet = false } = {}) {
  const hero = state.slots.me[0];
  if (!hero) return;
  const box = $('#result');
  if (quiet) {
    box.classList.add('updating');
  } else {
    box.innerHTML = '<div class="loading"><div class="spinner"></div>Анализирую драфт…</div>';
    if (window.innerWidth <= 1100) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  const scroll = box.scrollTop;
  $('#build').disabled = true;
  try {
    const res = await fetch('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hero,
        allies: state.slots.allies.filter(Boolean),
        enemies: state.slots.enemies.filter(Boolean),
        situations: state.situations.map((s) => s.id).filter((id) => state.picked.has(id)),
        position: state.position,
      }),
    });
    const data = await res.json();
    state.last = data;
    renderResult(data);
    box.scrollTop = quiet ? scroll : 0;
  } catch (err) {
    renderResult({ ok: false, error: 'Сервер недоступен. Проверь, что web.py запущен.' });
  } finally {
    box.classList.remove('updating');
    $('#build').disabled = !state.slots.me[0];
  }
}

/* ---------- Карточка предмета ---------- */

async function openItem(name) {
  const dlg = $('#modal');
  $('#modal-content').innerHTML = '<div class="loading" style="padding:50px"><div class="spinner"></div></div>';
  dlg.showModal();
  try {
    const res = await fetch('/api/item?name=' + encodeURIComponent(name));
    const it = await res.json();
    if (it.error) throw new Error(it.error);
    const body = it.text.split('\n')
      .filter((l) => l.trim() && !/^(Предмет|Стоимость|Тип):/.test(l.trim()))
      .map((l) => {
        const t = l.trim();
        const head = t.match(/^-{2,}\s*(.+?)\s*-{2,}$/);
        if (head) return `<h4>${esc(head[1])}</h4>`;
        return `<p>${esc(t.replace(/^-\s*/, ''))}</p>`;
      }).join('');
    const cost = costText(it.cost);
    $('#modal-content').innerHTML = `<div class="modal-head">
        ${itemIcon(it)}
        <div><h3>${esc(it.name)}</h3><div class="sub">${cost ? `<span class="cost"><i class="coin"></i>${esc(cost)}</span>` : ''}<span>${esc(it.type)}</span></div></div>
      </div><div class="card-text">${body}</div>`;
  } catch (err) {
    $('#modal-content').innerHTML = `<div class="error-box" style="margin:0">${esc(err.message)}</div>`;
  }
}

/* ---------- Инициализация ---------- */

function bind() {
  $('.slots').addEventListener('click', (e) => {
    const slot = e.target.closest('.slot');
    if (!slot) return;
    const group = slot.dataset.group;
    const idx = Number(slot.dataset.idx);
    if (e.target.closest('[data-remove]')) removeSlot(group, idx);
    else setActive(group, idx);
  });
  $('.slots').addEventListener('contextmenu', (e) => {
    const slot = e.target.closest('.slot.filled');
    if (!slot) return;
    e.preventDefault();
    removeSlot(slot.dataset.group, Number(slot.dataset.idx));
  });

  $('#grid').addEventListener('click', (e) => {
    const btn = e.target.closest('.hero');
    if (btn && !btn.classList.contains('taken')) pick(btn.dataset.name);
  });

  $('#search').addEventListener('input', (e) => { state.query = e.target.value; renderGrid(); });
  $('#search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && state.query.trim()) {
      const first = $('#grid .hero:not(.taken)');
      if (first) { pick(first.dataset.name); e.target.value = ''; state.query = ''; renderGrid(); }
    } else if (e.key === 'Escape') {
      e.target.value = ''; state.query = ''; renderGrid();
    }
  });

  $('#filters').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    state.attr = chip.dataset.attr;
    document.querySelectorAll('#filters .chip').forEach((c) => c.classList.toggle('active', c === chip));
    renderGrid();
  });

  $('#clear').addEventListener('click', () => {
    for (const g of Object.keys(GROUPS)) state.slots[g].fill(null);
    state.active = { group: 'me', idx: 0 };
    state.picked.clear();
    renderSlots();
    renderGrid();
    renderSituations();
  });

  $('#build').addEventListener('click', () => build());

  $('#sits').addEventListener('click', (e) => {
    const chip = e.target.closest('.sit');
    if (!chip) return;
    const id = chip.dataset.id;
    if (state.picked.has(id)) state.picked.delete(id); else state.picked.add(id);
    rebuildIfShown();
  });
  $('#sit-hint').addEventListener('click', (e) => {
    if (!e.target.closest('#sit-apply')) return;
    suggestions().forEach((_, id) => state.picked.add(id));
    rebuildIfShown();
  });
  $('#position').addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    state.position = btn.dataset.pos;
    rebuildIfShown();
  });
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') build();
  });

  $('#result').addEventListener('click', async (e) => {
    const item = e.target.closest('.item');
    if (item) return openItem(item.dataset.item);
    if (!state.last || !state.last.ok) return;
    if (e.target.closest('#copy')) {
      try { await navigator.clipboard.writeText(state.last.report); toast('Отчёт скопирован'); }
      catch { toast('Не удалось скопировать'); }
    } else if (e.target.closest('#download')) {
      const url = URL.createObjectURL(new Blob([state.last.report], { type: 'text/plain;charset=utf-8' }));
      const a = Object.assign(document.createElement('a'), { href: url, download: state.last.file });
      a.click();
      URL.revokeObjectURL(url);
    }
  });

  const dlg = $('#modal');
  $('#modal-close').addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
}

async function init() {
  bind();
  renderSlots();
  try {
    const meta = await (await fetch('/api/meta')).json();
    state.heroes = meta.heroes;
    state.byName = new Map(meta.heroes.map((h) => [h.name, h]));
    state.model = meta.model;
    state.situations = meta.situations || [];
    $('#st-heroes').innerHTML = `<b>${meta.heroes.length}</b> героев`;
    $('#st-items').innerHTML = `<b>${meta.items_count}</b> предметов`;
    const llm = $('#st-llm');
    const label = { online: `${meta.model}`, offline: 'Ollama не запущена', not_installed: 'Без LLM' }[meta.ollama];
    llm.classList.add(meta.ollama);
    llm.innerHTML = `<i class="dot"></i>${esc(label)}`;
    llm.title = meta.ollama === 'online'
      ? 'Ситуативные предметы ранжирует модель'
      : 'Ранжирование по локальной базе (Ollama недоступна)';
  } catch {
    toast('Не удалось загрузить базу героев');
  }
  renderGrid();
  renderSituations();
}

init();
