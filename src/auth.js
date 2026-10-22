/* Sign-in for the college pilot. Uses the existing visual style; injects a login overlay and a
   small "signed in as …" chip so no other markup changes. When AUTH_REQUIRED is off the app stays
   open and shows a subtle banner. */
'use strict';

(function () {
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function injectStyle() {
    if (document.getElementById('auth-style')) return;
    const s = document.createElement('style');
    s.id = 'auth-style';
    s.textContent = `
      #auth-overlay { position:fixed; inset:0; background:rgba(13,27,42,0.92); z-index:2000;
        display:flex; align-items:center; justify-content:center; }
      #auth-card { background:#fff; border-radius:10px; padding:28px 30px; width:340px; max-width:92vw;
        box-shadow:0 10px 40px rgba(0,0,0,0.35); }
      #auth-card h2 { margin:0 0 4px; color:#003d7c; font-size:1.3rem; }
      #auth-card p { margin:0 0 16px; color:#667; font-size:0.82rem; }
      #auth-card label { display:block; font-size:0.78rem; color:#425066; margin:10px 0 4px; font-weight:600; }
      #auth-card input { width:100%; padding:9px 10px; border:1px solid #ccd; border-radius:6px; font-size:0.9rem; }
      #auth-card button { width:100%; margin-top:18px; padding:10px; background:#003d7c; color:#fff; border:none;
        border-radius:6px; font-weight:700; font-size:0.9rem; cursor:pointer; }
      #auth-card button:hover { background:#0a4e96; }
      #auth-error { color:#c0392b; font-size:0.8rem; min-height:1.1em; margin-top:8px; }
      #auth-chip { position:fixed; top:8px; right:14px; z-index:1500; font-size:0.74rem; color:#fff;
        background:rgba(0,0,0,0.25); border-radius:12px; padding:3px 10px; }
      #auth-chip a { color:#ffd; text-decoration:underline; cursor:pointer; margin-left:8px; }
      #auth-openbanner { background:#fff8e1; border-bottom:1px solid #f0d890; color:#7a5c00;
        font-size:0.76rem; text-align:center; padding:4px 10px; }
    `;
    document.head.appendChild(s);
  }

  function showChip(user) {
    let chip = document.getElementById('auth-chip');
    if (!chip) { chip = document.createElement('div'); chip.id = 'auth-chip'; document.body.appendChild(chip); }
    chip.innerHTML = `Signed in: ${esc(user.email)} (${esc(user.role)})<a id="auth-signout">Sign out</a>`;
    document.getElementById('auth-signout').addEventListener('click', async () => {
      try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
      location.reload();
    });
  }

  function showOpenBanner() {
    if (document.getElementById('auth-openbanner')) return;
    const b = document.createElement('div');
    b.id = 'auth-openbanner';
    b.textContent = 'Open mode — no sign-in required. Set AUTH_REQUIRED and create accounts for a shared or hosted deployment.';
    document.body.insertBefore(b, document.body.firstChild);
  }

  function showLogin() {
    if (document.getElementById('auth-overlay')) return;
    const o = document.createElement('div');
    o.id = 'auth-overlay';
    o.innerHTML = `
      <form id="auth-card" autocomplete="on">
        <h2>PharmaWatch</h2>
        <p>Sign in to continue. Research and education use only.</p>
        <label for="auth-email">Email</label>
        <input id="auth-email" type="email" autocomplete="username" required />
        <label for="auth-pw">Password</label>
        <input id="auth-pw" type="password" autocomplete="current-password" required />
        <button type="submit">Sign in</button>
        <div id="auth-error"></div>
      </form>`;
    document.body.appendChild(o);
    o.querySelector('#auth-card').addEventListener('submit', async e => {
      e.preventDefault();
      const err = document.getElementById('auth-error');
      err.textContent = '';
      try {
        const r = await fetch('/api/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: document.getElementById('auth-email').value,
                                 password: document.getElementById('auth-pw').value })
        });
        const d = await r.json();
        if (r.ok) { location.reload(); }
        else err.textContent = d.error || 'Sign in failed.';
      } catch (e2) { err.textContent = 'Account service unavailable.'; }
    });
  }

  // A 401 with {auth:"required"} from any call surfaces the login overlay.
  window.authHandleUnauthorized = showLogin;
  const _fetch = window.fetch;
  window.fetch = function (...args) {
    return _fetch.apply(this, args).then(resp => {
      if (resp.status === 401) {
        resp.clone().json().then(b => { if (b && b.auth === 'required') showLogin(); }).catch(() => {});
      }
      return resp;
    });
  };

  document.addEventListener('DOMContentLoaded', async () => {
    injectStyle();
    try {
      const d = await _fetch('/api/auth/status').then(r => r.json());
      if (!d.auth_required) { showOpenBanner(); return; }
      if (d.user) showChip(d.user); else showLogin();
    } catch (e) { /* status unavailable: leave the app as-is */ }
  });
})();
