// Settings: API key (kept in the macOS Keychain), model, thinking depth, spend, learner memory, data.
import { api } from './api.js';
import { app, refreshState } from './state.js';
import { $, esc, toast } from './util.js';

let msg = null; // { ok: boolean, text: string }
let notes = [];

export async function openSettings() {
  render();
  try {
    await refreshState();
    notes = (await api('/notes')).notes;
  } catch (e) {
    msg = { ok: false, text: e.message };
  }
  render();
}

function money(v) {
  return v < 0.01 && v > 0 ? '< $0.01' : `$${v.toFixed(2)}`;
}

function render() {
  const s = app.state;
  if (!s) { $('#settings').innerHTML = '<p>加载中…</p>'; return; }
  const source = s.key_source === 'keychain' ? '已保存在钥匙串' : s.key_source === 'env' ? '来自环境变量 ANTHROPIC_API_KEY' : '';
  $('#settings').innerHTML = `
    <header class="page-head"><div><h1>设置</h1><p class="page-sub">版本 ${esc(s.version)}</p></div></header>

    <section class="set-sec" style="margin-top:22px">
      <h2>Anthropic API key</h2>
      <p>这个软件通过 Anthropic API 调用 Claude，按用量付费，和 Claude 会员订阅分开计费。在 console.anthropic.com 的 API Keys 页面创建 key，Billing 页面充值。</p>
      <p class="key-status">${s.has_key ? `${source}：<b>${esc(s.key_hint)}</b>` : '还没有设置。'}</p>
      <form class="key-row" id="key-form">
        <input type="password" id="key-input" placeholder="${s.has_key ? '粘贴新的 key 替换' : 'sk-ant-…'}" autocomplete="off" spellcheck="false">
        <button class="btn primary" type="submit">验证并保存</button>
        ${s.key_source === 'keychain' ? '<button class="btn" type="button" data-action="delete-key">删除</button>' : ''}
      </form>
      ${msg ? `<p class="${msg.ok ? 'msg-ok' : 'msg-err'}">${esc(msg.text)}</p>` : ''}
    </section>

    <section class="set-sec">
      <h2>模型</h2>
      <p>价格是每一百万 token 的美元价（输入 / 输出）。查一句话一般只要几美分。</p>
      <div class="options">${s.models.map((m) => `
        <button class="option" data-action="set-model" data-id="${m.id}" aria-pressed="${s.model === m.id}">
          <span class="dot"></span><b>${esc(m.label)}</b><span class="price">$${m.input} / $${m.output}</span><small>${esc(m.note)}</small>
        </button>`).join('')}</div>
    </section>

    <section class="set-sec">
      <h2>思考深度</h2>
      <p>想得越久，讲解越细，但也越慢、越贵。</p>
      <div class="options">${s.efforts.map((e) => `
        <button class="option" data-action="set-effort" data-id="${e.id}" aria-pressed="${s.effort === e.id}">
          <span class="dot"></span><b>${esc(e.label)}</b><span></span><small>${esc(e.note)}</small>
        </button>`).join('')}</div>
    </section>

    <section class="set-sec">
      <h2>花费估算</h2>
      <p>按公开价格和实际用量估算，账单以 Anthropic 控制台为准。</p>
      <div class="cost"><div><b>${money(s.usage.today)}</b><span>今天</span></div><div><b>${money(s.usage.month)}</b><span>这个月</span></div></div>
    </section>

    <section class="set-sec">
      <h2>记下的易错点</h2>
      <p>检查你的英文时，Claude 会把反复出现的问题记在这里，之后讲解会更有针对性。新开的对话会带上这些记录。</p>
      ${notes.length ? `<ul class="notes">${notes.map((n) => `<li>
          <b>${esc(n.topic)} <span class="count">出现 ${n.count} 次</span></b>
          <button class="btn sm ghost" data-action="delete-note" data-id="${n.id}">删除</button>
          <p>${esc(n.note)}${n.example ? `　例：${esc(n.example)}` : ''}</p></li>`).join('')}</ul>` : '<p class="page-sub">还没有记录。</p>'}
    </section>

    <section class="set-sec">
      <h2>数据</h2>
      <p>句子本和对话都存在这台 Mac 上。</p>
      <p class="path">${esc(s.data_dir)}</p>
      <div class="page-actions"><button class="btn" data-action="open-data-dir">在访达中打开</button><button class="btn" data-action="export">导出句子本为 CSV</button></div>
    </section>`;

  $('#key-form').addEventListener('submit', saveKey);
}

async function saveKey(e) {
  e.preventDefault();
  const input = $('#key-input');
  const key = input.value.trim();
  if (!key) return;
  msg = { ok: true, text: '正在验证…' };
  render();
  try {
    const res = await api('/key', { method: 'PUT', body: { api_key: key } });
    msg = { ok: true, text: res.warning || '已验证并保存到钥匙串。' };
    await refreshState();
  } catch (err) {
    msg = { ok: false, text: err.message };
  }
  render();
}

export async function deleteKey() {
  await api('/key', { method: 'DELETE' });
  msg = { ok: true, text: '已从钥匙串删除。' };
  await refreshState();
  render();
}

export async function setModel(id) {
  await api('/settings', { method: 'POST', body: { model: id } });
  app.state.model = id;
  render();
  toast('新的提问会用这个模型');
}

export async function setEffort(id) {
  await api('/settings', { method: 'POST', body: { effort: id } });
  app.state.effort = id;
  render();
}

export async function deleteNote(id) {
  await api(`/notes/${id}`, { method: 'DELETE' });
  notes = notes.filter((n) => n.id !== id);
  render();
}

export async function openDataDir() {
  await api('/open-data-dir', { method: 'POST' });
}
