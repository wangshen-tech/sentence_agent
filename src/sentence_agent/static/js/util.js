import { api } from './api.js';

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const arr = (v) => (Array.isArray(v) ? v : []);
export const str = (v) => (typeof v === 'string' ? v : v == null ? '' : String(v));

// A deliberately small Markdown subset: paragraphs, **bold**, `code`, and lists.
export function md(src) {
  const inline = (t) => t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
  let html = '';
  let list = null;
  let para = [];
  const flush = () => {
    if (para.length) { html += `<p>${inline(para.join('<br>'))}</p>`; para = []; }
  };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of esc(src).split('\n')) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    const num = line.match(/^\s*\d+[.)、]\s+(.*)/);
    if (bullet || num) {
      flush();
      const kind = bullet ? 'ul' : 'ol';
      if (list !== kind) { closeList(); html += `<${kind}>`; list = kind; }
      html += `<li>${inline((bullet || num)[1])}</li>`;
      continue;
    }
    closeList();
    if (!line.trim()) { flush(); continue; }
    para.push(line.replace(/^#+\s*/, ''));
  }
  flush();
  closeList();
  return html;
}

export function roleVar(role) {
  const r = role || '';
  if (r.includes('从句')) return '--r-clause';
  if (r.includes('主语')) return '--r-subj';
  if (r.includes('谓语') || r.includes('系动词')) return '--r-verb';
  if (r.includes('补')) return '--r-comp';
  if (r.includes('宾语')) return '--r-obj';
  if (r.includes('表语')) return '--r-comp';
  if (r.includes('状语')) return '--r-adv';
  if (r.includes('定语') || r.includes('同位')) return '--r-attr';
  return '--r-other';
}

// Word-level diff (LCS) for showing what a correction changed.
function tokens(s) {
  return (s.match(/\s+|[A-Za-z0-9'’-]+|[^\sA-Za-z0-9'’-]/g) || []).map((t) => (/^\s+$/.test(t) ? ' ' : t));
}
export function diffWords(a, b) {
  const A = tokens(a), B = tokens(b), n = A.length, m = B.length;
  if (n * m > 90000) return { a: esc(a), b: esc(b) };
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const wrap = (tag, t) => (t === ' ' ? ' ' : `<${tag}>${esc(t)}</${tag}>`);
  let i = 0, j = 0, oa = '', ob = '';
  while (i < n && j < m) {
    if (A[i] === B[j]) { oa += esc(A[i]); ob += esc(B[j]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) oa += wrap('del', A[i++]);
    else ob += wrap('ins', B[j++]);
  }
  while (i < n) oa += wrap('del', A[i++]);
  while (j < m) ob += wrap('ins', B[j++]);
  return { a: oa, b: ob };
}

const pad = (n) => String(n).padStart(2, '0');
export const dayKey = (ts) => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
export function dayLabel(ts) {
  const key = dayKey(ts);
  const now = Date.now() / 1000;
  if (key === dayKey(now)) return '今天';
  if (key === dayKey(now - 86400)) return '昨天';
  const d = new Date(ts * 1000);
  const wd = '日一二三四五六'[d.getDay()];
  return `${d.getFullYear() !== new Date().getFullYear() ? d.getFullYear() + '年' : ''}${d.getMonth() + 1}月${d.getDate()}日 周${wd}`;
}
export const timeLabel = (ts) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
export function dueLabel(ts) {
  const start = (t) => { const d = new Date(t * 1000); d.setHours(0, 0, 0, 0); return d.getTime(); };
  const days = Math.round((start(ts) - start(Date.now() / 1000)) / 86400000);
  return days <= 0 ? '今天' : days === 1 ? '明天' : `${days} 天后`;
}

let toastTimer = null;
export function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2400);
}

export function speak(text) {
  api('/speak', { method: 'POST', body: { text } }).catch(() => toast('朗读不了'));
}

export async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    await api('/copy', { method: 'POST', body: { text } });
  }
  toast('已复制');
}

export const ICON = {
  speak: '<svg viewBox="0 0 16 16"><path d="M2.5 6v4h2.5l3.5 3V3L5 6z"/><path d="M11 5.5a3.5 3.5 0 0 1 0 5M12.8 3.6a6 6 0 0 1 0 8.8"/></svg>',
  copy: '<svg viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 3.5v-.2A1.3 1.3 0 0 0 9.2 2H3.3A1.3 1.3 0 0 0 2 3.3v5.9a1.3 1.3 0 0 0 1.3 1.3h.2"/></svg>',
  trash: '<svg viewBox="0 0 16 16"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5"/></svg>',
};
export const speakBtn = (t) => (t ? `<button class="icon-btn" data-action="speak" data-text="${esc(t)}" title="朗读" aria-label="朗读">${ICON.speak}</button>` : '');
export const copyBtn = (t) => (t ? `<button class="icon-btn" data-action="copy" data-text="${esc(t)}" title="复制" aria-label="复制">${ICON.copy}</button>` : '');

// Keep the open/closed state of <details> blocks across a re-render.
export function setHTMLKeepingOpen(el, html) {
  const open = new Set($$('details[data-key]', el).filter((d) => d.open).map((d) => d.dataset.key));
  const closed = new Set($$('details[data-key]', el).filter((d) => !d.open).map((d) => d.dataset.key));
  el.innerHTML = html;
  for (const d of $$('details[data-key]', el)) {
    if (open.has(d.dataset.key)) d.open = true;
    else if (closed.has(d.dataset.key)) d.open = false;
  }
}
