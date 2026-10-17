/*
 * Drives the Prescription Review page in a real headless Chrome through the whole workflow:
 * open the tab, upload a fixture, check the extracted rows, confirm, wait for the report, and check
 * what the page shows. Needs the app running on 127.0.0.1:5000 and Node 22+ (built-in WebSocket).
 *
 *   node scripts/validation/ui_prescription_flow.js <chrome.exe> <fixture.pdf>
 */
const { spawn } = require('child_process');
const http = require('http');
const os = require('os');
const path = require('path');

const [chromePath, fixture] = process.argv.slice(2);
const PORT = 9333;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const getJson = (url) => new Promise((res, rej) => http.get(url, r => { let d = ''; r.on('data', c => d += c); r.on('end', () => res(JSON.parse(d))); }).on('error', rej));

(async () => {
  const profile = path.join(os.tmpdir(), 'pw-ui-' + Date.now());
  const chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--no-sandbox', `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
  let targets;
  for (let i = 0; i < 40; i++) { try { targets = await getJson(`http://127.0.0.1:${PORT}/json`); break; } catch (e) { await sleep(250); } }
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const pending = new Map(); const consoleErrors = [];
  ws.addEventListener('message', (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    if (msg.method === 'Runtime.exceptionThrown') consoleErrors.push(msg.params.exceptionDetails.text + ' ' + (msg.params.exceptionDetails.exception?.description || ''));
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') consoleErrors.push(msg.params.args.map(a => a.value || a.description).join(' '));
  });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); return r.result?.result?.value; };
  const waitFor = async (expr, label, ms = 120000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await evalJs(expr)) return; await sleep(400); } throw new Error('timed out waiting for ' + label); };
  const checks = [];
  const check = (name, ok, extra = '') => { checks.push({ name, ok: !!ok, extra }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (extra ? '  ' + extra : '')); };

  await send('Runtime.enable'); await send('Page.enable'); await send('DOM.enable');
  await send('Page.navigate', { url: 'http://127.0.0.1:5000/' });
  await waitFor(`!!document.querySelector('[data-section="prescription"]')`, 'page load');
  await sleep(1500);

  await evalJs(`document.querySelector('[data-section="prescription"]').click()`);
  await waitFor(`document.getElementById('rx-extractor-notice').textContent.includes('this machine') || document.getElementById('rx-extractor-notice').textContent.includes('external service')`, 'extractor notice');
  check('shows which extractor will run before upload', true, await evalJs(`document.getElementById('rx-extractor-notice').textContent`));
  check('research-use statement is visible', await evalJs(`document.querySelector('#prescription .rx-banner').textContent.includes('Research and education use only')`));
  check('only the upload step is visible at first', await evalJs(`getComputedStyle(document.getElementById('rx-step-upload')).display !== 'none' && getComputedStyle(document.getElementById('rx-step-confirm')).display === 'none' && getComputedStyle(document.getElementById('rx-step-report')).display === 'none'`));

  const doc = await send('DOM.getDocument');
  const input = await send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector: '#rx-file' });
  await send('DOM.setFileInputFiles', { files: [fixture], nodeId: input.result.nodeId });
  await evalJs(`document.getElementById('rx-reference').value = 'ui test'; document.getElementById('rx-upload-btn').click()`);
  await waitFor(`getComputedStyle(document.getElementById('rx-step-confirm')).display !== 'none'`, 'confirm step');
  const rows = await evalJs(`document.querySelectorAll('#rx-rows tr').length`);
  check('extracted rows are shown for confirmation', rows >= 2, `${rows} rows`);
  check('the report is not reachable yet', await evalJs(`getComputedStyle(document.getElementById('rx-step-report')).display === 'none'`));
  const first = await evalJs(`JSON.stringify(Array.from(document.querySelectorAll('#rx-rows tr')[0].querySelectorAll('input,select')).map(e => e.value))`);
  check('a row shows the matched ingredient beside the written name', first.includes('"'), first.slice(0, 120));

  await evalJs(`document.getElementById('rx-rows').querySelector('.rx-del').click()`);
  check('a row can be removed', (await evalJs(`document.querySelectorAll('#rx-rows tr').length`)) === rows - 1);
  await evalJs(`document.getElementById('rx-add-row').click()`);
  check('a row can be added', (await evalJs(`document.querySelectorAll('#rx-rows tr').length`)) === rows);
  await evalJs(`document.getElementById('rx-rows').lastElementChild.remove()`);   // drop the blank row again

  await evalJs(`document.getElementById('rx-confirm-btn').click()`);
  await waitFor(`getComputedStyle(document.getElementById('rx-step-report')).display !== 'none'`, 'report', 240000);
  const summary = await evalJs(`document.getElementById('rx-summary').textContent`);
  check('report shows a summary', summary.includes('medications analysed') || summary.includes('medication analysed'), summary.slice(0, 140));
  check('findings are listed with an evidence badge', await evalJs(`document.querySelectorAll('#rx-findings .rx-badge').length > 0`));
  check('pairs table is shown', await evalJs(`document.querySelectorAll('#rx-pairs tr').length > 1`));
  check('limitations (including no dose checking) are shown', await evalJs(`document.getElementById('rx-limitations').textContent.includes('Doses are not checked')`));
  const pdf = await evalJs(`document.getElementById('rx-pdf').getAttribute('href')`);
  check('a PDF download link is offered', pdf.endsWith('/report.pdf'), pdf);
  const text = (await evalJs(`document.getElementById('prescription').innerText`)).toLowerCase();
  check('the page never says a combination is safe', !/safe to|is safe|are safe|appear safe/.test(text));
  check('no JavaScript errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '));

  await evalJs(`document.getElementById('rx-delete-btn').click()`);
  await waitFor(`getComputedStyle(document.getElementById('rx-step-upload')).display !== 'none'`, 'reset after delete');
  check('deleting returns to the upload step', true);

  ws.close(); chrome.kill();
  const failed = checks.filter(c => !c.ok);
  console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch(e => { console.error('ERROR', e.message); process.exit(2); });
