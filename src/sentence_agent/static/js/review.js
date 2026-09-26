// Flashcard review with spaced repetition. "记住了" pushes a card further out (1, 3, 7, 16, 35 days);
// "还不熟" brings it back later in this round and again tomorrow.
import { api } from './api.js';
import { refreshStats } from './state.js';
import { $, esc, speakBtn, toast } from './util.js';

const ROUND = 20;
const SCOPES = [['due', '该复习的'], ['today', '今天存的'], ['weak', '还不熟的'], ['all', '全部随机']];
const saved = (() => { try { return localStorage.getItem('review-dir'); } catch { return null; } })();

let R = { stage: 'setup', dir: saved === 'en' ? 'en' : 'zh', scope: 'due', counts: null };

export async function openReview() {
  R = { stage: 'setup', dir: R.dir, scope: R.scope, counts: null };
  render();
  const { counts } = await api('/review/counts');
  R.counts = counts;
  if (!counts[R.scope]) R.scope = SCOPES.map(([k]) => k).find((k) => counts[k]) || 'due';
  render();
}

export function isReviewing() {
  return R.stage === 'card';
}

export function setDirection(dir) {
  R.dir = dir;
  try { localStorage.setItem('review-dir', dir); } catch { /* ignore */ }
  render();
}

export function setScope(scope) {
  R.scope = scope;
  render();
}

export async function startReview() {
  const { cards } = await api(`/review?scope=${R.scope}&limit=${ROUND}`);
  if (!cards.length) { toast('这里没有可以复习的句子'); return; }
  R = { ...R, stage: 'card', cards: new Map(cards.map((c) => [c.id, c])), queue: cards.map((c) => c.id), i: 0, flipped: false, good: 0, again: 0, seen: new Set() };
  render();
}

export function flip() {
  if (R.stage !== 'card') return;
  R.flipped = !R.flipped;
  const card = $('#rv-body .flash');
  if (card) card.classList.toggle('flipped', R.flipped);
  const actions = $('#rv-actions');
  if (actions) actions.innerHTML = actionsHTML();
}

export async function grade(remembered) {
  if (R.stage !== 'card' || !R.flipped) return;
  const id = R.queue[R.i];
  R.seen.add(id);
  if (remembered) R.good++;
  else {
    R.again++;
    if (R.queue.indexOf(id, R.i + 1) < 0) R.queue.push(id);
  }
  R.i++;
  R.flipped = false;
  if (R.i >= R.queue.length) R.stage = 'done';
  render();
  try {
    await api(`/review/${id}`, { method: 'POST', body: { remembered } });
  } catch (e) {
    toast(e.message);
  }
  if (R.stage === 'done') {
    const stats = await refreshStats();
    R.left = stats.due;
    render();
  }
}

function actionsHTML() {
  return R.flipped
    ? '<button class="btn rv-again" data-action="grade" data-remembered="0">还不熟 <kbd>1</kbd></button><button class="btn primary" data-action="grade" data-remembered="1">记住了 <kbd>2</kbd></button>'
    : '<button class="btn primary only" data-action="flip-card">翻面 <kbd>空格</kbd></button>';
}

function render() {
  const body = $('#rv-body');
  const total = R.queue ? R.queue.length : 0;
  $('#rv-count').textContent = R.stage === 'card' ? `${R.i + 1} / ${total}` : '';
  $('#rv-bar').style.width = R.stage === 'card' ? `${(R.i / total) * 100}%` : R.stage === 'done' ? '100%' : '0%';

  if (R.stage === 'setup') {
    const counts = R.counts;
    const n = counts ? Math.min(counts[R.scope] || 0, ROUND) : 0;
    body.innerHTML = `<h2>复习句子本</h2>
      <div class="rv-field"><span class="rv-label">怎么练</span><div class="seg">
        <button data-action="review-dir" data-dir="zh" aria-pressed="${R.dir === 'zh'}">看中文，说英文</button>
        <button data-action="review-dir" data-dir="en" aria-pressed="${R.dir === 'en'}">看英文，想中文</button></div></div>
      <div class="rv-field"><span class="rv-label">练哪些</span><div class="rv-scopes">${SCOPES.map(([k, label]) => {
        const c = counts ? counts[k] : null;
        return `<button class="rv-scope" data-action="review-scope" data-scope="${k}" aria-pressed="${R.scope === k}" ${c === 0 ? 'disabled' : ''}><b>${label}</b><span>${c == null ? '…' : c + ' 句'}</span></button>`;
      }).join('')}</div></div>
      <button class="btn primary lg" data-action="start-review" ${n ? '' : 'disabled'}>开始${n ? ` · ${n} 句` : ''}</button>
      <p class="rv-tip">每轮最多 ${ROUND} 句。翻面后选「记住了」，这句会隔 1、3、7、16、35 天再出现；选「还不熟」，这一轮结束前会再考你一次，明天也会再出现。</p>`;
    return;
  }

  if (R.stage === 'done') {
    body.innerHTML = `<div class="rv-done"><h2>这一轮练完了</h2>
      <p>练了 ${R.seen.size} 句，一次记住 ${R.good} 次，还不熟 ${R.again} 次。</p>
      ${R.left == null ? '' : R.left ? `<p>还有 ${R.left} 句该复习。</p>` : '<p>今天该复习的都练完了。</p>'}
      <div class="rv-done-actions">${R.left ? '<button class="btn primary lg" data-action="review-again">再来一轮</button>' : ''}
        <button class="btn lg" data-action="nav" data-view="book">回到句子本</button></div></div>`;
    return;
  }

  const c = R.cards.get(R.queue[R.i]);
  const front = R.dir === 'zh' ? { k: '中文', t: c.zh, cls: 'zh' } : { k: 'ENGLISH', t: c.en, cls: 'en' };
  const back = R.dir === 'zh' ? { k: 'ENGLISH', t: c.en, cls: 'en' } : { k: '中文', t: c.zh, cls: 'zh' };
  body.innerHTML = `<div class="flash${R.flipped ? ' flipped' : ''}" data-action="flip-card" role="button" tabindex="0" aria-label="翻面">
      <div class="flash-inner">
        <div class="face front"><span class="face-k">${front.k}</span>${front.cls === 'en' ? `<div class="actions">${speakBtn(front.t)}</div>` : ''}
          <p class="face-main ${front.cls}">${esc(front.t)}</p>
          <span class="face-hint">${R.dir === 'zh' ? '先自己说一遍英文，再点一下翻面' : '想想中文意思，再点一下翻面'}</span></div>
        <div class="face back"><span class="face-k">${back.k}</span><div class="actions">${speakBtn(c.en)}</div>
          <p class="face-sub ${front.cls}">${esc(front.t)}</p><p class="face-main ${back.cls}">${esc(back.t)}</p></div>
      </div>
    </div>
    <div class="rv-actions" id="rv-actions">${actionsHTML()}</div>
    <div class="rv-meta"><button class="link" data-action="open-card" data-id="${c.id}">看这句的完整解析</button><button class="link" data-action="end-review">结束这一轮</button></div>`;
}

export function endReview() {
  R.stage = 'done';
  render();
  refreshStats().then((s) => { R.left = s.due; render(); }).catch(() => {});
}
