// The chat view: conversation list, transcript, composer, and the live streaming turn.
import { api, streamChat } from './api.js';
import { KIND_BY_TOOL, notebookEnglish, renderCard } from './cards.js';
import { app, refreshStats } from './state.js';
import { $, arr, dayLabel, esc, md, setHTMLKeepingOpen, timeLabel, toast } from './util.js';

const C = {
  id: null,
  items: [],
  conversations: [],
  live: null,        // { blocks, committed, done, meta }
  controller: null,
  confirmDelete: null,
};

const EXAMPLES = [
  ['中文 → 地道英文', '这事儿我得再考虑考虑。'],
  ['弄懂一句英文', "I'd rather not get into it right now."],
  ['检查我的英文', 'I very like this movie, it make me cry. 这样说对吗？'],
  ['问问题', '“hang out” 和 “hang around” 有什么区别？'],
];

export function initChat() {
  const form = $('#composer');
  const input = $('#composer-input');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    send(input.value);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      send(input.value);
    }
  });
  input.addEventListener('input', autoGrow);
}

function autoGrow() {
  const input = $('#composer-input');
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 220) + 'px';
}

export async function loadThreads() {
  const { conversations } = await api('/conversations');
  C.conversations = conversations;
  renderThreads();
}

function renderThreads() {
  const el = $('#threads');
  if (!C.conversations.length) {
    el.innerHTML = '<p class="threads-empty">还没有对话</p>';
    return;
  }
  el.innerHTML = C.conversations.map((c) => {
    const confirming = C.confirmDelete === c.id;
    return `<div class="thread-row">
      <button class="thread" data-action="open-thread" data-id="${c.id}" aria-current="${c.id === C.id}">
        <span class="thread-title">${esc(c.title || '新对话')}</span>
        <span class="thread-meta">${dayLabel(c.updated_at)}${c.card_count ? ` · ${c.card_count} 句` : ''}</span>
      </button>
      ${c.id === C.id && c.message_count ? `<button class="btn sm ghost ${confirming ? 'danger' : ''}" data-action="delete-thread" data-id="${c.id}" style="margin:0 0 6px 10px">${confirming ? '确认删除这段对话' : '删除对话'}</button>` : ''}
    </div>`;
  }).join('');
}

export async function openConversation(id, { stickToBottom = true } = {}) {
  const data = await api(`/conversations/${id}`);
  C.id = id;
  C.items = data.items;
  renderThreads();
  render();
  if (stickToBottom) scrollToBottom();
  const long = data.context_tokens >= 120000;
  setNotice(long ? '这段对话已经很长了，每次提问都要带上前面的内容，费用会变高。开一个新对话会更省。' : '', long);
}

export async function newConversation() {
  const { id } = await api('/conversations', { method: 'POST' });
  await loadThreads();
  await openConversation(id);
  $('#composer-input').focus();
}

export async function deleteConversation(id) {
  if (C.confirmDelete !== id) {
    C.confirmDelete = id;
    renderThreads();
    setTimeout(() => { if (C.confirmDelete === id) { C.confirmDelete = null; renderThreads(); } }, 4000);
    return;
  }
  C.confirmDelete = null;
  await api(`/conversations/${id}`, { method: 'DELETE' });
  const { conversations } = await api('/conversations');
  C.conversations = conversations;
  if (conversations.length) await openConversation(conversations[0].id);
  else await newConversation();
  toast('已删除这段对话（句子本里的句子还在）');
}

function setNotice(text, withNewButton) {
  const el = $('#chat-notice');
  el.hidden = !text;
  el.innerHTML = text ? `<span>${esc(text)}</span>${withNewButton ? '<button class="btn sm" data-action="new-chat">开新对话</button>' : ''}` : '';
}

// ---------- rendering ----------

function nearBottom() {
  const el = $('#chat-scroll');
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
}
function scrollToBottom() {
  const el = $('#chat-scroll');
  el.scrollTop = el.scrollHeight;
}

function welcomeHTML() {
  const s = app.state;
  const setup = s && !s.has_key ? `<div class="setup-box">
      <h3>先设置一个服务商</h3>
      <p>这个软件通过大模型的 API 工作，按用量付费（和各家的会员订阅分开计费）。下面几种都可以：</p>
      <ol>
        <li><b>Anthropic 官方</b>：在 console.anthropic.com 创建 key。功能最全。</li>
        <li><b>其他服务商</b>：OpenAI、DeepSeek、通义千问、Kimi、智谱、硅基流动、OpenRouter、Gemini 等，选好预设、填 key 就行。</li>
        <li><b>中转站</b>：填中转站给的接口地址和 key，OpenAI 格式和 Anthropic 格式都支持。</li>
      </ol>
      <p>key 会存进 Mac 的钥匙串，不会写进任何文件。</p>
      <button class="btn primary" data-action="nav" data-view="settings">去设置</button>
    </div>` : '';
  return `<div class="welcome">
    <h2>今天想说哪句话？</h2>
    <p>发中文，给你母语者会怎么说；发英文，帮你弄懂意思和结构；发你自己写的英文，帮你看看对不对、地道不地道。每一句都会自动存进句子本。</p>
    <div class="examples">${EXAMPLES.map(([k, t]) => `<button class="example" data-action="example" data-text="${esc(t)}"><b>${k}</b><span>${esc(t)}</span></button>`).join('')}</div>
    ${setup}
  </div>`;
}

function toolChip(b) {
  const r = b.result && typeof b.result === 'object' ? b.result : {};
  const q = b.input && b.input.query ? `“${esc(b.input.query)}”` : '';
  if (b.is_error) return `<span class="tool-chip err">工具出错：${esc(typeof b.result === 'string' ? b.result.slice(0, 80) : b.name)}</span>`;
  if (b.name === 'search_notebook') return `<span class="tool-chip">查了句子本 ${q}${r.count != null ? ` · 找到 ${r.count} 条` : '…'}</span>`;
  if (b.name === 'remember_weak_point') return `<span class="tool-chip">记下了一个易错点：${esc((b.input && b.input.topic) || '')}${r.count > 1 ? `（第 ${r.count} 次）` : ''}</span>`;
  if (b.name === 'get_study_overview') return '<span class="tool-chip">看了你的学习概况</span>';
  return `<span class="tool-chip">${esc(b.name)}</span>`;
}

function blockHTML(b, idx, live) {
  if (b.type === 'thinking') {
    const streaming = live && b.live;
    return `<details class="think" data-key="think-${idx}"><summary>${streaming ? '思考中…' : '思考过程'}</summary><div class="md">${md(b.text)}</div></details>`;
  }
  if (b.type === 'text') return `<div class="md">${md(b.text)}</div>`;
  if (b.type === 'notice') return `<p class="notice-line">${esc(b.text)}</p>`;
  if (b.type === 'error') {
    const toSettings = ['no_key', 'auth', 'permission', 'model'].includes(b.code);
    return `<div class="error-box"><span>${esc(b.message)}</span>${toSettings ? '<button class="btn sm" data-action="nav" data-view="settings">去设置</button>' : ''}</div>`;
  }
  if (b.type === 'tool') {
    const kind = KIND_BY_TOOL[b.name];
    if (!kind) return toolChip(b);
    let state = 'streaming';
    let cardId = null;
    let pinned = null;
    if (b.card) { state = 'saved'; cardId = b.card.id; pinned = b.card.en; }
    else if (b.result && b.result.card_id && !b.is_error) {
      state = b.stored ? 'deleted' : 'saved';
      cardId = b.result.card_id;
      pinned = notebookEnglish(kind, b.input || {});
    } else if (b.is_error) state = 'error';
    else if (!live || b.final) state = live ? 'streaming' : 'error';
    return renderCard(kind, b.input, { state, cardId, pinned, showOpen: true });
  }
  return '';
}

function assistantHTML(blocks, live) {
  return blocks.map((b, i) => blockHTML(b, i, live)).join('');
}

function itemHTML(item) {
  if (item.role === 'user') return `<div class="msg-user">${esc(item.text)}</div>`;
  if (item.role === 'ephemeral') return `<div class="msg-assistant">${assistantHTML(item.blocks, false)}</div>`;
  const blocks = item.blocks.map((b) => (b.type === 'tool' && b.result ? { ...b, stored: true } : b));
  return `<div class="msg-assistant" data-turn="${item.id}">${assistantHTML(blocks, false)}<span class="turn-meta">${timeLabel(item.created_at)}</span></div>`;
}

function liveHTML() {
  const L = C.live;
  const waiting = !L.done && (!L.blocks.length || L.blocks.every((b) => b.type === 'thinking' && !b.text));
  return `${assistantHTML(L.blocks, true)}${waiting ? '<span class="typing"><i></i><i></i><i></i></span>' : ''}`;
}

export function render() {
  const el = $('#chat-items');
  if (!C.items.length && !C.live) {
    el.innerHTML = welcomeHTML();
    return;
  }
  const stick = nearBottom();
  setHTMLKeepingOpen(el, C.items.map(itemHTML).join('') + (C.live ? `<div class="msg-assistant" id="live-turn">${liveHTML()}</div>` : ''));
  if (stick) scrollToBottom();
}

let frame = 0;
function renderLive() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    const el = $('#live-turn');
    if (!el || !C.live) return render();
    const stick = nearBottom();
    setHTMLKeepingOpen(el, liveHTML());
    if (stick) scrollToBottom();
  });
}

// ---------- sending ----------

function setBusy(busy) {
  $('#composer-send').hidden = busy;
  $('#composer-stop').hidden = !busy;
  $('#composer-input').disabled = false;
}

function lastBlock(type) {
  const blocks = C.live.blocks;
  const last = blocks[blocks.length - 1];
  if (last && last.type === type) return last;
  const fresh = type === 'thinking' ? { type, text: '', live: true } : { type, text: '' };
  blocks.push(fresh);
  return fresh;
}

function onEvent(ev) {
  const L = C.live;
  const tool = () => L.blocks.find((b) => b.type === 'tool' && b.id === ev.id);
  switch (ev.type) {
    case 'thinking_start': L.blocks.push({ type: 'thinking', text: '', live: true }); break;
    case 'thinking_delta': lastBlock('thinking').text += ev.text; break;
    case 'text_start': L.blocks.push({ type: 'text', text: '' }); break;
    case 'text_delta': lastBlock('text').text += ev.text; break;
    case 'tool_start': L.blocks.push({ type: 'tool', id: ev.id, name: ev.name, input: {} }); break;
    case 'tool_input': { const b = tool(); if (b) { b.input = ev.input; if (ev.final) b.final = true; } break; }
    case 'tool_result': { const b = tool(); if (b) { b.result = ev.result; b.is_error = ev.is_error; } break; }
    case 'message_done':
      L.blocks.forEach((b) => { if (b.type === 'thinking') b.live = false; });
      L.committed = L.blocks.length;
      break;
    case 'retry': L.blocks.length = L.committed; break;
    case 'notice': L.blocks.push({ type: 'notice', text: ev.text }); break;
    case 'error': L.blocks.push({ type: 'error', code: ev.code, message: ev.message }); break;
    case 'done': L.done = true; L.meta = ev; break;
    default: break;
  }
  renderLive();
}

async function send(raw) {
  const text = raw.trim();
  if (!text || (C.live && !C.live.done)) return;
  if (app.state && !app.state.has_key) {
    toast('先在「设置」里填好 API key');
    return;
  }
  const input = $('#composer-input');
  input.value = '';
  autoGrow();
  C.items.push({ role: 'user', text });
  C.live = { blocks: [], committed: 0, done: false };
  render();
  scrollToBottom();
  setBusy(true);
  C.controller = new AbortController();
  let failed = null;
  try {
    for await (const ev of streamChat({ conversation_id: C.id, text }, C.controller.signal)) onEvent(ev);
  } catch (e) {
    if (e.name !== 'AbortError') failed = e.message;
  }
  const keep = C.live.blocks.filter((b) => b.type === 'error' || b.type === 'notice');
  if (failed) keep.push({ type: 'error', message: failed });
  const meta = C.live.meta;
  C.live = null;
  C.controller = null;
  setBusy(false);
  try {
    await openConversation(C.id);
  } catch { render(); }
  if (keep.length) {
    C.items.push({ role: 'ephemeral', blocks: keep });
    render();
    scrollToBottom();
  }
  if (meta && meta.long_conversation) setNotice('这段对话已经很长了，每次提问都要带上前面的内容，费用会变高。开一个新对话会更省。', true);
  loadThreads().catch(() => {});
  refreshStats().catch(() => {});
}

export function stop() {
  if (C.controller) C.controller.abort();
}

export function sendExample(text) {
  $('#composer-input').value = text;
  send(text);
}

export function currentConversationId() {
  return C.id;
}

// Keep chat cards in sync after the notebook changes a card (pin / delete).
export function patchCardInChat(cardId, patch) {
  let changed = false;
  for (const item of C.items) {
    for (const b of arr(item.blocks)) {
      if (b.card && b.card.id === cardId) {
        if (patch === null) b.card = null;
        else Object.assign(b.card, patch);
        changed = true;
      }
    }
  }
  if (changed) render();
}
