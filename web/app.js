/* 企业知识库问答 · M6 前端
   与接口同源（FastAPI 把 web/ 挂在 /），所以不带 CORS 配置。
   所有服务端文本一律用 textContent 写 DOM；只有 http(s) 的出处链接才渲染成 <a>。 */
const TOKEN_KEY = 'kbra_token';
let token = localStorage.getItem(TOKEN_KEY) || '';
let busy = false;

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

async function api(path, body, method) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {
    method: method || (body ? 'POST' : 'GET'),
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && path !== '/api/login') signOut();
    const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

function signOut() {
  token = '';
  localStorage.removeItem(TOKEN_KEY);
  showAuth('');
}

function showAuth(msg, ok) {
  $('authCard').hidden = !!token;
  $('app').hidden = !token;
  const m = $('authMsg');
  m.textContent = msg || '';
  m.classList.toggle('ok', !!ok);
  if (token) {
    $('whoami').textContent = '已登录';
    const out = el('button', '', '退出');
    out.onclick = async () => { try { await api('/api/logout', {}); } catch (e) {} signOut(); };
    $('whoami').appendChild(out);
  } else {
    $('whoami').textContent = '';
  }
}

async function doAuth(register) {
  const username = $('username').value.trim();
  const password = $('password').value;
  try {
    if (register) await api('/api/register', { username, password });
    const r = await api('/api/login', { username, password });
    token = r.token;
    localStorage.setItem(TOKEN_KEY, token);
    showAuth('');
    loadHistory();
  } catch (e) {
    showAuth(e.message);
    $('authCard').hidden = false;
  }
}

/* ---------- 答案渲染 ---------- */

function citeTextFor(retrieved, chunkId) {
  const hit = (retrieved || []).find((r) => r.chunk_id === chunkId);
  return hit ? hit.text : '';
}

function answerView(data) {
  const w = data.warnings || {};
  const nWarn = (w.suspicious || []).length + (w.mismatched || []).length;
  const card = el('div', 'card answer-card' + (nWarn ? ' warned' : ''));
  if (data.refused) card.appendChild(el('span', 'badge refused', '已拒答'));
  card.appendChild(el('p', 'answer', data.answer));

  if (w.suspicious?.length) {
    card.appendChild(el('div', 'alerts hallu',
      '凭空引用（答案提到、本次检索里没有这部法律）：' + w.suspicious.join('、')));
  }
  if (w.mismatched?.length) {
    card.appendChild(el('div', 'alerts mismatch', '引用错配：' + w.mismatched.join('；')));
  }
  if (data.caveat) card.appendChild(el('p', 'caveat', data.caveat));

  const cites = data.citations || [];
  if (cites.length) {
    const ul = el('ul', 'cites');
    for (const c of cites) {
      const li = el('li', 'cite');
      const details = el('details');
      const sum = el('summary');
      sum.appendChild(el('span', 'n', `[${c.n}]`));
      sum.appendChild(el('span', '', c.title + (c.article ? ' ' + c.article : '')));
      details.appendChild(sum);
      const body = el('div', 'body');
      const text = citeTextFor(data.retrieved, c.chunk_id);
      body.appendChild(el('p', 'text', text || '（原文未随本条记录保存）'));
      if (c.url && /^https?:\/\//.test(c.url)) {
        const a = el('a', '', '出处：' + c.url);
        a.href = c.url;
        a.target = '_blank';
        a.rel = 'noreferrer';
        body.appendChild(a);
      }
      if (c.license) body.appendChild(el('p', 'hint', '许可证：' + c.license));
      details.appendChild(body);
      li.appendChild(details);
      ul.appendChild(li);
    }
    card.appendChild(ul);
  } else if (!data.refused) {
    card.appendChild(el('p', 'caveat', '⚠️ 答案未带任何 [n] 引用标，无法溯源。'));
  }

  const s = data.stats || {};
  const parts = [];
  if (s.top1_sim !== undefined) parts.push(`top1 相似度 ${s.top1_sim}`);
  if (s.retrieve_s !== undefined) parts.push(`检索 ${s.retrieve_s}s`);
  if (s.generate_s !== undefined) parts.push(`生成 ${s.generate_s}s / ${s.new_tokens} tok = ${s.tok_per_s} tok/s`);
  if (s.reason) parts.push(s.reason);
  card.appendChild(el('p', 'stats', parts.join(' ｜ ') || '无统计'));

  if (data.log_id) card.appendChild(feedbackRow(data.log_id, data.feedback));
  return card;
}

function feedbackRow(logId, current) {
  const row = el('div', 'fb');
  const up = el('button', '', '👍 有用');
  const down = el('button', '', '👎 有误');
  const label = el('span', 'muted', current === 1 ? '已反馈：有用' : current === -1 ? '已反馈：有误' : '你的反馈会写进 feedback 表');
  const paint = (v) => {
    up.className = v === 1 ? 'on' : '';
    down.className = v === -1 ? 'off' : '';
  };
  paint(current);
  const send = async (rating) => {
    try {
      await api('/api/feedback', { log_id: logId, rating });
      paint(rating);
      label.textContent = rating === 1 ? '已反馈：有用' : '已反馈：有误';
      loadHistory();
    } catch (e) { label.textContent = e.message; }
  };
  up.onclick = () => send(1);
  down.onclick = () => send(-1);
  row.append(up, down, label);
  return row;
}

function debugTable(rows) {
  const tbody = $('dbgTable').tBodies[0];
  tbody.textContent = '';
  if (!rows || !rows.length) {
    tbody.appendChild(el('tr')).appendChild(el('td', 'muted', '本次没有入模资料'));
    return;
  }
  rows.forEach((r, i) => {
    const tr = el('tr');
    tr.appendChild(el('td', '', String(i + 1)));
    tr.appendChild(el('td', '', r.chunk_id));
    const td = el('td');
    const d = el('details');
    d.appendChild(el('summary', '', (r.title || '') + (r.article ? ' ' + r.article : '') + '（点开看原文）'));
    d.appendChild(el('pre', '', r.text || ''));
    td.appendChild(d);
    tr.appendChild(td);
    tr.appendChild(el('td', '', r.score === undefined ? '' : String(r.score)));
    tbody.appendChild(tr);
  });
}

/* ---------- 交互 ---------- */

async function ask() {
  if (busy) return;
  const question = $('question').value.trim();
  if (question.length < 2) { $('askMsg').textContent = '问题太短'; return; }
  busy = true;
  $('btnAsk').disabled = true;
  $('askMsg').classList.remove('ok');
  $('askMsg').textContent = '提问中…（服务冷启动时首条要等索引与模型加载，可能 40 秒）';
  const t0 = performance.now();
  try {
    const data = await api('/api/ask', { question, k: +$('kSel').value, debug: true });
    const seconds = ((performance.now() - t0) / 1000).toFixed(1);
    $('askMsg').textContent = `端到端 ${seconds}s`;
    $('askMsg').classList.add('ok');
    $('result').textContent = '';
    $('result').appendChild(answerView(data));
    debugTable(data.retrieved);
    $('question').value = '';        // 不清空的话，回车两次就变成两条同样的问答记录
    loadHistory();
  } catch (e) {
    $('askMsg').textContent = '失败：' + e.message;
  } finally {
    busy = false;
    $('btnAsk').disabled = false;
  }
}

async function loadHistory() {
  try {
    const { items } = await api('/api/history?limit=10');
    const ul = $('history');
    ul.textContent = '';
    if (!items.length) { ul.appendChild(el('li', 'muted', '尚无记录')); return; }
    for (const it of items) {
      const li = el('li');
      const btn = el('button', '', it.question);
      btn.onclick = async () => {
        const full = await api(`/api/logs/${it.log_id}`);
        $('result').textContent = '';
        $('result').appendChild(answerView(full));
        debugTable(full.retrieved);
      };
      li.appendChild(btn);
      li.appendChild(el('span', 'tag', it.refused ? '拒答' : `${(it.citations || []).length} 条引用`));
      const w = it.warnings || {};
      const nWarn = (w.suspicious || []).length + (w.mismatched || []).length;
      if (nWarn) li.appendChild(el('span', 'tag', `⚠️ ${nWarn} 条引用告警`));
      if (it.feedback) li.appendChild(el('span', 'tag', it.feedback === 1 ? '👍' : '👎'));
      ul.appendChild(li);
    }
  } catch (e) { /* 未登录时忽略 */ }
}

$('btnLogin').onclick = () => doAuth(false);
$('btnRegister').onclick = () => doAuth(true);
$('btnAsk').onclick = ask;
$('question').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); ask(); }
});

(async function init() {
  if (!token) return showAuth('');
  try {
    await api('/api/history?limit=1');
    showAuth('');
    loadHistory();
  } catch (e) { showAuth('令牌已失效，请重新登录'); }
})();
