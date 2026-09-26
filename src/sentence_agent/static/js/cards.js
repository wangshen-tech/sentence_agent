// Rendering of the three kinds of notebook cards. The same renderer draws a card while its tool
// input is still streaming in (partial data), after it is saved, and in the notebook's detail view.
import { arr, copyBtn, diffWords, esc, md, roleVar, speakBtn, str } from './util.js';

export const KIND_BY_TOOL = { save_translation: 'zh2en', save_analysis: 'en2zh', save_correction: 'check' };
export const KIND_LABEL = { zh2en: '中→英', en2zh: '解析', check: '纠错' };

// Card data from a stored notebook card (its `detail` plus the source sentence).
export function dataFromCard(card) {
  const d = card.detail || {};
  if (card.kind === 'zh2en') return { ...d, zh: card.source || card.zh };
  if (card.kind === 'en2zh') return { ...d, en: card.source || card.en };
  return { ...d, original: d.original || card.source };
}

// Which English sentence the notebook keeps for a card, mirroring the server's choice.
export function notebookEnglish(kind, data) {
  if (kind === 'zh2en') return str(data.best);
  if (kind === 'en2zh') return str(data.en);
  const natural = arr(data.natural);
  return data.verdict === 'natural' || !natural.length ? str(data.corrected) : str(natural[0].en);
}

function pinBtn(opts, en) {
  if (!opts.cardId || opts.state !== 'saved' || !en) return '';
  if (opts.pinned === en) return '<span class="pinned">★ 句子本里存的是这句</span>';
  return `<button class="pin" data-action="pin" data-id="${opts.cardId}" data-text="${esc(en)}">存这句</button>`;
}

function statusHTML(opts) {
  switch (opts.state) {
    case 'streaming': return '<span class="card-status">正在写…</span>';
    case 'saved': return '<span class="card-status saved">已存进句子本</span>';
    case 'deleted': return '<span class="card-status">已从句子本删除</span>';
    case 'error': return '<span class="card-status">没存上</span>';
    default: return '';
  }
}

export function renderCard(kind, data, opts = {}) {
  data = data || {};
  const source = kind === 'zh2en' ? data.zh : kind === 'en2zh' ? data.en : data.original;
  const body = kind === 'zh2en' ? translationBody(data, opts) : kind === 'en2zh' ? analysisBody(data, opts) : correctionBody(data, opts);
  return `<article class="card">
    <div class="card-head">
      <span class="chip k-${kind}">${KIND_LABEL[kind]}</span>
      ${statusHTML(opts)}
      <span class="spacer"></span>
      ${opts.cardId && opts.state === 'saved' && opts.showOpen ? `<button class="link" data-action="open-card" data-id="${opts.cardId}">在句子本里打开</button>` : ''}
    </div>
    ${source ? `<p class="card-src ${kind === 'zh2en' ? 'zh' : 'en'}">${esc(source)}</p>` : ''}
    ${body}
    ${opts.state === 'streaming' ? '<p class="writing">正在写…</p>' : ''}
  </article>`;
}

function translationBody(d, opts) {
  const versions = arr(d.versions).filter((v) => v && v.en);
  const phrases = arr(d.phrases).filter((p) => p && p.phrase);
  const avoid = arr(d.avoid).filter((a) => a && a.en);
  return `
    ${d.best ? `<div class="lead"><div><div class="label">最推荐</div><p class="lead-en">${esc(d.best)}</p></div><div class="actions">${speakBtn(d.best)}${copyBtn(d.best)}</div></div>` : ''}
    ${versions.length ? `<section class="sec"><h4>不同语气的说法</h4><ul class="versions">${versions.map((v) => `
      <li class="ver"><span class="chip">${esc(v.tone || '说法')}</span>
        <div><p class="ver-en">${esc(v.en)}</p>${v.note ? `<p class="ver-note">${esc(v.note)}</p>` : ''}</div>
        <div class="actions">${speakBtn(v.en)}${copyBtn(v.en)}${pinBtn(opts, v.en)}</div></li>`).join('')}</ul></section>` : ''}
    ${phrases.length ? `<section class="sec"><h4>值得记住的表达</h4><div class="phrases">${phrases.map((p) => `
      <div class="phrase"><p class="ph"><mark>${esc(p.phrase)}</mark></p>${p.meaning ? `<p class="ph-mean">${esc(p.meaning)}</p>` : ''}
      ${p.example ? `<p class="ph-ex">${esc(p.example)}</p>` : ''}${p.example_zh ? `<p class="ph-exzh">${esc(p.example_zh)}</p>` : ''}</div>`).join('')}</div></section>` : ''}
    ${avoid.length ? `<section class="sec"><h4>别这么说</h4><ul class="avoid">${avoid.map((a) => `<li><s>${esc(a.en)}</s>${a.why ? `<span>${esc(a.why)}</span>` : ''}</li>`).join('')}</ul></section>` : ''}`;
}

function analysisBody(d) {
  const parts = arr(d.parts).filter((p) => p && p.text);
  const points = arr(d.points).filter((p) => p && p.title);
  const notes = parts.filter((p) => p.note);
  return `
    ${d.zh ? `<div class="lead"><div><div class="label">中文意思</div><p class="lead-zh">${esc(d.zh)}</p></div><div class="actions">${speakBtn(d.en)}</div></div>` : ''}
    ${parts.length || d.skeleton || d.pattern ? `<section class="sec"><h4>句子成分</h4>
      ${parts.length ? `<div class="parse-wrap"><div class="parse">${parts.map((p) => `<span class="chunk" style="--rc:var(${roleVar(p.role)})"><span class="chunk-t">${esc(p.text)}</span><span class="chunk-r">${esc(p.role)}</span></span>`).join('')}</div></div>` : ''}
      ${d.skeleton || d.pattern ? `<dl class="skel">${d.skeleton ? `<div><dt>主干</dt><dd class="en">${esc(d.skeleton)}</dd></div>` : ''}${d.pattern ? `<div><dt>句型</dt><dd>${esc(d.pattern)}</dd></div>` : ''}</dl>` : ''}
      ${notes.length ? `<ul class="part-notes">${notes.map((p) => `<li style="--rc:var(${roleVar(p.role)})"><span class="chip">${esc(p.role)}</span><span><b>${esc(p.text)}</b>　${esc(p.note)}</span></li>`).join('')}</ul>` : ''}
    </section>` : ''}
    ${d.explanation ? `<section class="sec"><h4>这句话怎么理解</h4><div class="md">${md(d.explanation)}</div></section>` : ''}
    ${points.length ? `<section class="sec"><h4>知识点</h4><div class="points">${points.map((p) => `<div class="point"><h5>${esc(p.title)}</h5>${p.explain ? `<p>${esc(p.explain)}</p>` : ''}${p.example ? `<p class="ex">${esc(p.example)}</p>` : ''}${p.example_zh ? `<p class="exzh">${esc(p.example_zh)}</p>` : ''}</div>`).join('')}</div></section>` : ''}`;
}

function correctionBody(d, opts) {
  const label = { natural: '✓ 地道', ok: '能懂，但不够地道', wrong: '有需要改的错误' }[d.verdict];
  const orig = str(d.original);
  const corrected = str(d.corrected);
  const changed = corrected && orig && corrected !== orig;
  const diff = changed ? diffWords(orig, corrected) : null;
  const issues = arr(d.issues).filter((i) => i && (i.wrong || i.fix || i.why));
  const natural = arr(d.natural).filter((v) => v && v.en);
  return `
    ${label ? `<div class="verdict v-${esc(d.verdict)}"><span class="v-badge">${label}</span>${d.comment ? `<p>${esc(d.comment)}</p>` : ''}</div>` : ''}
    ${corrected ? `<section class="sec"><h4>${changed ? '改动' : '原句'}</h4><div class="diff">
      ${changed
        ? `<div class="diff-row"><span class="diff-k">你写的</span><p>${diff.a}</p><span></span></div>
           <div class="diff-row"><span class="diff-k">改正后</span><p>${diff.b}</p><div class="actions">${speakBtn(corrected)}${copyBtn(corrected)}${pinBtn(opts, corrected)}</div></div>`
        : `<div class="diff-row"><span class="diff-k">你写的</span><p>${esc(orig)}</p><div class="actions">${speakBtn(orig)}${pinBtn(opts, orig)}</div></div>`}
      </div>${d.zh ? `<p class="zh-mean">意思：${esc(d.zh)}</p>` : ''}</section>` : ''}
    ${issues.length ? `<section class="sec"><h4>逐条说明</h4><ul class="issues">${issues.map((i) => `<li class="issue">
      <span class="chip">${esc(i.type || '说明')}</span>
      <span class="fix">${i.wrong ? `<s>${esc(i.wrong)}</s>` : ''}${i.wrong && i.fix ? ' → ' : ''}${i.fix ? `<b>${esc(i.fix)}</b>` : ''}</span>
      ${i.why ? `<p>${esc(i.why)}</p>` : ''}</li>`).join('')}</ul></section>` : ''}
    ${natural.length ? `<section class="sec"><h4>母语者更常这么说</h4><ul class="versions">${natural.map((v) => `<li class="ver">
      <span class="chip">地道</span><div><p class="ver-en">${esc(v.en)}</p>${v.note ? `<p class="ver-note">${esc(v.note)}</p>` : ''}</div>
      <div class="actions">${speakBtn(v.en)}${copyBtn(v.en)}${pinBtn(opts, v.en)}</div></li>`).join('')}</ul></section>` : ''}`;
}
