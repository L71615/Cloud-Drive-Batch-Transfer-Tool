/* PanTransfer 前端：无框架、无构建，纯 DOM。 */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const S = {
    meta: { providers: [], version: '0.1.0' },
    accounts: [],
    tasks: [],
    settings: {},
    provider: 'baidu',
    filter: '',
    timer: null,
    openTask: null,
  };

  /* ---------------- HTTP ---------------- */
  async function api(path, opt = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...opt,
    });
    let j = null;
    try { j = await res.json(); } catch { /* 非 JSON */ }
    if (!res.ok || !j || j.ok === false) {
      throw new Error((j && j.error) || `HTTP ${res.status}`);
    }
    return j;
  }
  const post = (path, body = {}) => api(path, { method: 'POST', body: JSON.stringify(body) });

  function toast(msg, kind = '') {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.textContent = msg;
    $('#toasts').append(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, 3200);
    setTimeout(() => el.remove(), 3500);
  }

  /* ---------------- 路由 ---------------- */
  const TITLES = {
    files: ['网盘管理', '浏览你自己网盘里的目录与文件'],
    transfer: ['新建转存', '粘贴分享链接，批量存入你的网盘'],
    tasks: ['转存任务', '实时进度 · 支持暂停 / 继续 / 取消'],
    accounts: ['账号管理', '保存你自己的登录态（仅本机）'],
    settings: ['设置', '任务引擎与限速参数'],
    diag: ['协议诊断', '按步骤跑最小链路，核对原始响应'],
    about: ['关于', '版本与免责声明'],
  };

  function go(view) {
    $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === view));
    $$('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + view));
    const [t, c] = TITLES[view] || ['', ''];
    $('#pageTitle').textContent = t;
    $('#pageCrumb').textContent = c;
    location.hash = view;
    if (view === 'files') initFiles();
    if (view === 'tasks') loadTasks();
    if (view === 'accounts') { loadAccounts(); }
    if (view === 'settings') loadSettings();
    if (view === 'diag') initDiag();
  }

  /* ---------------- 网盘 ---------------- */
  function providerInfo(name) {
    return S.meta.providers.find(p => p.name === name) || { name, label: name, color: '#666', short: name.slice(0, 2) };
  }

  function renderDrives() {
    const box = $('#drives');
    box.innerHTML = '';
    S.meta.providers.forEach(p => {
      const d = document.createElement('div');
      d.className = 'drive' + (p.name === S.provider ? ' active' : '');
      d.dataset.name = p.name;
      d.innerHTML = `
        <div class="ico" style="background:${esc(p.color || '#666')}">${esc(p.short || p.name.slice(0, 2))}</div>
        <div>
          <div class="t">${esc(p.label)}</div>
          <div class="s">${p.ready ? '已就绪' : '待配置规格'}</div>
        </div>`;
      d.onclick = () => { S.provider = p.name; renderDrives(); syncAccountSelect(); countLinks(); };
      box.append(d);
    });
    const cur = providerInfo(S.provider);
    $('#driveHint').textContent = cur.ready
      ? `${cur.label} 接口已就绪`
      : `${cur.label} 接口待配置（见 docs/spec/${cur.name}.md §3–§5），可先用「演示模式」跑通流程`;
  }

  function countLinks() {
    const text = $('#links').value.trim();
    const n = text ? text.split(/\s+/).filter(Boolean).length : 0;
    $('#linkCount').textContent = `${n} 条链接`;
  }

  /* ---------------- 账号 ---------------- */
  async function loadAccounts() {
    const { accounts } = await api('/api/accounts');
    S.accounts = accounts;
    const tb = $('#accBody');
    tb.innerHTML = '';
    $('#accEmpty').hidden = accounts.length > 0;
    accounts.forEach(a => {
      const p = providerInfo(a.provider);
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="pill">${esc(p.label)}</span></td>
        <td>${esc(a.name || '-')}</td>
        <td class="muted">${new Date(a.created_at * 1000).toLocaleString('zh-CN')}</td>
        <td class="row">
          <button class="ghost sm" data-act="check">检测</button>
          <button class="ghost sm" data-act="use">用于转存</button>
          <button class="danger sm" data-act="del">删除</button>
        </td>`;
      tr.querySelector('[data-act="check"]').onclick = async () => {
        toast('正在检测 ' + p.label + ' 登录态…');
        try {
          const r = await post(`/api/accounts/${a.id}/check`);
          toast(r.message || (r.ok ? '登录态有效' : '登录态无效'), r.valid ? 'ok' : 'bad');
        } catch (e) { toast(e.message, 'bad'); }
      };
      tr.querySelector('[data-act="use"]').onclick = () => {
        S.provider = a.provider;
        renderDrives();
        syncAccountSelect();
        $('#accountSel').value = String(a.id);
        go('transfer');
        toast('已选择：' + (a.name || p.label));
      };
      tr.querySelector('[data-act="del"]').onclick = async () => {
        if (!confirm(`删除 ${p.label} 的登录态「${a.name || a.id}」？`)) return;
        await fetch(`/api/accounts/${a.id}`, { method: 'DELETE' });
        loadAccounts(); syncAccountSelect();
        toast('已删除', 'ok');
      };
      tb.append(tr);
    });
    syncAccountSelect();
    renderFoot(accounts[0]);
  }

  // 左下角的账号块，和桌面版那种"当前登录用户"一样
  function renderFoot(acc) {
    const p = acc ? providerInfo(acc.provider) : null;
    $('#footAvatar').textContent = acc ? String(p.short || p.label).slice(0, 2) : '—';
    $('#footName').textContent = acc ? (acc.name || p.label) : '未登录';
    $('#footSub').textContent = acc ? p.label + ' · 已连接' : '点「账号管理」添加';
  }

  function syncAccountSelect() {
    const sel = $('#accountSel');
    const list = S.accounts.filter(a => a.provider === S.provider);
    sel.innerHTML = '<option value="">（不使用已保存账号，手动填写）</option>';
    list.forEach(a => sel.append(new Option(a.name || `${providerInfo(a.provider).label} #${a.id}`, String(a.id))));
    if (list.length) sel.value = String(list[0].id);
    const has = list.length > 0;
    $('#cred').style.display = has && sel.value ? 'none' : 'block';
  }

  async function saveAccount() {
    const provider = $('#accProvider').value;
    const name = $('#accName').value.trim();
    const credential = $('#accCookie').value.trim();
    if (!credential) { note('#accHint', 'Cookie 不能为空', true); return; }
    try {
      const v = await post('/api/accounts/validate', { provider, credential });
      if (!v.valid) { note('#accHint', v.message, true); return; }
      await post('/api/accounts', { provider, name, credential: v.cookie });
      $('#accCookie').value = ''; $('#accName').value = '';
      note('#accHint', `已保存（${v.count} 个字段）`);
      loadAccounts();
      toast('登录态已保存', 'ok');
    } catch (e) { note('#accHint', e.message, true); }
  }

  /* ---------------- 登录指引 ---------------- */
  const GUIDE = {
    baidu: [
      '浏览器打开 <code>pan.baidu.com</code>，确认右上角能看到自己的头像（已登录状态）。',
      '按 <code>F12</code> 打开开发者工具，切到 <code>Network</code>（网络）面板。',
      '在页面上点一下「我的文件」，右侧会出现一批请求。',
      '点任意一条 <code>pan.baidu.com</code> 的请求 → <code>Headers</code> → <code>Request Headers</code> → 找到 <code>Cookie</code>。',
      '右键该行选「Copy value」（或手动全选复制），粘贴到下方输入框。',
      '这段里必须含 <code>BDUSS</code>；没有就是没登录成功、或复制成了 Response Headers。',
    ],
    quark: [
      '浏览器打开 <code>pan.quark.cn</code> 并登录。',
      '按 <code>F12</code> → <code>Network</code>，点一下左侧「全部文件」刷新目录。',
      '点任意一条 <code>drive-pc.quark.cn</code> 的请求 → <code>Headers</code> → <code>Request Headers</code> → <code>Cookie</code>。',
      '整段复制粘贴到下方输入框。',
      '必须含 <code>__pus</code> 或 <code>__puus</code>，否则是游客态。',
    ],
    uc: [
      '浏览器打开 <code>drive.uc.cn</code> 并登录。',
      '按 <code>F12</code> → <code>Network</code>，点进任意文件夹让接口发一次请求。',
      '点任意一条 <code>pc-api.uc.cn</code> 的请求 → <code>Headers</code> → <code>Cookie</code>。',
      '整段复制粘贴到下方输入框。',
      '必须含 <code>__pus</code> 或 <code>__puus</code>。',
    ],
  };

  function renderGuide(provider) {
    const steps = GUIDE[provider] || ['先选一个网盘。'];
    $('#guideSteps').innerHTML = steps.map(s => `<li>${s}</li>`).join('');
  }

  let cookieTimer = null;
  function checkCookieSoon() {
    clearTimeout(cookieTimer);
    cookieTimer = setTimeout(async () => {
      const credential = $('#accCookie').value;
      if (!credential.trim()) { note('#accHint', ''); return; }
      try {
        const r = await post('/api/accounts/validate', {
          provider: $('#accProvider').value, credential,
        });
        if (r.valid) note('#accHint', `格式正确：识别到 ${r.count} 个字段`, false);
        else note('#accHint', r.message, true);
      } catch (e) { note('#accHint', e.message, true); }
    }, 350);
  }

  /* ---------------- 扫码登录（浏览器代登录） ---------------- */
  let loginTimer = null, loginSid = null;

  function stopLoginPoll() {
    clearTimeout(loginTimer); loginTimer = null;
    $('#btnLoginCancel').hidden = true;
  }

  async function startBrowserLogin() {
    const provider = $('#accProvider').value;
    if (!provider) { note('#loginHint', '先选一个网盘', true); return; }
    note('#loginHint', '正在打开登录窗口…');
    try {
      const r = await post('/api/login/browser', { provider });
      loginSid = r.login.sid;
      $('#btnLoginCancel').hidden = false;
      note('#loginHint', r.hint || '等待登录…');
      pollLogin();
    } catch (e) { note('#loginHint', e.message, true); }
  }

  async function pollLogin() {
    if (!loginSid) return;
    try {
      const r = await api('/api/login/browser/' + loginSid);
      const lg = r.login;
      if (lg.status === 'ready') { await finishLogin(lg); return; }
      note('#loginHint', lg.message || '等待登录…');
      if (lg.status === 'closed' || lg.status === 'error') { stopLoginPoll(); return; }
    } catch (e) {
      note('#loginHint', e.message, true);
      stopLoginPoll();
      return;
    }
    loginTimer = setTimeout(pollLogin, 1200);
  }

  async function finishLogin() {
    clearTimeout(loginTimer); loginTimer = null;
    try {
      const r = await post('/api/login/browser/save', {
        sid: loginSid, name: $('#accName').value.trim(),
      });
      toast(`登录成功，已保存（${r.count} 个字段）`, 'ok');
      note('#loginHint', '登录成功，已加入账号列表');
      $('#accCookie').value = ''; $('#accName').value = '';
      loginSid = null;
      $('#btnLoginCancel').hidden = true;
      loadAccounts();
    } catch (e) { note('#loginHint', e.message, true); }
  }

  async function cancelLogin() {
    if (loginSid) {
      try { await fetch('/api/login/browser/' + loginSid, { method: 'DELETE' }); } catch (e) { /* ignore */ }
    }
    stopLoginPoll();
    loginSid = null;
    note('#loginHint', '已取消，窗口已关闭');
  }

  /* ---------------- 解析 / 建任务 ---------------- */
  async function doParse() {
    const text = $('#links').value;
    try {
      const r = await post('/api/parse', { provider: S.provider, text });
      const card = $('#previewCard');
      const body = $('#previewBody');
      body.innerHTML = '';
      $('#previewHint').textContent = `识别到 ${r.count} 个分享`;
      r.refs.forEach(x => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td><span class="pill">${esc(providerInfo(x.provider).label)}</span></td>
          <td class="mono">${esc(x.share_id)}</td>
          <td>${x.passcode ? esc(x.passcode) : '<span class="muted">无</span>'}</td>
          <td class="mono muted">${esc(x.url)}</td>`;
        body.append(tr);
      });
      card.hidden = r.count === 0;
      if (r.count) {
        note('#hint', `解析到 ${r.count} 个分享，可以开始转存`, false);
      } else {
        note('#hint', r.hint || `没有识别到「${providerInfo(S.provider).label}」的分享链接——检查链接是否复制完整`, true);
      }
    } catch (e) { note('#hint', e.message, true); }
  }

  async function createTask() {
    const btn = $('#btnCreate');
    const body = {
      provider: S.provider,
      links: $('#links').value,
      target: $('#target').value.trim() || '/PanTransfer',
    };
    const accId = $('#accountSel').value;
    if (accId) body.account_id = Number(accId);
    else body.credential = $('#cred').value;

    btn.disabled = true;
    try {
      const r = await post('/api/tasks', body);
      note('#hint', '已创建任务 ' + r.id + '，可在「转存任务」查看');
      toast('任务已创建', 'ok');
      $('#links').value = ''; countLinks(); $('#previewCard').hidden = true;
      await loadTasks();
      setBadge();
    } catch (e) { note('#hint', e.message, true); }
    finally { btn.disabled = false; }
  }

  const note = (sel, msg, bad) => {
    const el = $(sel);
    el.textContent = msg || '';
    el.classList.toggle('bad', !!bad);
  };

  /* ---------------- 任务 ---------------- */
  const STATUS = {
    pending: ['排队中', 'wait'], resolving: ['解析链接', 'run'], listing: ['读取目录', 'run'],
    transferring: ['转存中', 'run'], verifying: ['校验中', 'run'], done: ['已完成', 'ok'],
    failed: ['失败', 'bad'], paused: ['已暂停', 'wait'], canceled: ['已取消', 'bad'],
    skipped: ['跳过', 'wait'],
  };
  const pill = (s) => {
    const [txt, cls] = STATUS[s] || [s, ''];
    return `<span class="pill ${cls}">${esc(txt)}</span>`;
  };
  const isRunning = (s) => ['pending', 'resolving', 'listing', 'transferring', 'verifying'].includes(s);

  // total = 分享内已发现的文件数（一条链接可能含成百上千个文件），不是链接数
  function progressText(t) {
    const fail = t.failed ? ` · 失败 ${t.failed}` : '';
    const skip = t.skipped ? ` · 跳过 ${t.skipped}` : '';
    if (t.status === 'listing' && !t.done) return `正在读取目录 · 已发现 ${t.total} 个文件`;
    if (t.status === 'transferring') return `已转存 ${t.done}/${t.total}${skip}${fail}`;
    return `${t.done}/${t.total}${skip}${fail}`;
  }

  async function loadTasks() {
    const { tasks } = await api('/api/tasks?limit=200');
    S.tasks = tasks;
    const tb = $('#taskBody');
    tb.innerHTML = '';
    const list = S.filter ? tasks.filter(t => S.filter === 'running' ? isRunning(t.status) : t.status === S.filter) : tasks;
    $('#taskEmpty').hidden = list.length > 0;
    $('#taskEmpty').textContent = tasks.length ? '没有符合筛选条件的任务' : '还没有任务。去「新建转存」粘贴几个链接试试。';

    list.forEach(t => {
      const p = providerInfo(t.provider);
      const pct = t.total ? Math.round((t.done + t.failed) * 100 / t.total) : 0;
      const tr = document.createElement('tr');
      tr.className = 'clickable';
      tr.innerHTML = `
        <td class="mono">${esc(t.id)}</td>
        <td><span class="pill" style="border-color:${esc(p.color)}55;color:${esc(p.color)}">${esc(p.label)}</span></td>
        <td>${pill(t.status)}${t.error ? `<div class="muted" style="color:#f0a29c;margin-top:3px">${esc(t.error)}</div>` : ''}</td>
        <td>
          <div class="muted">${progressText(t)}</div>
          <div class="bar"><i style="width:${pct}%"></i></div>
        </td>
        <td class="mono muted">${esc(t.target)}</td>
        <td class="row">
          ${isRunning(t.status) || t.status === 'paused'
            ? `<button class="ghost sm" data-a="toggle">${t.status === 'paused' ? '继续' : '暂停'}</button>` : ''}
          ${t.status === 'failed' ? '<button class="ghost sm" data-a="retry">重试</button>' : ''}
          <button class="danger sm" data-a="del">删除</button>
        </td>`;
      tr.onclick = (e) => { if (!e.target.closest('button')) openDrawer(t.id); };
      const toggle = tr.querySelector('[data-a="toggle"]');
      if (toggle) toggle.onclick = async () => {
        const act = t.status === 'paused' ? 'resume' : 'pause';
        await post(`/api/tasks/${t.id}/${act}`);
        loadTasks();
      };
      const retry = tr.querySelector('[data-a="retry"]');
      if (retry) retry.onclick = async () => {
        // 旧版本任务没存链接，重试时必须让用户把链接补回来
        let links = '';
        if (t.has_links === false) {
          links = prompt('这个任务是旧版本创建的，没有保存原始链接。\n把同一条分享链接再粘一次（已转存的文件会自动跳过）：') || '';
          if (!links.trim()) return;
        }
        try {
          await post(`/api/tasks/${t.id}/retry`, links ? { links } : undefined);
          toast('已重新排队', 'ok'); loadTasks();
        } catch (e) { toast(e.message, true); }
      };
      tr.querySelector('[data-a="del"]').onclick = async () => {
        if (!confirm('删除任务记录 ' + t.id + '？（不影响已转存的文件）')) return;
        await fetch(`/api/tasks/${t.id}`, { method: 'DELETE' });
        loadTasks(); loadStats();
      };
      tb.append(tr);
    });

    const active = tasks.filter(t => isRunning(t.status)).length;
    $('#navBadge').hidden = active === 0;
    $('#navBadge').textContent = String(active);
    loadStats();
  }

  async function loadStats() {
    try {
      const s = await api('/api/stats');
      $('#stRunning').textContent = s.running;
      $('#stDone').textContent = s.done;
      $('#stFailed').textContent = s.failed;
      $('#stFiles').textContent = s.files;
    } catch { /* 忽略轮询错误 */ }
  }

  function setBadge() { /* loadTasks 内已维护 */ }

  async function openDrawer(id) {
    const { task } = await api('/api/tasks/' + id);
    S.openTask = id;
    $('#drawerTitle').textContent = `任务 ${task.id} · ${providerInfo(task.provider).label}`;
    const pct = task.total ? Math.round((task.done + task.failed) * 100 / task.total) : 0;
    const okN = task.items.filter(i => i.status === 'done').length;
    const badN = task.items.filter(i => i.status === 'failed').length;
    const rows = task.items.map((i, n) => `
      <tr>
        <td class="mono" style="color:var(--dim)">${n + 1}</td>
        <td class="mono">${esc(i.src)}</td>
        <td class="muted">${fmtSize(i.size)}</td>
        <td>${i.status === 'done' ? '<span class="pill ok">成功</span>' : '<span class="pill bad">失败</span>'}</td>
      </tr>`).join('');
    $('#drawerBody').innerHTML = `
      <div class="kv" style="margin-bottom:14px">
        <div class="k">状态</div><div>${pill(task.status)}</div>
        <div class="k">进度</div><div>${task.done}/${task.total}${task.failed ? ` · 失败 ${task.failed}` : ''}（${pct}%）</div>
        <div class="k">目标目录</div><div class="mono">${esc(task.target)}</div>
        <div class="k">创建时间</div><div>${new Date(task.created_at * 1000).toLocaleString('zh-CN')}</div>
        ${task.error ? `<div class="k">错误</div><div style="color:#f0a29c">${esc(task.error)}</div>` : ''}
      </div>
      <div class="bar" style="margin-bottom:14px"><i style="width:${pct}%"></i></div>
      <div class="muted" style="margin-bottom:8px">文件明细 · 成功 ${okN} / 失败 ${badN}</div>
      ${task.items.length
        ? `<table class="tb"><thead><tr><th style="width:34px">#</th><th>源路径</th><th style="width:80px">大小</th><th style="width:70px">结果</th></tr></thead><tbody>${rows}</tbody></table>`
        : '<div class="empty">还没有文件明细</div>'}`;
    $('#mask').classList.add('on');
    $('#drawer').classList.add('on');
  }

  function closeDrawer() {
    $('#mask').classList.remove('on');
    $('#drawer').classList.remove('on');
    S.openTask = null;
  }

  const fmtSize = (n) => {
    if (!n) return '-';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
  };

  /* ---------------- 目录浏览 ---------------- */
  const DIR = { provider: '', accountId: '', credential: '', path: '/', items: [] };

  function credQuery() {
    const accId = $('#accountSel').value;
    if (accId) return 'account_id=' + encodeURIComponent(accId);
    const c = $('#cred').value.trim();
    return c ? 'credential=' + encodeURIComponent(c) : '';
  }

  async function openDirPicker() {
    DIR.provider = S.provider;
    DIR.path = $('#target').value.trim() || '/';
    if (!DIR.path.startsWith('/')) DIR.path = '/' + DIR.path;
    $('#dirMask').classList.add('on');
    $('#dirDrawer').classList.add('on');
    await loadDir(DIR.path);
  }

  async function loadDir(path) {
    DIR.path = path || '/';
    $('#dirPath').textContent = DIR.path;
    $('#dirList').innerHTML = '<div class="dir-empty">加载中…</div>';
    const q = [`provider=${encodeURIComponent(DIR.provider)}`,
               'path=' + encodeURIComponent(DIR.path)];
    const auth = credQuery();
    if (auth) q.push(auth);
    try {
      const r = await api('/api/browse?' + q.join('&'));
      DIR.items = r.items || [];
      renderDir();
    } catch (e) {
      DIR.items = [];
      $('#dirList').innerHTML = `<div class="dir-empty">读取失败：${esc(e.message)}</div>`;
      $('#dirNote').textContent = '需要有效的登录态才能浏览目录';
    }
  }

  function renderDir() {
    const box = $('#dirList');
    box.innerHTML = '';
    const noteEl = $('#dirNote');
    if (DIR.path !== '/') {
      const up = document.createElement('div');
      up.className = 'dir-item';
      up.innerHTML = '<span class="di">↑</span><span class="dn">上一级</span>';
      up.onclick = () => loadDir(parentOf(DIR.path));
      box.append(up);
    }
    const dirs = DIR.items.filter(x => x.is_dir);
    dirs.forEach(x => {
      const row = document.createElement('div');
      row.className = 'dir-item';
      row.innerHTML = `<span class="di">📁</span><span class="dn">${esc(x.name)}</span>`;
      row.onclick = () => loadDir(x.path);
      box.append(row);
    });
    const files = DIR.items.length - dirs.length;
    noteEl.textContent = dirs.length
      ? `${dirs.length} 个文件夹${files ? ` · ${files} 个文件已忽略` : ''}`
      : '这里没有子文件夹，可以直接选这个目录';
    if (!DIR.items.length) box.innerHTML = '<div class="dir-empty">空目录</div>';
  }

  const parentOf = (p) => {
    const t = p.replace(/\/+$/, '');
    const i = t.lastIndexOf('/');
    return i <= 0 ? '/' : t.slice(0, i);
  };

  function pickDir() {
    $('#target').value = DIR.path;
    closeDir();
    toast('目标目录：' + DIR.path, 'ok');
  }

  function closeDir() {
    $('#dirMask').classList.remove('on');
    $('#dirDrawer').classList.remove('on');
  }

  /* ---------------- 协议诊断 ---------------- */
  function initDiag() {
    const sel = $('#diagProvider');
    if (!sel.options.length) {
      S.meta.providers.forEach(p => sel.append(new Option(p.label, p.name)));
    }
    if (S.meta.providers.some(p => p.name === S.provider)) sel.value = S.provider;
    syncDiagAccount();
  }

  function syncDiagAccount() {
    const prov = $('#diagProvider').value;
    const sel = $('#diagAccount');
    const keep = sel.value;
    sel.innerHTML = '<option value="">（不使用账号，手动填 Cookie）</option>';
    S.accounts.filter(a => a.provider === prov)
      .forEach(a => sel.append(new Option(a.name || `${providerInfo(prov).label} #${a.id}`, String(a.id))));
    if (keep && [...sel.options].some(o => o.value === keep)) sel.value = keep;
    $('#diagCred').style.display = sel.value ? 'none' : 'block';
  }

  async function runDiag() {
    const provider = $('#diagProvider').value;
    const link = $('#diagLink').value.trim();
    if (!link) { note('#diagHint', '先填一条分享链接', true); return; }
    const body = { provider, link, passcode: $('#diagPass').value.trim() };
    const accId = $('#diagAccount').value;
    if (accId) body.account_id = Number(accId);
    else if ($('#diagCred').value.trim()) body.credential = $('#diagCred').value.trim();

    const btn = $('#btnDiag');
    btn.disabled = true;
    note('#diagHint', '正在按步骤执行…');
    try {
      const r = await post('/api/diag', body);
      const steps = r.steps || [];
      const okN = steps.filter(s => s.ok).length;
      $('#diagSummary').textContent = `${okN}/${steps.length} 步成功`;
      $('#diagBody').innerHTML = steps.map(s => `
        <tr>
          <td>${esc(s.name)}</td>
          <td>${s.ok ? '<span class="pill ok">通过</span>' : '<span class="pill bad">失败</span>'}</td>
          <td class="muted">${s.ms} ms</td>
          <td class="step-detail">${esc(s.detail || s.error || '')}</td>
        </tr>`).join('');
      $('#diagCard').hidden = false;
      const raw = r.raw || [];
      $('#diagRaw').textContent = raw.length
        ? raw.map((x, i) => `#${i + 1}  ${x.method} ${x.url}\n${x.response}`).join('\n\n── ── ──\n\n')
        : '（本次没有发出 HTTP 请求）';
      $('#diagRawCard').hidden = false;
      const bad = steps.find(s => !s.ok);
      note('#diagHint', bad ? `第一步失败：${bad.name}` : '全部步骤通过', !!bad);
    } catch (e) {
      note('#diagHint', e.message, true);
    } finally { btn.disabled = false; }
  }

  /* ---------------- 设置 ---------------- */
  async function loadSettings() {
    const { settings } = await api('/api/settings');
    S.settings = settings;
    $('#setConcurrency').value = settings.concurrency ?? 2;
    $('#setQps').value = settings.qps ?? 2.5;
    $('#setRetries').value = settings.retries ?? 3;
    $('#setTarget').value = settings.default_target ?? '/PanTransfer';
    $('#target').value = settings.default_target ?? '/PanTransfer';
    refreshRates();
  }

  // 自动调速：把每个网盘当前的真实速率显示出来，让用户知道不用自己算
  async function refreshRates() {
    try {
      const s = await api('/api/stats');
      const rates = s.rates || {};
      const parts = Object.entries(rates).map(([name, r]) =>
        `<span class="pill ${r.penalties ? 'wait' : 'ok'}">${esc(name)} ${r.rate}/s</span>`);
      $('#rateInfo').innerHTML = parts.length
        ? `当前实际速率：${parts.join(' ')} <em>（上限 ${S.settings?.qps ?? '-'} /s，被限速会自动降，恢复后自动升）</em>`
        : '<em>还没有请求，速率以上限的一半起步，平稳后会自动升上去</em>';
    } catch (_) { /* 统计接口失败不影响保存 */ }
  }

  async function saveSettings() {
    try {
      await post('/api/settings', {
        concurrency: Number($('#setConcurrency').value),
        qps: Number($('#setQps').value),
        retries: Number($('#setRetries').value),
        default_target: $('#setTarget').value.trim() || '/PanTransfer',
      });
      note('#setHint', '已保存');
      toast('设置已保存', 'ok');
      refreshRates();
    } catch (e) { note('#setHint', e.message, true); }
  }

  /* ---------------- 启动 ---------------- */
  /* ---------------- 网盘管理（浏览自己的网盘） ---------------- */
  const FILES = { provider: '', accountId: '', path: '/', items: [], q: '', sort: 'name' };

  function filesAccount() {
    return S.accounts.filter(a => a.provider === FILES.provider)[0] || null;
  }

  function quotaText(n) {
    if (n === null || n === undefined) return '-';
    if (n < 1024) return n + ' B';
    const u = ['KB', 'MB', 'GB', 'TB'];
    let v = n / 1024, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + ' ' + u[i];
  }

  async function initFiles() {
    const acc = filesAccount() || S.accounts[0] || null;
    if (acc) {
      FILES.provider = acc.provider;
      FILES.accountId = String(acc.id);
    }
    const p = providerInfo(FILES.provider || S.provider);
    $('#filesTitle').textContent = acc ? (acc.name || p.label + ' 网盘') : '我的网盘';
    $('#filesTag').textContent = p.label || '网盘';
    if (!acc) {
      $('#filesQuotaText').textContent = '还没有登录态';
      $('#filesList').innerHTML = '';
      $('#filesEmpty').hidden = false;
      $('#filesEmpty').textContent = '还没有保存任何网盘的登录态，先去「账号管理」扫码登录';
      return;
    }
    loadQuota();
    await loadFiles('/');
  }

  async function loadQuota() {
    try {
      const q = await api(`/api/quota?provider=${encodeURIComponent(FILES.provider)}&account_id=${encodeURIComponent(FILES.accountId)}`);
      const total = q.total || 0, used = q.used || 0;
      $('#filesQuotaText').textContent = total ? `已用 ${quotaText(used)} / ${quotaText(total)}` : '容量未知';
      $('#quotaFill').style.width = total ? Math.min(100, (used / total) * 100).toFixed(1) + '%' : '0%';
    } catch (e) {
      $('#filesQuotaText').textContent = '容量查询失败：' + e.message;
    }
  }

  async function loadFiles(path) {
    FILES.path = path || '/';
    $('#filesNote').textContent = '读取中…';
    $('#filesEmpty').hidden = true;
    try {
      const r = await api(`/api/browse?provider=${encodeURIComponent(FILES.provider)}` +
        `&account_id=${encodeURIComponent(FILES.accountId)}&path=${encodeURIComponent(FILES.path)}`);
      FILES.items = r.items || [];
      renderFiles();
    } catch (e) {
      FILES.items = [];
      $('#filesList').innerHTML = '';
      $('#filesEmpty').hidden = false;
      $('#filesEmpty').textContent = '读取失败：' + e.message;
      $('#filesNote').textContent = '读取失败';
    }
  }

  function crumbsFor(path) {
    const box = $('#filesCrumbs');
    const parts = (path || '/').split('/').filter(Boolean);
    box.innerHTML = '<a data-p="/">全部文件</a>';
    let acc = '';
    parts.forEach(name => {
      acc += '/' + name;
      box.insertAdjacentHTML('beforeend', `<span class="sep">/</span><a data-p="${esc(acc)}">${esc(name)}</a>`);
    });
    $$('#filesCrumbs a').forEach(a => a.onclick = () => loadFiles(a.dataset.p));
  }

  function renderFiles() {
    crumbsFor(FILES.path);
    const q = FILES.q.trim().toLowerCase();
    const list = FILES.items.filter(it => !q || it.name.toLowerCase().includes(q));
    const dirs = list.filter(it => it.is_dir);
    const files = list.filter(it => !it.is_dir);
    if (FILES.sort === 'size') files.sort((a, b) => (b.size || 0) - (a.size || 0));
    if (FILES.sort === 'time') files.reverse();
    dirs.sort((a, b) => a.name.localeCompare(b.name, 'zh'));
    const all = dirs.concat(files);

    $('#filesCount').textContent = `${dirs.length} 个文件夹 · ${files.length} 个文件`;
    $('#filesNote').textContent = FILES.path === '/' ? '根目录' : FILES.path;

    const box = $('#filesList');
    box.innerHTML = '';
    const folderIco = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h4l2 2.5h6.5A2.5 2.5 0 0 1 20.5 10v7A2.5 2.5 0 0 1 18 19.5H5.5A2.5 2.5 0 0 1 3 17z"/></svg>`;
    const fileIco = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>`;

    all.forEach(it => {
      const el = document.createElement('div');
      el.className = 'frow' + (it.is_dir ? ' dir' : '');
      el.innerHTML = `
        <div class="fi">${it.is_dir ? folderIco : fileIco}</div>
        <div class="fn" title="${esc(it.name)}">${esc(it.name)}</div>
        <div class="fs">${it.is_dir ? '-' : quotalike(it.size)}</div>
        <div class="ft">${it.is_dir ? '文件夹' : '文件'}</div>
        <div class="fm">${esc(it.path || '')}</div>`;
      if (it.is_dir) el.onclick = () => loadFiles(it.path);
      box.append(el);
    });
    $('#filesEmpty').hidden = all.length > 0;
    if (!all.length) $('#filesEmpty').textContent = q ? '没有匹配的文件名' : '这个目录是空的';
  }

  function quotalike(n) {
    if (!n) return '-';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
  }

  function syncSortChips() {
    $$('.files-toolbar .chip').forEach(c => {
      const on = (c.id === 'sortSize' && FILES.sort === 'size')
        || (c.id === 'sortTime' && FILES.sort === 'time')
        || (c.dataset.default === '1' && FILES.sort === 'name');
      c.classList.toggle('active', on);
    });
  }

  async function boot() {
    try {
      const meta = await api('/api/meta');
      S.meta = meta;
      $('#footVer').textContent = 'v' + meta.version;
      $('#aboutVer').textContent = 'v' + meta.version;
      if (!meta.providers.some(p => p.name === S.provider)) S.provider = meta.providers[0]?.name || 'mock';
    } catch (e) {
      $('#svc').classList.add('bad');
      $('#svc').lastElementChild.textContent = '服务异常';
      toast('无法连接后端：' + e.message, 'bad');
    }
    renderDrives();
    const sel = $('#accProvider');
    sel.innerHTML = '';
    const real = S.meta.providers.filter(p => p.name !== 'mock');
    real.forEach(p => sel.append(new Option(p.label, p.name)));

    const gsel = $('#guideProvider');
    gsel.innerHTML = '';
    real.forEach(p => gsel.append(new Option(p.label, p.name)));
    gsel.value = sel.value;
    renderGuide(gsel.value);
    gsel.onchange = () => renderGuide(gsel.value);
    sel.onchange = () => { gsel.value = sel.value; renderGuide(sel.value); checkCookieSoon(); };
    $('#accCookie').oninput = checkCookieSoon;

    await Promise.allSettled([loadAccounts(), loadTasks(), loadSettings()]);

    $$('.nav-item').forEach(n => n.onclick = () => go(n.dataset.view));
    $('#links').oninput = countLinks;
    $('#btnParse').onclick = doParse;
    $('#btnClear').onclick = () => { $('#links').value = ''; countLinks(); $('#previewCard').hidden = true; note('#hint', ''); };
    $('#btnCreate').onclick = createTask;
    $('#btnTemp').onclick = () => {
      const el = $('#cred');
      el.style.display = el.style.display === 'none' ? 'block' : 'none';
      if (el.style.display === 'block') el.focus();
    };
    $('#accountSel').onchange = () => {
      const has = S.accounts.some(a => String(a.id) === $('#accountSel').value);
      $('#cred').style.display = haveManual() || !has ? 'block' : 'none';
    };
    $('#btnSaveAcc').onclick = saveAccount;
    $('#btnLogin').onclick = startBrowserLogin;
    $('#btnLoginCancel').onclick = cancelLogin;
    $('#btnSaveSet').onclick = saveSettings;
    $('#btnBrowse').onclick = openDirPicker;
    $('#dirClose').onclick = closeDir;
    $('#dirMask').onclick = closeDir;
    $('#dirUp').onclick = () => loadDir(parentOf(DIR.path));
    $('#dirPick').onclick = pickDir;
    $('#btnDiag').onclick = runDiag;
    $('#diagProvider').onchange = () => { syncDiagAccount(); $('#diagCard').hidden = true; $('#diagRawCard').hidden = true; };
    $('#btnRefresh').onclick = () => { loadTasks(); toast('已刷新'); };
    $('#btnFilesRefresh').onclick = () => { loadFiles(FILES.path); loadQuota(); };
    $('#filesSearch').oninput = () => { FILES.q = $('#filesSearch').value; renderFiles(); };
    $('#sortSize').onclick = () => { FILES.sort = FILES.sort === 'size' ? 'name' : 'size'; syncSortChips(); renderFiles(); };
    $('#sortTime').onclick = () => { FILES.sort = FILES.sort === 'time' ? 'name' : 'time'; syncSortChips(); renderFiles(); };
    $('#fabNew').onclick = () => go('transfer');
    $('#taskFilter').onchange = () => { S.filter = $('#taskFilter').value; loadTasks(); };
    $('#mask').onclick = closeDrawer;
    $('#drawerClose').onclick = closeDrawer;
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if ($('#dirDrawer').classList.contains('on')) closeDir();
      else closeDrawer();
    });

    const view = (location.hash || '').replace('#', '');
    go(TITLES[view] ? view : 'transfer');
    countLinks();

    clearInterval(S.timer);
    S.timer = setInterval(() => {
      loadTasks().catch(() => {});
      if (S.openTask) openDrawer(S.openTask).catch(() => {});
    }, 1500);
  }

  function haveManual() {
    return $('#cred').value.trim().length > 0;
  }

  boot();
})();
