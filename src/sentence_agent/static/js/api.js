// Talks to the local server. The per-launch token arrives in the URL fragment (#t=...).
const token = () => (location.hash.match(/t=([^&]+)/) || [])[1] || '';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function headers(json) {
  const h = { 'X-App-Token': token() };
  if (json) h['Content-Type'] = 'application/json';
  return h;
}

export async function api(path, { method = 'GET', body } = {}) {
  let res;
  try {
    res = await fetch('/api' + path, {
      method,
      headers: headers(body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError('连不上本地服务，重新打开软件试试。', 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new ApiError((data && data.detail) || `请求失败（${res.status}）`, res.status);
  return data;
}

// POST /api/chat and yield each server-sent event as a parsed object.
export async function* streamChat(body, signal) {
  const res = await fetch('/api/chat', { method: 'POST', headers: headers(true), body: JSON.stringify(body), signal });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).detail; } catch { /* ignore */ }
    throw new ApiError(detail || `请求失败（${res.status}）`, res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const line = chunk.split('\n').find((l) => l.startsWith('data: '));
      if (line) yield JSON.parse(line.slice(6));
    }
  }
}
