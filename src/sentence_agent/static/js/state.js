// App-wide state shared by the views: settings, key status, notebook counts.
import { api } from './api.js';
import { $, esc } from './util.js';

export const app = { state: null, view: 'chat' };

export async function refreshState() {
  app.state = await api('/state');
  renderBadges();
  return app.state;
}

export async function refreshStats() {
  const { stats } = await api('/review/counts');
  if (app.state) app.state.stats = stats;
  renderBadges();
  return stats;
}

export function renderBadges() {
  const s = app.state;
  if (!s) return;
  const select = $('#provider-switch');
  if (select && s.providers) {
    select.innerHTML = s.providers
      .map((p) => `<option value="${p.id}" ${p.id === s.active_provider ? 'selected' : ''} ${p.ready ? '' : 'disabled'}>${esc(p.name)} · ${esc(p.model || '未选模型')}${p.ready ? '' : '（未设置 key）'}</option>`)
      .join('');
  }
  $('#nav-book-count').textContent = s.stats.total ? String(s.stats.total) : '';
  $('#nav-due-count').textContent = s.stats.due ? String(s.stats.due) : '';
  $('#nav-key-warn').textContent = s.has_key ? '' : '!';
  const parts = [];
  if (s.stats.today) parts.push(`今天新存 ${s.stats.today} 句`);
  if (s.stats.reviewed_today) parts.push(`复习了 ${s.stats.reviewed_today} 次`);
  $('#rail-foot').textContent = parts.join(' · ');
}
