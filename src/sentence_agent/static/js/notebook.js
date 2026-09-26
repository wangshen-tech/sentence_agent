// The notebook: Chinese on the left, English on the right. Either side can be covered and each
// sentence flipped open on its own, like a vocabulary app.
import { api } from './api.js';
import { KIND_LABEL, dataFromCard, renderCard } from './cards.js';
import { patchCardInChat } from './chat.js';
import { app, refreshStats } from './state.js';
import { $, $$, dayKey, dayLabel, dueLabel, esc, speakBtn, toast } from './util.js';

const saved = (() => { try { return localStorage.getItem('book-cover'); } catch { return null; } })();
const B = {
  cards: [],
  total: 0,
  cover: ['none', 'zh', 'en'].includes(saved) ? saved : 'none',
  revealed: new Set(),
  shuffle: false,
  order: [],
  filter: 'all',
  query: '',
  detail: null,
  confirmDelete: false,
};

export function initNotebook() {
  $('#book-filter').addEventListener('change', (e) => { B.filter = e.target.value; loadNotebook(); });
  let timer = null;
  $('#book-search').addEventListener('input', (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => { B.query = e.target.value; loadNotebook(); }, 200);
  });
}

export async function loadNotebook() {
  const params = new URLSearchParams({ filter: B.filter, q: B.query, limit: '2000' });
  const data = await api(`/cards?${params}`);
  B.cards = data.cards;
  B.total = data.total;
  if (app.state) app.state.stats = data.stats;
  if (B.shuffle) {
    const known = new Set(B.order);
    B.order = [...B.order, ...B.cards.map((c) => c.id).filter((id) => !known.has(id))];
  }
  renderNotebook();
}

function meterHTML(c) {
  const t = !c.reviews ? '还没复习过' : c.due ? `该复习了 · 熟练度 ${c.level}/5` : `熟练度 ${c.level}/5 · ${dueLabel(c.due_at)}再复习`;
  return `<span class="meter${c.due ? ' due' : ''}" title="${t}" aria-label="${t}">${[1, 2, 3, 4, 5].map((n) => `<i class="${n <= c.level ? 'on' : ''}"></i>`).join('')}</span>`;
}

function cellHTML(c, side) {
  const covered = B.cover === side;
  const key = `${c.id}:${side}`;
  const open = covered && B.revealed.has(key);
  const attrs = covered ? ` role="button" tabindex="0" data-action="flip-cell" data-key="${key}" aria-label="${open ? '遮住' : '查看'}${side === 'zh' ? '中文' : '英文'}"` : '';
  const text = side === 'zh' ? c.zh : c.en;
  return `<div class="cell ${side}${covered ? (open ? ' peek' : ' covered') : ''}"${attrs}><span class="txt">${esc(text)}</span></div>`;
}

function rowHTML(c) {
  return `<div class="row">${cellHTML(c, 'zh')}${cellHTML(c, 'en')}
    <div class="side"><span class="chip k-${c.kind}">${KIND_LABEL[c.kind]}</span>${meterHTML(c)}${speakBtn(c.en)}
      <button class="btn sm ghost" data-action="open-card" data-id="${c.id}">详情</button></div></div>`;
}

function statLine() {
  if (!B.cards.length) return '';
  const opened = B.cover === 'none' ? 0 : B.cards.filter((c) => B.revealed.has(`${c.id}:${B.cover}`)).length;
  return `共 ${B.total} 句` + (B.cover !== 'none' ? ` · 已翻开 ${opened} · 点格子翻开，再点一次遮回去` : '');
}

export function renderNotebook() {
  const s = app.state && app.state.stats;
  $('#book-sum').textContent = s && s.total ? `共 ${s.total} 句 · ${s.due} 句该复习 · ${s.known} 句已记住` : '';
  $('#book-review').textContent = s && s.due ? `开始复习 · ${Math.min(s.due, 20)} 句` : '开始复习';
  $$('#view-book .seg button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.cover === B.cover)));
  $('#book-shuffle').setAttribute('aria-pressed', String(B.shuffle));
  $('#book-shuffle').textContent = B.shuffle ? '按日期排' : '打乱顺序';

  const list = $('#book-list');
  if (!B.cards.length) {
    const searching = B.query.trim() || B.filter !== 'all';
    list.innerHTML = `<p class="empty">${searching ? '没有符合条件的句子。' : '句子本还是空的。去「对话」里问一句中文或英文，它会自动存到这里。'}</p>`;
  } else if (B.shuffle) {
    const pos = new Map(B.order.map((id, i) => [id, i]));
    const sorted = [...B.cards].sort((a, b) => (pos.get(a.id) ?? 1e9) - (pos.get(b.id) ?? 1e9));
    list.innerHTML = sorted.map(rowHTML).join('');
  } else {
    const groups = new Map();
    for (const c of B.cards) {
      const k = dayKey(c.created_at);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(c);
    }
    list.innerHTML = [...groups.values()].map((cs) => `<div class="day-h">${dayLabel(cs[0].created_at)} <small>${cs.length} 句</small></div>${cs.map(rowHTML).join('')}`).join('');
  }
  $('#book-stat').textContent = statLine();
  $('#book-recover').hidden = B.cover === 'none' || !B.cards.some((c) => B.revealed.has(`${c.id}:${B.cover}`));
}

export function setCover(cover) {
  B.cover = cover;
  B.revealed.clear();
  try { localStorage.setItem('book-cover', cover); } catch { /* ignore */ }
  renderNotebook();
}

export function flipCell(el) {
  const key = el.dataset.key;
  if (B.revealed.has(key)) B.revealed.delete(key); else B.revealed.add(key);
  const open = B.revealed.has(key);
  el.classList.toggle('covered', !open);
  el.classList.toggle('peek', open);
  el.setAttribute('aria-label', (open ? '遮住' : '查看') + (key.endsWith(':zh') ? '中文' : '英文'));
  $('#book-stat').textContent = statLine();
  $('#book-recover').hidden = !B.cards.some((c) => B.revealed.has(`${c.id}:${B.cover}`));
}

export function recoverAll() {
  B.revealed.clear();
  renderNotebook();
}

export function toggleShuffle() {
  B.shuffle = !B.shuffle;
  if (B.shuffle) {
    const ids = B.cards.map((c) => c.id);
    for (let i = ids.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [ids[i], ids[j]] = [ids[j], ids[i]];
    }
    B.order = ids;
  }
  renderNotebook();
}

// ---------- detail drawer ----------

export async function openCard(id) {
  try {
    B.detail = await api(`/cards/${id}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  B.confirmDelete = false;
  renderDrawer();
  $('#drawer').hidden = false;
  $('.drawer-panel').scrollTop = 0;
}

export function closeDrawer() {
  B.detail = null;
  $('#drawer').hidden = true;
}

function renderDrawer() {
  const c = B.detail;
  if (!c) return;
  const reviewed = c.reviews ? `复习过 ${c.reviews} 次，熟练度 ${c.level}/5，${c.due ? '现在该复习了' : dueLabel(c.due_at) + '再复习'}。` : '还没复习过。';
  $('#drawer-body').innerHTML = `
    ${renderCard(c.kind, dataFromCard(c), { state: 'saved', cardId: c.id, pinned: c.en })}
    <div class="edit-grid">
      <label>句子本里的中文<textarea id="edit-zh" rows="2">${esc(c.zh)}</textarea></label>
      <label>句子本里的英文<textarea id="edit-en" rows="2" class="en">${esc(c.en)}</textarea></label>
      <div class="edit-actions">
        <button class="btn primary" data-action="save-card" data-id="${c.id}">保存修改</button>
        <button class="btn ${B.confirmDelete ? 'danger' : ''}" data-action="delete-card" data-id="${c.id}">${B.confirmDelete ? '确认从句子本删除' : '从句子本删除'}</button>
      </div>
    </div>
    <p class="review-state">${reviewed} 存于 ${dayLabel(c.created_at)}。</p>`;
}

export async function pinEnglish(id, en) {
  const card = await api(`/cards/${id}`, { method: 'PATCH', body: { en } });
  toast('句子本里换成了这句');
  patchCardInChat(id, { en: card.en });
  if (B.detail && B.detail.id === id) { B.detail = card; renderDrawer(); }
  const row = B.cards.find((c) => c.id === id);
  if (row) { row.en = card.en; renderNotebook(); }
}

export async function saveCardEdits(id) {
  const zh = $('#edit-zh').value.trim();
  const en = $('#edit-en').value.trim();
  if (!zh || !en) { toast('中文和英文都不能是空的'); return; }
  const card = await api(`/cards/${id}`, { method: 'PATCH', body: { zh, en } });
  B.detail = card;
  renderDrawer();
  patchCardInChat(id, { zh: card.zh, en: card.en });
  const row = B.cards.find((c) => c.id === id);
  if (row) { Object.assign(row, { zh: card.zh, en: card.en }); renderNotebook(); }
  toast('已保存');
}

export async function deleteCard(id) {
  if (!B.confirmDelete) {
    B.confirmDelete = true;
    renderDrawer();
    setTimeout(() => { if (B.confirmDelete && B.detail && B.detail.id === id) { B.confirmDelete = false; renderDrawer(); } }, 4000);
    return;
  }
  await api(`/cards/${id}`, { method: 'DELETE' });
  closeDrawer();
  patchCardInChat(id, null);
  B.cards = B.cards.filter((c) => c.id !== id);
  B.total = Math.max(0, B.total - 1);
  await refreshStats();
  renderNotebook();
  toast('已从句子本删除');
}

export async function exportCsv() {
  const { path, count } = await api('/export', { method: 'POST' });
  toast(`导出了 ${count} 句，文件在桌面：${path.split('/').pop()}`);
}
