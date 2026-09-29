/* Rafita web SPA (Fase 3): chat, baúl y llamada compartiendo el mismo
   Agent Core que Telegram. Sin dependencias externas. */

const TOKEN_KEY = 'rafita_token';

const $ = (sel) => document.querySelector(sel);

let state = { token: localStorage.getItem(TOKEN_KEY) || '', email: '', view: 'chat' };

function show(view) {
  $('#login').classList.toggle('hidden', view !== 'login');
  $('#app').classList.toggle('hidden', view === 'login');
}

async function api(path, options = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
  const resp = await fetch('/api' + path, Object.assign({}, options, { headers }));
  if (resp.status === 401) {
    logout();
    throw new Error('sesión caducada');
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.error || ('HTTP ' + resp.status));
  return data;
}

function logout() {
  state.token = '';
  localStorage.removeItem(TOKEN_KEY);
  show('login');
}

async function enterApp() {
  try {
    const me = await api('/auth/me');
    state.email = me.user.email;
    $('#user-email').textContent = me.user.email + (me.user.is_admin ? ' (admin)' : '');
    show('app');
    setView('chat');
    loadChatHistory();
  } catch (_e) {
    logout();
  }
}

/* ---------- navegación ---------- */

function setView(view) {
  state.view = view;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === view));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  if (view === 'vault') loadNotes();
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => setView(tab.dataset.view));
});

/* ---------- chat ---------- */

function addBubble(text, cls) {
  const div = document.createElement('div');
  div.className = 'bubble ' + cls;
  div.textContent = text;
  $('#chat-messages').appendChild(div);
  $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
  return div;
}

async function loadChatHistory() {
  try {
    const data = await api('/chat/history?limit=30');
    const box = $('#chat-messages');
    box.innerHTML = '';
    data.messages.forEach((m) => {
      if (m.role === 'user' || m.role === 'assistant') {
        addBubble(m.content, m.role === 'user' ? 'user' : 'bot');
      }
    });
    if (!data.messages.length) addBubble('Hola, soy Rafita. ¿En qué te ayudo?', 'bot');
  } catch (e) {
    addBubble('No pude cargar el historial: ' + e.message, 'bot');
  }
}

$('#chat-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const input = $('#chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addBubble(text, 'user');
  const typing = addBubble('Rafita está escribiendo…', 'bot typing');
  try {
    const data = await api('/chat', { method: 'POST', body: JSON.stringify({ message: text }) });
    typing.remove();
    addBubble(data.reply, 'bot');
  } catch (e) {
    typing.remove();
    addBubble('Error: ' + e.message, 'bot');
  }
});

/* ---------- baúl ---------- */

let currentNote = null;

async function loadNotes() {
  const query = encodeURIComponent($('#vault-search').value.trim());
  const folder = encodeURIComponent($('#vault-folder').value.trim());
  const list = $('#vault-list');
  list.innerHTML = '<li class="muted">Cargando…</li>';
  try {
    const data = await api(`/vault/notes?query=${query}&folder=${folder}`);
    list.innerHTML = '';
    if (!data.notes.length) {
      list.innerHTML = '<li class="muted">No hay notas que coincidan.</li>';
      return;
    }
    data.notes.forEach((note) => {
      const li = document.createElement('li');
      li.dataset.path = note.path;
      li.innerHTML = `<span class="title"></span><span class="muted small"></span>`;
      li.querySelector('.title').textContent = note.title;
      li.querySelector('.small').textContent = `${note.folder || '/'} · ${note.modified}`;
      li.addEventListener('click', () => openNote(note.path, li));
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = `<li class="error">Error: ${e.message}</li>`;
  }
}

async function openNote(path, li) {
  try {
    const data = await api('/vault/note?path=' + encodeURIComponent(path));
    currentNote = data.path;
    $('#note-path').value = data.path;
    $('#note-content').value = data.content;
    $('#note-status').textContent = '';
    document.querySelectorAll('#vault-list li').forEach((el) => el.classList.remove('active'));
    if (li) li.classList.add('active');
  } catch (e) {
    $('#note-status').textContent = 'Error: ' + e.message;
  }
}

$('#vault-search').addEventListener('input', debounce(loadNotes, 350));
$('#vault-folder').addEventListener('input', debounce(loadNotes, 350));

$('#vault-new').addEventListener('click', () => {
  currentNote = null;
  $('#note-path').value = '00-Inbox/nota-' + new Date().toISOString().slice(0, 10) + '.md';
  $('#note-content').value = '';
  $('#note-status').textContent = 'Nueva nota (sin guardar)';
});

$('#note-save').addEventListener('click', async () => {
  const path = $('#note-path').value.trim();
  const content = $('#note-content').value;
  if (!path) return;
  try {
    const data = await api('/vault/note', {
      method: 'POST',
      body: JSON.stringify({ path, content }),
    });
    currentNote = data.path;
    $('#note-status').textContent = 'Guardada ✓';
    loadNotes();
  } catch (e) {
    $('#note-status').textContent = 'Error: ' + e.message;
  }
});

$('#note-delete').addEventListener('click', async () => {
  const path = $('#note-path').value.trim();
  if (!path || !confirm(`¿Borrar la nota ${path}?`)) return;
  try {
    await api('/vault/note?path=' + encodeURIComponent(path), { method: 'DELETE' });
    currentNote = null;
    $('#note-path').value = '';
    $('#note-content').value = '';
    $('#note-status').textContent = 'Nota borrada';
    loadNotes();
  } catch (e) {
    $('#note-status').textContent = 'Error: ' + e.message;
  }
});

/* ---------- llamada ---------- */

$('#call-start').addEventListener('click', async () => {
  try {
    const data = await api('/call/token');
    const cfg = window.RAFITA_CONFIG || {};
    const origin = cfg.callOrigin || `${location.protocol}//${location.hostname}:8001`;
    const url = `${origin}/?token=${encodeURIComponent(data.token || '')}`;
    $('#call-frame').src = url;
    $('#call-frame').classList.remove('hidden');
    $('#call-placeholder').classList.add('hidden');
  } catch (e) {
    alert('No se pudo iniciar la llamada: ' + e.message);
  }
});

/* ---------- utilidades ---------- */

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/* ---------- arranque ---------- */

$('#login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const err = $('#login-error');
  err.classList.add('hidden');
  try {
    const data = await api('/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        email: $('#login-email').value,
        password: $('#login-password').value,
      }),
    });
    state.token = data.token;
    localStorage.setItem(TOKEN_KEY, data.token);
    enterApp();
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  }
});

$('#logout').addEventListener('click', logout);

// Token llegado del callback de Google (/app/#token=...)
if (location.hash.startsWith('#token=')) {
  state.token = decodeURIComponent(location.hash.slice(7));
  localStorage.setItem(TOKEN_KEY, state.token);
  history.replaceState(null, '', '/app/');
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

if (state.token) enterApp();
else show('login');
