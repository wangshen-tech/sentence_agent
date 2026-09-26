// Settings: model providers (any OpenAI-compatible or Anthropic-format endpoint, keys in the Keychain),
// thinking depth, spend, learner memory, data.
import { api } from './api.js';
import { app, refreshState } from './state.js';
import { $, esc, toast } from './util.js';

let notes = [];
let form = null;      // the add/edit provider form, or null
let confirmDelete = null;

export async function openSettings() {
  form = null;
  render();
  try {
    await refreshState();
    notes = (await api('/notes')).notes;
  } catch (e) {
    toast(e.message);
  }
  render();
}

const fmtTokens = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n));
const money = (v) => (v > 0 && v < 0.01 ? '< $0.01' : `$${v.toFixed(2)}`);

function providerRow(p, activeId) {
  const protocol = p.official ? '官方' : p.protocol === 'openai' ? 'OpenAI 格式' : 'Anthropic 格式';
  const keyText = p.has_key ? `key ${esc(p.key_hint)}${p.key_source === 'env' ? '（环境变量）' : ''}` : '<span class="warn-text">还没填 key</span>';
  return `<div class="prov${p.id === activeId ? ' on' : ''}">
    <button class="prov-pick" data-action="provider-activate" data-id="${p.id}" aria-pressed="${p.id === activeId}" ${p.ready ? '' : 'disabled'} title="${p.ready ? '用这个' : '先填好 key 和模型'}"><span class="dot"></span></button>
    <div class="prov-main">
      <b>${esc(p.name)}</b> <span class="chip">${protocol}</span>
      <small>${esc(p.model || '未选模型')} · ${keyText}${p.base_url ? ` · ${esc(p.base_url)}` : ''}</small>
    </div>
    <button class="btn sm" data-action="provider-edit" data-id="${p.id}">编辑</button>
  </div>`;
}

function modelSuggestions() {
  const fromApi = form.models || [];
  const catalog = form.protocol === 'anthropic' && !form.base_url ? app.state.anthropic_models.map((m) => m.id) : [];
  return [...new Set([...catalog, ...fromApi])];
}

function formHTML() {
  const s = app.state;
  const editing = form.mode === 'edit';
  const anthropicOfficial = form.protocol === 'anthropic' && !form.base_url.trim();
  const suggestions = modelSuggestions();
  const quick = suggestions.slice(0, 24);
  const preset = s.presets.find((p) => p.key === form.preset);
  const urlHint = form.protocol === 'openai'
    ? 'OpenAI 格式的地址一般以 /v1 结尾，比如 https://api.example.com/v1'
    : '留空就是 Anthropic 官方接口；中转站填它给的地址，一般不带 /v1';
  return `<div class="prov-form" id="provider-form">
    <h3>${editing ? `编辑「${esc(form.name)}」` : '添加服务商'}</h3>
    ${editing ? '' : `<label class="field"><span>常用服务商</span>
      <select data-field="preset">
        <option value="">自定义…</option>
        ${s.presets.map((p) => `<option value="${p.key}" ${form.preset === p.key ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}
      </select></label>`}
    ${preset && preset.note ? `<p class="field-note">${esc(preset.note)}</p>` : ''}
    <label class="field"><span>名称</span><input type="text" data-field="name" value="${esc(form.name)}" placeholder="比如：我的中转站"></label>
    <div class="field"><span>接口格式</span><div class="seg">
      <button type="button" data-action="provider-protocol" data-protocol="openai" aria-pressed="${form.protocol === 'openai'}">OpenAI 兼容</button>
      <button type="button" data-action="provider-protocol" data-protocol="anthropic" aria-pressed="${form.protocol === 'anthropic'}">Anthropic</button>
    </div></div>
    <label class="field"><span>接口地址</span><input type="text" data-field="base_url" value="${esc(form.base_url)}" placeholder="${form.protocol === 'openai' ? 'https://…/v1' : '留空 = Anthropic 官方'}" spellcheck="false"></label>
    <p class="field-note">${urlHint}</p>
    <label class="field"><span>API key</span><input type="password" data-field="api_key" value="${esc(form.api_key)}" placeholder="${editing && form.has_key ? '已保存在钥匙串，留空表示不修改' : '粘贴 key'}" autocomplete="off" spellcheck="false"></label>
    <div class="field"><span>模型</span>
      <div class="model-row">
        <input type="text" data-field="model" value="${esc(form.model)}" list="model-options" placeholder="${anthropicOfficial ? 'claude-opus-5' : '填服务商提供的模型名'}" spellcheck="false">
        <button type="button" class="btn" data-action="provider-fetch-models" ${form.testing ? 'disabled' : ''}>${form.testing ? '正在连接…' : '测试连接并获取模型'}</button>
      </div>
      <datalist id="model-options">${suggestions.map((m) => `<option value="${esc(m)}">`).join('')}</datalist>
    </div>
    ${form.msg ? `<p class="${form.msg.ok ? 'msg-ok' : 'msg-err'}">${esc(form.msg.text)}</p>` : ''}
    ${quick.length ? `<div class="model-chips">${quick.map((m) => `<button type="button" class="mchip${m === form.model ? ' on' : ''}" data-action="provider-pick-model" data-model="${esc(m)}">${esc(m)}</button>`).join('')}${suggestions.length > quick.length ? `<span class="field-note">还有 ${suggestions.length - quick.length} 个，可以在输入框里搜</span>` : ''}</div>` : ''}
    <p class="field-note">模型要支持工具调用（function calling / tools），不然句子没法自动存进句子本。</p>
    <div class="form-actions">
      <button type="button" class="btn primary" data-action="provider-save" data-activate="1">保存并使用</button>
      <button type="button" class="btn" data-action="provider-save" data-activate="0">只保存</button>
      <button type="button" class="btn ghost" data-action="provider-cancel">取消</button>
      <span class="grow"></span>
      ${editing && form.has_key && form.key_source === 'keychain' ? '<button type="button" class="btn ghost" data-action="provider-delete-key">清除 key</button>' : ''}
      ${editing && s.providers.length > 1 ? `<button type="button" class="btn ${confirmDelete === form.id ? 'danger' : 'ghost'}" data-action="provider-delete">${confirmDelete === form.id ? '确认删除' : '删除服务商'}</button>` : ''}
    </div>
  </div>`;
}

function render() {
  const s = app.state;
  if (!s) { $('#settings').innerHTML = '<p>加载中…</p>'; return; }
  const active = s.providers.find((p) => p.id === s.active_provider);
  const official = active && active.official;
  const unpriced = [...new Set([...s.usage.today.unpriced_models, ...s.usage.month.unpriced_models])];
  $('#settings').innerHTML = `
    <header class="page-head"><div><h1>设置</h1><p class="page-sub">版本 ${esc(s.version)}</p></div></header>

    <section class="set-sec" style="margin-top:22px">
      <h2>服务商与模型</h2>
      <p>可以用 Anthropic 官方接口，也可以用 OpenAI、DeepSeek、通义千问、Kimi 等服务商，或者任何 OpenAI 格式、Anthropic 格式的中转站。可以添加好几个，随时切换，同一段对话里换了也能接着聊。key 都存在 Mac 的钥匙串里。</p>
      <div class="prov-list">${s.providers.map((p) => providerRow(p, s.active_provider)).join('')}</div>
      ${form ? formHTML() : '<button class="btn" data-action="provider-add" style="margin-top:10px">＋ 添加服务商</button>'}
    </section>

    <section class="set-sec">
      <h2>思考深度</h2>
      <p>想得越久，讲解越细，但也越慢、越贵。${official ? '' : '<b>只对 Anthropic 官方接口生效</b>，其他服务商按它们自己的默认方式回答。'}</p>
      <div class="options">${s.efforts.map((e) => `
        <button class="option" data-action="set-effort" data-id="${e.id}" aria-pressed="${s.effort === e.id}">
          <span class="dot"></span><b>${esc(e.label)}</b><span></span><small>${esc(e.note)}</small>
        </button>`).join('')}</div>
    </section>

    <section class="set-sec">
      <h2>用量</h2>
      <p>Claude 官方模型按公开价格估算花费；其他模型只统计 token 数，花费以服务商账单为准。</p>
      <div class="cost">
        <div><b>${money(s.usage.today.cost)}</b><span>今天 · ${fmtTokens(s.usage.today.tokens)} tokens</span></div>
        <div><b>${money(s.usage.month.cost)}</b><span>这个月 · ${fmtTokens(s.usage.month.tokens)} tokens</span></div>
      </div>
      ${unpriced.length ? `<p class="field-note" style="margin-top:8px">没有估算价格的模型：${unpriced.map(esc).join('、')}</p>` : ''}
    </section>

    <section class="set-sec">
      <h2>记下的易错点</h2>
      <p>检查你的英文时，agent 会把反复出现的问题记在这里，之后讲解会更有针对性。新开的对话会带上这些记录。</p>
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
}

// Keep typed values in `form` so re-renders never lose them.
document.addEventListener('input', (e) => {
  const field = e.target.dataset && e.target.dataset.field;
  if (!form || !field || !e.target.closest('#provider-form')) return;
  if (field === 'preset') {
    const preset = app.state.presets.find((p) => p.key === e.target.value);
    form.preset = e.target.value;
    if (preset) Object.assign(form, { name: preset.name, protocol: preset.protocol, base_url: preset.base_url, models: [], msg: null });
    render();
    return;
  }
  form[field] = e.target.value;
});

export function addProvider() {
  form = { mode: 'add', preset: '', name: '', protocol: 'openai', base_url: '', api_key: '', model: '', models: [], msg: null };
  confirmDelete = null;
  render();
  $('#provider-form select, #provider-form input').focus();
}

export function editProvider(id) {
  const p = app.state.providers.find((x) => x.id === id);
  if (!p) return;
  form = { mode: 'edit', id, name: p.name, protocol: p.protocol, base_url: p.base_url, api_key: '', model: p.model, has_key: p.has_key, key_source: p.key_source, models: [], msg: null };
  confirmDelete = null;
  render();
}

export function cancelForm() {
  form = null;
  render();
}

export function setProtocol(protocol) {
  form.protocol = protocol;
  form.models = [];
  render();
}

export function pickModel(model) {
  form.model = model;
  render();
}

export async function fetchModels() {
  form.testing = true;
  form.msg = null;
  render();
  try {
    const res = await api('/providers/models', {
      method: 'POST',
      body: { protocol: form.protocol, base_url: form.base_url, api_key: form.api_key || null, provider_id: form.mode === 'edit' ? form.id : null },
    });
    form.models = res.models;
    form.msg = { ok: true, text: res.count ? `连接成功，找到 ${res.count} 个模型。点一个或在输入框里搜。` : '连接成功，但服务商没有列出模型，手填模型名就行。' };
  } catch (e) {
    form.msg = { ok: false, text: e.message };
  }
  form.testing = false;
  render();
}

export async function saveProvider(activate) {
  const body = { name: form.name, protocol: form.protocol, base_url: form.base_url, model: form.model, api_key: form.api_key || null, activate };
  try {
    if (form.mode === 'edit') await api(`/providers/${form.id}`, { method: 'PUT', body });
    else await api('/providers', { method: 'POST', body });
  } catch (e) {
    form.msg = { ok: false, text: e.message };
    render();
    return;
  }
  form = null;
  await refreshState();
  render();
  toast(activate ? '已保存，接下来的提问会用它' : '已保存');
}

export async function deleteProvider() {
  if (confirmDelete !== form.id) {
    confirmDelete = form.id;
    render();
    return;
  }
  await api(`/providers/${form.id}`, { method: 'DELETE' });
  form = null;
  confirmDelete = null;
  await refreshState();
  render();
  toast('已删除');
}

export async function deleteProviderKey() {
  await api(`/providers/${form.id}/key`, { method: 'DELETE' });
  await refreshState();
  form.has_key = false;
  render();
  toast('已从钥匙串清除');
}

export async function activateProvider(id) {
  await api(`/providers/${id}/activate`, { method: 'POST' });
  await refreshState();
  if (app.view === 'settings') render();
  const p = app.state.providers.find((x) => x.id === id);
  toast(`接下来用 ${p.name} · ${p.model}`);
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
