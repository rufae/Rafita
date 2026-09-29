/* Rafita web SPA (Fase 3): chat, baúl y llamada compartiendo el mismo
   Agent Core que Telegram. Sin dependencias externas. */

const TOKEN_KEY = 'rafita_token';

const $ = (sel) => document.querySelector(sel);

const state = { token: localStorage.getItem(TOKEN_KEY) || '', email: '', view: 'chat' };

function show(view) {
  $('#login').classList.toggle('hidden', view !== 'login');
  $('#app').classList.toggle('hidden', view === 'login');
}

async function fetchConTimeout(url, opciones, ms) {
  const limite = ms || 10000;
  const ctrl = new AbortController();
  const temporizador = setTimeout(() => ctrl.abort(), limite);
  try {
    return await fetch(url, Object.assign({}, opciones || {}, { signal: ctrl.signal }));
  } catch (e) {
    if (e && e.name === 'AbortError') {
      throw new Error(
        `La petición tardó demasiado (${Math.round(limite / 1000)}s). Comprueba la conexión.`,
      );
    }
    throw e;
  } finally {
    clearTimeout(temporizador);
  }
}

// Modal propio (Fase 1.6): sustituye a alert()/confirm() nativos.
function mostrarModal({ titulo = 'Rafita', mensaje = '', confirmar = 'Aceptar', cancelar = '' }) {
  return new Promise((resolve) => {
    const capa = document.getElementById('modal');
    document.getElementById('modal-titulo').textContent = titulo;
    document.getElementById('modal-mensaje').textContent = mensaje;
    const btnOk = document.getElementById('modal-aceptar');
    const btnCancel = document.getElementById('modal-cancelar');
    btnOk.textContent = confirmar;
    btnCancel.textContent = cancelar || 'Cancelar';
    btnCancel.classList.toggle('hidden', !cancelar);
    capa.classList.remove('hidden');
    const cerrar = (valor) => {
      capa.classList.add('hidden');
      btnOk.onclick = null;
      btnCancel.onclick = null;
      resolve(valor);
    };
    btnOk.onclick = () => cerrar(true);
    btnCancel.onclick = () => cerrar(false);
    btnOk.focus();
  });
}

function mostrarAviso(mensaje) {
  return mostrarModal({ mensaje, confirmar: 'Entendido' });
}

// Mensajes para el usuario: nunca un codigo HTTP en crudo.
let mensajeSesion = '';

async function api(path, options = {}, ms) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let resp;
  try {
    resp = await fetchConTimeout(
      `/api${path}`,
      Object.assign({}, options, { headers }),
      ms || 10000,
    );
  } catch (e) {
    if (e && e.name === 'TypeError') {
      throw new Error('No se pudo conectar con el servidor. Comprueba tu conexión.');
    }
    throw e;
  }
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401) {
    mensajeSesion = 'Tu sesión ha caducado. Vuelve a entrar.';
    logout();
    throw new Error(mensajeSesion);
  }
  if (!resp.ok) {
    if (resp.status >= 500) {
      throw new Error(
        'El servidor no pudo completar la petición. Inténtalo de nuevo en un momento.',
      );
    }
    throw new Error(data.detail || data.error || 'No se pudo completar la operación.');
  }
  return data;
}

function logout() {
  state.token = '';
  localStorage.removeItem(TOKEN_KEY);
  show('login');
  if (mensajeSesion) {
    const aviso = document.getElementById('login-error');
    aviso.textContent = mensajeSesion;
    aviso.classList.remove('hidden');
    mensajeSesion = '';
  }
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
  document.querySelectorAll('.tab').forEach((t) => {
    const seleccionada = t.dataset.view === view;
    t.classList.toggle('active', seleccionada);
    t.setAttribute('aria-selected', seleccionada ? 'true' : 'false');
    t.tabIndex = seleccionada ? 0 : -1;
  });
  document.querySelectorAll('.view').forEach((v) => {
    v.classList.toggle('active', v.id === `view-${view}`);
  });
  if (view === 'vault') loadNotes();
  if (view === 'meetings') loadMeetings();
}

// Navegación de pestañas con teclado (patrón WAI-ARIA tabs).
document.querySelector('.tabs').addEventListener('keydown', (ev) => {
  const tabs = [...document.querySelectorAll('.tab')];
  const i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  let destino = null;
  if (ev.key === 'ArrowRight') destino = (i + 1) % tabs.length;
  else if (ev.key === 'ArrowLeft') destino = (i - 1 + tabs.length) % tabs.length;
  else if (ev.key === 'Home') destino = 0;
  else if (ev.key === 'End') destino = tabs.length - 1;
  if (destino === null) return;
  ev.preventDefault();
  tabs[destino].focus();
  setView(tabs[destino].dataset.view);
});

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => setView(tab.dataset.view));
});

/* ---------- chat ---------- */

function horaCorta(fechaISO) {
  const d = fechaISO ? new Date(String(fechaISO).replace(' ', 'T')) : new Date();
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Presentacion de los mensajes (mismas clases que antes: .bubble .bot/.user).
// Markdown minimo y seguro: primero se escapa el HTML y despues se aplican
// negritas, codigo, titulos y tablas (nunca se ejecuta nada del modelo).
function renderizarMarkdown(texto) {
  const escapado = String(texto ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
  const lineas = escapado.split('\n');
  const salida = [];
  let i = 0;
  while (i < lineas.length) {
    const linea = lineas[i];
    const esTabla =
      linea.includes('|') &&
      i + 1 < lineas.length &&
      /^\s*\|?[\s:|-]{3,}\|?\s*$/.test(lineas[i + 1]);
    if (esTabla) {
      const filas = [];
      while (i < lineas.length && lineas[i].includes('|')) {
        filas.push(lineas[i]);
        i += 1;
      }
      const celdas = (fila) =>
        fila
          .split('|')
          .map((c) => c.trim())
          .filter((c, idx, arr) => !(c === '' && (idx === 0 || idx === arr.length - 1)));
      const cabecera = celdas(filas[0]);
      const cuerpoFilas = filas.slice(2).map(celdas);
      salida.push(
        '<div class="tabla-scroll"><table><thead><tr>' +
          cabecera.map((c) => `<th>${c}</th>`).join('') +
          '</tr></thead><tbody>' +
          cuerpoFilas
            .map((fila) => `<tr>${fila.map((c) => `<td>${c}</td>`).join('')}</tr>`)
            .join('') +
          '</tbody></table></div>',
      );
      continue;
    }
    if (/^#{1,6}\s+/.test(linea)) {
      salida.push(`<strong>${linea.replace(/^#{1,6}\s+/, '')}</strong>`);
    } else if (linea.trim() === '') {
      salida.push('<br>');
    } else {
      salida.push(linea);
    }
    i += 1;
  }
  return salida
    .join('<br>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

// Los mensajes consecutivos del mismo emisor se leen como un grupo: solo el
// primero lleva avatar y nombre.
function reagruparBurbujas() {
  let anterior = '';
  document.querySelectorAll('#chat-messages .bubble').forEach((b) => {
    const rol = b.classList.contains('user') ? 'user' : 'bot';
    b.classList.toggle('continuacion', rol === anterior);
    anterior = rol;
  });
}

function crearBurbuja(texto, rol, fechaISO) {
  const div = document.createElement('div');
  if (rol === 'typing') {
    div.className = 'bubble typing';
    div.setAttribute('role', 'status');
    div.setAttribute('aria-label', 'Rafita está escribiendo');
    const cuerpo = document.createElement('div');
    cuerpo.className = 'typing-dots';
    for (let i = 0; i < 3; i++) {
      const punto = document.createElement('span');
      punto.className = 'dot';
      cuerpo.appendChild(punto);
    }
    div.appendChild(cuerpo);
    return div;
  }
  div.className = `bubble ${rol}`;
  const cuerpo = document.createElement('div');
  if (rol === 'bot') {
    const quien = document.createElement('span');
    quien.className = 'who';
    quien.textContent = 'Rafita';
    cuerpo.appendChild(quien);
  }
  const parrafo = document.createElement('p');
  parrafo.className = 'text';
  if (rol === 'bot') {
    parrafo.innerHTML = renderizarMarkdown(texto);
  } else {
    parrafo.textContent = texto;
  }
  cuerpo.appendChild(parrafo);
  const hora = horaCorta(fechaISO);
  if (hora) {
    const tiempo = document.createElement('time');
    tiempo.className = 'time';
    tiempo.textContent = hora;
    cuerpo.appendChild(tiempo);
  }
  div.appendChild(cuerpo);
  return div;
}

function addBubble(text, cls) {
  const rol = cls.includes('typing') ? 'typing' : cls.includes('user') ? 'user' : 'bot';
  const div = crearBurbuja(text, rol, '');
  $('#chat-messages').appendChild(div);
  reagruparBurbujas();
  $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
  return div;
}

let chatOffset = 0;

async function loadChatHistory(acumular) {
  const mas = acumular === true;
  const boxInicial = $('#chat-messages');
  if (!mas) {
    chatOffset = 0;
    boxInicial.innerHTML = skeletonHTML(2);
  }
  try {
    const data = await api(`/chat/history?limit=30&offset=${mas ? chatOffset : 0}`);
    const box = $('#chat-messages');
    if (!mas) box.innerHTML = '';
    const previo = document.getElementById('chat-more');
    if (previo) previo.remove();
    // Historial mas reciente primero en la API; los mensajes mas antiguos se
    // insertan ARRIBA en orden cronologico (Fase 4.3).
    const burbujaDe = (m) =>
      crearBurbuja(m.content, m.role === 'user' ? 'user' : 'bot', m.created_at);
    const burbujas = data.messages.filter((m) => m.role === 'user' || m.role === 'assistant');
    if (chatOffset === 0 && !burbujas.length)
      addBubble('Hola, soy Rafita. ¿En qué te ayudo?', 'bot');
    if (mas) {
      const ancla = box.querySelector('.bubble');
      [...burbujas].reverse().forEach((m) => {
        box.insertBefore(burbujaDe(m), ancla);
      });
    } else {
      burbujas.forEach((m) => {
        box.appendChild(burbujaDe(m));
      });
    }
    reagruparBurbujas();
    chatOffset += data.messages.length;
    if (data.messages.length >= 30) {
      const mas = document.createElement('div');
      mas.id = 'chat-more';
      mas.innerHTML = '<button class="ghost">Cargar mensajes anteriores</button>';
      mas.querySelector('button').addEventListener('click', () => loadChatHistory(true));
      box.prepend(mas);
      box.scrollTop = box.scrollHeight;
    }
  } catch (e) {
    addBubble(`No pude cargar el historial: ${e.message}`, 'bot');
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
    const data = await api(
      '/chat',
      { method: 'POST', body: JSON.stringify({ message: text }) },
      180000,
    );
    typing.remove();
    addBubble(data.reply, 'bot');
  } catch (e) {
    typing.remove();
    addBubble(`Error: ${e.message}`, 'bot');
  }
});

/* ---------- baúl ---------- */

let _currentNote = null;
let noteDirty = false;

// Fase 1.4: aviso de cambios sin guardar al cambiar de nota o salir.
async function confirmarDescartarCambios() {
  if (!noteDirty) return true;
  const seguir = await mostrarModal({
    titulo: 'Cambios sin guardar',
    mensaje: 'La nota actual tiene cambios sin guardar. ¿Descartarlos?',
    confirmar: 'Descartar',
    cancelar: 'Seguir editando',
  });
  if (seguir) noteDirty = false;
  return seguir;
}

$('#note-content').addEventListener('input', () => {
  noteDirty = true;
  $('#note-status').textContent = 'Cambios sin guardar';
});

window.addEventListener('beforeunload', (e) => {
  if (noteDirty) {
    e.preventDefault();
    e.returnValue = '';
  }
});

let vaultOffset = 0;

async function loadNotes(acumular) {
  // Ojo: debounce() reenvia el evento; solo "true" literal pagina.
  const mas = acumular === true;
  const query = encodeURIComponent($('#vault-search').value.trim());
  const folder = encodeURIComponent($('#vault-folder').value.trim());
  const list = $('#vault-list');
  if (!mas) {
    vaultOffset = 0;
    list.innerHTML = skeletonHTML(4);
  }
  try {
    const data = await api(
      `/vault/notes?query=${query}&folder=${folder}&limit=50&offset=${mas ? vaultOffset : 0}`,
    );
    if (!mas) list.innerHTML = '';
    if (!data.notes.length) {
      pintarVacio(
        list,
        '🗒️',
        query || folder ? 'Sin resultados' : 'Aún no hay notas',
        query || folder
          ? 'Prueba con otra búsqueda o borra el filtro de carpeta.'
          : 'Crea tu primera nota para empezar tu bóveda.',
        query || folder ? 'Limpiar búsqueda' : 'Nueva nota',
        'vault-empty-cta',
        () => {
          if (query || folder) {
            $('#vault-search').value = '';
            $('#vault-folder').value = '';
            loadNotes();
          } else {
            $('#vault-new').click();
          }
        },
      );
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
    vaultOffset += data.notes.length;
    // Paginacion real (Fase 4.3): si quedan mas, boton para cargar la pagina.
    const previo = document.getElementById('vault-more');
    if (previo) previo.remove();
    if (vaultOffset < data.total) {
      const mas = document.createElement('li');
      mas.id = 'vault-more';
      mas.innerHTML =
        '<button class="ghost" style="width:100%">Cargar más (' +
        (data.total - vaultOffset) +
        ' restantes)</button>';
      mas.querySelector('button').addEventListener('click', () => loadNotes(true));
      list.appendChild(mas);
    }
  } catch (e) {
    list.innerHTML = '';
    const li = document.createElement('li');
    li.className = 'error';
    li.textContent = `Error: ${e.message}`;
    list.appendChild(li);
  }
}

async function openNote(path, li) {
  if (!(await confirmarDescartarCambios())) return;
  try {
    const data = await api(`/vault/note?path=${encodeURIComponent(path)}`);
    _currentNote = data.path;
    $('#note-path').value = data.path;
    $('#note-content').value = data.content;
    $('#note-status').textContent = '';
    document.querySelectorAll('#vault-list li').forEach((el) => {
      el.classList.remove('active');
    });
    if (li) li.classList.add('active');
  } catch (e) {
    $('#note-status').textContent = `Error: ${e.message}`;
  }
}

$('#vault-search').addEventListener('input', debounce(loadNotes, 350));
$('#vault-folder').addEventListener('input', debounce(loadNotes, 350));

$('#vault-new').addEventListener('click', async () => {
  if (!(await confirmarDescartarCambios())) return;
  _currentNote = null;
  $('#note-path').value = `00-Inbox/nota-${new Date().toISOString().slice(0, 10)}.md`;
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
    _currentNote = data.path;
    noteDirty = false;
    $('#note-status').textContent = 'Guardada ✓';
    loadNotes();
  } catch (e) {
    $('#note-status').textContent = `Error: ${e.message}`;
  }
});

$('#note-delete').addEventListener('click', async () => {
  const path = $('#note-path').value.trim();
  if (!path) return;
  const borrar = await mostrarModal({
    titulo: 'Borrar nota',
    mensaje: `¿Borrar la nota ${path}?`,
    confirmar: 'Borrar',
    cancelar: 'Cancelar',
  });
  if (!borrar) return;
  try {
    await api(`/vault/note?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
    _currentNote = null;
    noteDirty = false;
    $('#note-path').value = '';
    $('#note-content').value = '';
    $('#note-status').textContent = 'Nota borrada';
    loadNotes();
  } catch (e) {
    $('#note-status').textContent = `Error: ${e.message}`;
  }
});

/* ---------- reuniones ---------- */

let recorder = null;
let recordingStream = null;
let recordChunks = [];
let pollTimer = null;
let currentMeetingId = null;
let meetStatusTimer = null;

function meetStatus(texto, limpiarMs) {
  clearTimeout(meetStatusTimer);
  $('#meet-status').textContent = texto;
  if (limpiarMs) {
    meetStatusTimer = setTimeout(() => {
      $('#meet-status').textContent = '';
    }, limpiarMs);
  }
}

function mejorMimeGrabacion() {
  // Fase 1.5: no todos los navegadores graban webm (Safari prefiere mp4).
  if (typeof MediaRecorder === 'undefined') return null;
  const candidatos = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
  for (const c of candidatos) {
    if (MediaRecorder.isTypeSupported(c)) return c;
  }
  return null;
}

async function uploadMeeting(blob, nombreFichero) {
  const form = new FormData();
  form.append('file', blob, nombreFichero || 'reunion.webm');
  form.append('title', $('#meet-title').value.trim());
  const resp = await fetchConTimeout(
    '/api/meetings',
    {
      method: 'POST',
      headers: state.token ? { Authorization: `Bearer ${state.token}` } : {},
      body: form,
    },
    30000,
  );
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
  return data;
}

function formatDuration(seconds) {
  const s = Math.round(seconds || 0);
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

async function loadMeetings() {
  const list = $('#meet-list');
  list.innerHTML = skeletonHTML(3);
  try {
    const data = await api('/meetings');
    list.innerHTML = '';
    if (!data.meetings.length) {
      pintarVacio(
        list,
        '🎙️',
        'Aún no hay reuniones',
        'Graba tu primera reunión: la transcribimos, separamos hablantes y generamos el acta.',
        'Grabar una reunión',
        'meet-empty-cta',
        () => $('#meet-mic').click(),
      );
      return;
    }
    data.meetings.forEach((m) => {
      const li = document.createElement('li');
      li.innerHTML = '<span class="title"></span><span class="muted small"></span>';
      li.querySelector('.title').textContent = m.title;
      const estado = m.status === 'done' ? formatDuration(m.duration_s) : m.status;
      li.querySelector('.small').textContent = `${m.created_at} · ${estado}`;
      li.addEventListener('click', () => showMeeting(m.id));
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = '';
    const li = document.createElement('li');
    li.className = 'error';
    li.textContent = `Error: ${e.message}`;
    list.appendChild(li);
  }
}

async function showMeeting(id) {
  clearInterval(pollTimer);
  try {
    const m = await api(`/meetings/${id}`);
    currentMeetingId = m.id;
    $('#meet-detail-title').value = m.title;
    $('#meet-save').classList.remove('hidden');
    $('#meet-delete').classList.remove('hidden');
    $('#meet-detail-meta').textContent =
      `${m.created_at} · ${m.status} · ${formatDuration(m.duration_s)}` +
      (m.speakers ? ` · ${m.speakers}` : '') +
      (m.note_path ? ' · guardada en el Baúl' : '');
    $('#meet-transcript').value = m.transcript || '';
    const box = $('#meet-summary');
    if (m.status === 'done') {
      const tareas = (m.tasks_list || []).map((t) => `<li>${escapeHtml(t)}</li>`).join('');
      box.innerHTML =
        `<p class="meet-summary-text">${escapeHtml(m.summary || '')}</p>` +
        (tareas ? `<strong>Tareas y compromisos</strong><ul>${tareas}</ul>` : '');
      box.classList.remove('muted');
    } else if (m.status === 'error') {
      box.textContent = 'No se pudo procesar el audio.';
    } else {
      box.textContent = 'Procesando (transcripción y resumen)…';
      pollTimer = setTimeout(() => showMeeting(id), 5000);
    }
    loadMeetings();
  } catch (e) {
    $('#meet-summary').textContent = `Error: ${e.message}`;
  }
}

// Editar y borrar la reunión seleccionada (el acta del Baúl se actualiza).
$('#meet-save').addEventListener('click', async () => {
  if (!currentMeetingId) return;
  const boton = $('#meet-save');
  boton.classList.add('is-loading');
  try {
    await api(`/meetings/${currentMeetingId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        title: $('#meet-detail-title').value.trim(),
        transcript: $('#meet-transcript').value,
      }),
    });
    meetStatus('Cambios guardados ✓', 3000);
    loadMeetings();
  } catch (e) {
    $('#meet-status').textContent = `No se pudo guardar: ${e.message}`;
  } finally {
    boton.classList.remove('is-loading');
  }
});

$('#meet-delete').addEventListener('click', async () => {
  if (!currentMeetingId) return;
  const borrar = await mostrarModal({
    titulo: 'Borrar reunión',
    mensaje: 'Se borrará la reunión, su audio y su acta del Baúl.',
    confirmar: 'Borrar',
    cancelar: 'Cancelar',
  });
  if (!borrar) return;
  try {
    await api(`/meetings/${currentMeetingId}`, { method: 'DELETE' });
    currentMeetingId = null;
    $('#meet-detail-title').value = '';
    $('#meet-transcript').value = '';
    $('#meet-summary').textContent = 'Selecciona o graba una reunión para ver su resumen.';
    $('#meet-detail-meta').textContent = '';
    $('#meet-save').classList.add('hidden');
    $('#meet-delete').classList.add('hidden');
    meetStatus('Reunión borrada', 3000);
    loadMeetings();
  } catch (e) {
    $('#meet-status').textContent = `No se pudo borrar: ${e.message}`;
  }
});

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

async function toggleRecording(kind) {
  const status = $('#meet-status');
  if (recorder && recorder.state === 'recording') {
    recorder.stop();
    return;
  }
  const mime = mejorMimeGrabacion();
  if (!mime) {
    status.textContent = 'Tu navegador no permite grabar audio (falta soporte de webm/mp4).';
    return;
  }
  try {
    recordingStream =
      kind === 'mic'
        ? await navigator.mediaDevices.getUserMedia({ audio: true })
        : await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
  } catch (e) {
    status.textContent = `Permiso denegado: ${e.message}`;
    return;
  }
  recordChunks = [];
  recorder = new MediaRecorder(recordingStream, { mimeType: mime });
  recorder.ondataavailable = (ev) => {
    if (ev.data.size) recordChunks.push(ev.data);
  };
  recorder.onstop = async () => {
    recordingStream.getTracks().forEach((t) => {
      t.stop();
    });
    $('#meet-mic').textContent = '● Grabar micro';
    $('#meet-tab').textContent = 'Grabar pestaña';
    status.textContent = 'Subiendo audio…';
    try {
      const blob = new Blob(recordChunks, { type: recorder.mimeType || 'audio/webm' });
      const nombre = (recorder.mimeType || '').includes('mp4') ? 'reunion.mp4' : 'reunion.webm';
      const data = await uploadMeeting(blob, nombre);
      status.textContent = 'Procesando…';
      $('#meet-title').value = '';
      showMeeting(data.id);
    } catch (e) {
      status.textContent = `Error: ${e.message}`;
    }
  };
  recorder.start(1000);
  $('#meet-mic').textContent = kind === 'mic' ? '■ Parar' : '● Grabar micro';
  $('#meet-tab').textContent = kind === 'tab' ? '■ Parar' : 'Grabar pestaña';
  status.textContent = 'Grabando…';
}

$('#meet-mic').addEventListener('click', () => toggleRecording('mic'));
$('#meet-tab').addEventListener('click', () => toggleRecording('tab'));

/* ---------- llamada ---------- */

$('#call-start').addEventListener('click', async () => {
  try {
    const data = await api('/call/token');
    const cfg = window.RAFITA_CONFIG || {};
    // Origen de voz: si la SPA va por HTTPS (p. ej. Tailscale) la voz vive en
    // el mismo host en :8443; por HTTP en LAN se usa :8001. config.js puede
    // forzarlo con callOrigin.
    const origin =
      cfg.callOrigin ||
      (location.protocol === 'https:'
        ? `${location.protocol}//${location.hostname}:8443`
        : `${location.protocol}//${location.hostname}:8001`);
    const frame = $('#call-frame');
    // El token NO va en la URL: se pasa por postMessage al cargar (Fase 0.2).
    frame.onload = () => {
      frame.contentWindow.postMessage({ type: 'rafita-auth', token: data.token }, origin);
    };
    frame.src = `${origin}/`;
    frame.classList.remove('hidden');
    $('#call-placeholder').classList.add('hidden');
  } catch (e) {
    mostrarAviso(`No se pudo iniciar la llamada: ${e.message}`);
  }
});

/* ---------- utilidades ---------- */

function skeletonHTML(n) {
  return Array.from({ length: n }, () => '<li class="skeleton"></li>').join('');
}

function vacioHTML(icono, _titulo, _texto, _ctaTexto, ctaId) {
  return (
    '<li class="vacio">' +
    '<div class="icono" aria-hidden="true">' +
    icono +
    '</div>' +
    '<strong></strong><span class="muted"></span>' +
    '<button class="primary" id="' +
    ctaId +
    '"></button></li>'
  );
}

function pintarVacio(contenedor, icono, titulo, texto, ctaTexto, ctaId, alPulsar) {
  contenedor.innerHTML = vacioHTML(icono, titulo, texto, ctaTexto, ctaId);
  contenedor.querySelector('strong').textContent = titulo;
  contenedor.querySelector('.muted').textContent = texto;
  const cta = contenedor.querySelector(`#${ctaId}`);
  cta.textContent = ctaTexto;
  cta.addEventListener('click', alPulsar);
}

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

/* Sign in with Google: flujo de dispositivo (funciona en LAN sin redirect
   URI). Si falla, se cae al flujo clasico con redirect. */
document.querySelector('.google-btn').addEventListener('click', async (ev) => {
  ev.preventDefault();
  const err = $('#login-error');
  const box = $('#google-device');
  err.classList.add('hidden');
  // Si la instalacion no tiene Google configurado, se explica (nada de JSON).
  let estadoGoogle = { configured: true };
  try {
    estadoGoogle = await api('/auth/google/status');
  } catch (_e) {
    /* si no se puede consultar, se intenta igualmente */
  }
  if (estadoGoogle && estadoGoogle.configured === false) {
    err.textContent =
      'El acceso con Google no está configurado en esta instalación. Entra con tu correo y contraseña (o define GOOGLE_WEB_CLIENT_ID y GOOGLE_WEB_CLIENT_SECRET).';
    err.classList.remove('hidden');
    return;
  }
  try {
    const start = await api('/auth/google/device/start', { method: 'POST' });
    box.classList.remove('hidden');
    box.innerHTML = '';
    const p = document.createElement('p');
    p.innerHTML =
      'Abre <a href="' +
      start.verification_url +
      '" target="_blank" rel="noopener">' +
      start.verification_url +
      '</a> e introduce el código:';
    const code = document.createElement('div');
    code.className = 'device-code';
    code.textContent = start.user_code;
    const espera = document.createElement('p');
    espera.className = 'muted small';
    espera.textContent = 'Esperando a que autorices en Google…';
    box.append(p, code, espera);
    const intervalo = Math.max(3, start.interval || 5) * 1000;
    const timer = setInterval(async () => {
      try {
        const res = await api(`/auth/google/device/poll?state=${encodeURIComponent(start.state)}`);
        if (res.status === 'ok') {
          clearInterval(timer);
          state.token = res.token;
          localStorage.setItem(TOKEN_KEY, res.token);
          enterApp();
        }
      } catch (e) {
        if (/caducad|no encontrada|validar/.test(e.message)) {
          clearInterval(timer);
          espera.textContent = `No se pudo completar: ${e.message}`;
        }
      }
    }, intervalo);
  } catch (_e) {
    // Sin flujo de dispositivo (p. ej. cliente web): flujo con redirect; sus
    // errores vuelven aqui como #google-error y se explican.
    location.href = '/api/auth/google/start';
  }
});

// Avisos del acceso con Google que vuelven como fragmento.
const errorGoogle = location.hash.match(/#google-error=([a-z_]+)/);
if (errorGoogle) {
  const mensajesGoogle = {
    no_config:
      'El acceso con Google no está configurado en esta instalación. Entra con tu correo y contraseña.',
    denied: 'Has cancelado el acceso con Google. Puedes intentarlo de nuevo.',
    state: 'La sesión de Google caducó antes de volver. Inténtalo otra vez.',
    google: 'Google no pudo validar tu cuenta. Inténtalo de nuevo.',
  };
  const aviso = document.getElementById('login-error');
  aviso.textContent =
    mensajesGoogle[errorGoogle[1]] || 'No se pudo completar el acceso con Google.';
  aviso.classList.remove('hidden');
  history.replaceState(null, '', '/app/');
}

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
