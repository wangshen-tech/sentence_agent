// Entry point: navigation, one delegated click handler for every data-action, keyboard shortcuts.
import * as chat from './chat.js';
import * as book from './notebook.js';
import * as review from './review.js';
import * as settings from './settings.js';
import { app, refreshState } from './state.js';
import { $, $$, copy, speak, toast } from './util.js';

const VIEWS = ['chat', 'book', 'review', 'settings'];

async function show(view) {
  if (!VIEWS.includes(view)) return;
  app.view = view;
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== view;
  $$('.rail .nav').forEach((b) => (b.dataset.view === view ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current')));
  try {
    if (view === 'book') await book.loadNotebook();
    if (view === 'review') await review.openReview();
    if (view === 'settings') await settings.openSettings();
    if (view === 'chat') $('#composer-input').focus();
  } catch (e) {
    toast(e.message);
  }
}

const actions = {
  nav: (el) => show(el.dataset.view),
  'new-chat': () => chat.newConversation(),
  'open-thread': (el) => chat.openConversation(Number(el.dataset.id)),
  'delete-thread': (el) => chat.deleteConversation(Number(el.dataset.id)),
  example: (el) => chat.sendExample(el.dataset.text),
  stop: () => chat.stop(),
  speak: (el) => speak(el.dataset.text),
  copy: (el) => copy(el.dataset.text),
  pin: (el) => book.pinEnglish(Number(el.dataset.id), el.dataset.text),
  'open-card': (el) => book.openCard(Number(el.dataset.id)),
  'close-drawer': () => book.closeDrawer(),
  'save-card': (el) => book.saveCardEdits(Number(el.dataset.id)),
  'delete-card': (el) => book.deleteCard(Number(el.dataset.id)),
  cover: (el) => book.setCover(el.dataset.cover),
  'flip-cell': (el) => book.flipCell(el),
  shuffle: () => book.toggleShuffle(),
  recover: () => book.recoverAll(),
  export: () => book.exportCsv(),
  'review-dir': (el) => review.setDirection(el.dataset.dir),
  'review-scope': (el) => review.setScope(el.dataset.scope),
  'start-review': () => review.startReview(),
  'review-again': () => review.openReview(),
  'end-review': () => review.endReview(),
  'flip-card': () => review.flip(),
  grade: (el) => review.grade(el.dataset.remembered === '1'),
  'provider-add': () => settings.addProvider(),
  'provider-edit': (el) => settings.editProvider(el.dataset.id),
  'provider-cancel': () => settings.cancelForm(),
  'provider-protocol': (el) => settings.setProtocol(el.dataset.protocol),
  'provider-pick-model': (el) => settings.pickModel(el.dataset.model),
  'provider-fetch-models': () => settings.fetchModels(),
  'provider-save': (el) => settings.saveProvider(el.dataset.activate === '1'),
  'provider-delete': () => settings.deleteProvider(),
  'provider-delete-key': () => settings.deleteProviderKey(),
  'provider-activate': (el) => settings.activateProvider(el.dataset.id),
  'set-effort': (el) => settings.setEffort(el.dataset.id),
  'delete-note': (el) => settings.deleteNote(Number(el.dataset.id)),
  'open-data-dir': () => settings.openDataDir(),
};

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el || !actions[el.dataset.action]) return;
  if (el.tagName === 'A') e.preventDefault();
  Promise.resolve(actions[el.dataset.action](el)).catch((err) => toast(err.message || String(err)));
});

document.addEventListener('keydown', (e) => {
  const target = e.target;
  const typing = target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement;
  if (e.key === 'Escape' && !$('#drawer').hidden) { book.closeDrawer(); return; }
  if (target.dataset && target.dataset.action === 'flip-cell' && (e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault();
    book.flipCell(target);
    return;
  }
  if (app.view === 'review' && review.isReviewing() && !typing && $('#drawer').hidden && !e.metaKey && !e.ctrlKey) {
    if (e.key === ' ' || (e.key === 'Enter' && target.dataset && target.dataset.action === 'flip-card')) { e.preventDefault(); review.flip(); }
    else if (e.key === '1') review.grade(false);
    else if (e.key === '2') review.grade(true);
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'n' && app.view === 'chat') { e.preventDefault(); chat.newConversation(); }
});

async function boot() {
  chat.initChat();
  book.initNotebook();
  $('#provider-switch').addEventListener('change', (e) => {
    settings.activateProvider(e.target.value).catch((err) => toast(err.message));
  });
  try {
    const state = await refreshState();
    await chat.loadThreads();
    await chat.openConversation(state.conversation_id);
    if (!state.has_key) chat.render();
  } catch (e) {
    toast(e.message);
  }
  $('#composer-input').focus();
}

boot();
