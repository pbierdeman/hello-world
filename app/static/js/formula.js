'use strict';

const tbody = document.getElementById('component-rows');
const tmpl  = document.getElementById('row-template');
const total = document.getElementById('pct-total');
const addBtn = document.getElementById('add-row');
const form  = document.getElementById('sds-form');
const statusMsg = document.getElementById('status-msg');

function addRow() {
  const row = tmpl.content.cloneNode(true).querySelector('tr');
  tbody.appendChild(row);
  bindRow(row);
  updateTotal();
}

function bindRow(row) {
  row.querySelector('.remove-row').addEventListener('click', () => {
    row.remove();
    updateTotal();
  });

  row.querySelector('.chem-pct').addEventListener('input', updateTotal);

  row.querySelector('.lookup-btn').addEventListener('click', async () => {
    const nameEl = row.querySelector('.chem-name');
    const casEl  = row.querySelector('.chem-cas');
    const resultEl = row.querySelector('.lookup-result');
    const btn = row.querySelector('.lookup-btn');

    const query = (casEl.value.trim() || nameEl.value.trim());
    if (!query) {
      resultEl.textContent = 'Enter a name or CAS number first.';
      return;
    }

    btn.classList.add('loading');
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Looking up…';
    resultEl.textContent = '';

    try {
      const resp = await fetch(`/api/lookup?q=${encodeURIComponent(query)}`);
      const data = await resp.json();

      if (data.found) {
        if (data.name && !nameEl.value.trim()) nameEl.value = data.name;
        if (data.cas  && !casEl.value.trim())  casEl.value  = data.cas;

        let html = `<span style="color:#1a3a5c;font-weight:600">${escHtml(data.name)}</span>`;
        if (data.cas) html += ` &nbsp;|&nbsp; CAS: ${escHtml(data.cas)}`;
        if (data.molecular_formula) html += ` &nbsp;|&nbsp; ${escHtml(data.molecular_formula)}`;
        if (data.flash_point) html += `<br>Flash pt: ${escHtml(data.flash_point)}`;
        if (data.signal_word) {
          const cls = data.signal_word === 'Danger' ? 'badge-danger-sm' : 'badge-warning-sm';
          html += ` &nbsp;<span class="${cls}">${escHtml(data.signal_word)}</span>`;
        }
        if (data.h_codes && data.h_codes.length) {
          html += `<br><span style="font-size:.7rem">${data.h_codes.join(', ')}</span>`;
        }
        resultEl.innerHTML = html;
      } else {
        resultEl.innerHTML = '<span style="color:#c0392b">Not found in PubChem. '
          + 'Data will not be included automatically.</span>';
      }
    } catch (err) {
      resultEl.textContent = 'Lookup failed. Check your network connection.';
    } finally {
      btn.classList.remove('loading');
      btn.innerHTML = '<i class="bi bi-search me-1"></i>Look Up';
    }
  });

  // Allow pressing Enter in the CAS field to trigger lookup
  row.querySelector('.chem-cas').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      row.querySelector('.lookup-btn').click();
    }
  });

  bindAutocomplete(row);
}

// ── Search-as-you-type autocomplete on the chemical-name field ──
function bindAutocomplete(row) {
  const nameEl = row.querySelector('.chem-name');
  const casEl  = row.querySelector('.chem-cas');
  const cell   = nameEl.parentElement;
  cell.style.position = 'relative';

  const menu = document.createElement('div');
  menu.className = 'autocomplete-menu';
  menu.style.display = 'none';
  cell.appendChild(menu);

  let items = [];
  let active = -1;
  let timer = null;

  function close() { menu.style.display = 'none'; active = -1; }

  function choose(item) {
    nameEl.value = item.name;
    if (item.cas) casEl.value = item.cas;
    close();
    row.querySelector('.lookup-btn').click();   // auto-fill hazard data
  }

  function render() {
    if (!items.length) { close(); return; }
    menu.innerHTML = items.map((it, i) =>
      `<div class="autocomplete-item${i === active ? ' active' : ''}" data-i="${i}">`
      + `<span class="ac-name">${escHtml(it.name)}</span>`
      + (it.cas ? `<span class="ac-cas">${escHtml(it.cas)}</span>` : '')
      + `</div>`
    ).join('');
    menu.style.display = 'block';
  }

  menu.addEventListener('mousedown', e => {      // mousedown beats blur
    const el = e.target.closest('.autocomplete-item');
    if (el) { e.preventDefault(); choose(items[+el.dataset.i]); }
  });

  nameEl.addEventListener('input', () => {
    const q = nameEl.value.trim();
    clearTimeout(timer);
    if (q.length < 2) { close(); return; }
    timer = setTimeout(async () => {
      try {
        const resp = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`);
        items = await resp.json();
        active = -1;
        render();
      } catch (err) { close(); }
    }, 200);
  });

  nameEl.addEventListener('keydown', e => {
    if (menu.style.display === 'none') {
      if (e.key === 'Enter') { e.preventDefault(); row.querySelector('.lookup-btn').click(); }
      return;
    }
    if (e.key === 'ArrowDown')      { e.preventDefault(); active = Math.min(active + 1, items.length - 1); render(); }
    else if (e.key === 'ArrowUp')   { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
    else if (e.key === 'Enter')     { e.preventDefault(); active >= 0 ? choose(items[active]) : row.querySelector('.lookup-btn').click(); }
    else if (e.key === 'Escape')    { close(); }
  });

  nameEl.addEventListener('blur', () => setTimeout(close, 150));
}

function updateTotal() {
  let sum = 0;
  document.querySelectorAll('.chem-pct').forEach(el => {
    sum += parseFloat(el.value || 0);
  });
  const rounded = Math.round(sum * 100) / 100;
  total.textContent = rounded + '%';
  total.className = '';
  if (rounded > 100.01)       total.classList.add('pct-over');
  else if (rounded >= 99.99)  total.classList.add('pct-ok');
  else                        total.classList.add('pct-under');
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// ── Verification-gated submit ──
// Intercept submit → run the completeness checker → show results in a modal.
// Fails block generation; warnings allow "Generate anyway". A guard flag lets
// the modal button submit natively without re-triggering this handler.
let verified = false;
const verifyModalEl = document.getElementById('verify-modal');
const verifyModal = verifyModalEl ? new bootstrap.Modal(verifyModalEl) : null;
const verifyGenBtn = document.getElementById('verify-generate-btn');

const SEVERITY = {
  fail: { cls: 'danger',  icon: 'x-circle-fill',       label: 'Must fix' },
  warn: { cls: 'warning', icon: 'exclamation-triangle-fill', label: 'Review' },
  pass: { cls: 'info',    icon: 'info-circle-fill',     label: 'Note' },
};

form.addEventListener('submit', async (e) => {
  if (verified) return;              // guard: let the native submit through
  e.preventDefault();

  const btn = document.getElementById('generate-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Verifying…';
  statusMsg.textContent = 'Checking the formula for completeness…';

  let report;
  try {
    const resp = await fetch('/api/verify', { method: 'POST', body: new FormData(form) });
    report = await resp.json();
  } catch (err) {
    statusMsg.textContent = 'Verification failed. Check your connection.';
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-file-earmark-pdf me-2"></i>Generate SDS PDF';
    return;
  }

  renderVerifyReport(report);
  btn.disabled = false;
  btn.innerHTML = '<i class="bi bi-file-earmark-pdf me-2"></i>Generate SDS PDF';
  statusMsg.textContent = '';
  if (verifyModal) verifyModal.show();
});

function renderVerifyReport(report) {
  const summary = document.getElementById('verify-summary');
  const list = document.getElementById('verify-checks');
  const checks = report.checks || [];
  const fails = checks.filter(c => c.severity === 'fail');
  const warns = checks.filter(c => c.severity === 'warn');

  if (report.status === 'fail') {
    summary.innerHTML = `<div class="alert alert-danger mb-0"><i class="bi bi-x-circle me-2"></i>`
      + `<strong>${fails.length} problem(s) must be fixed</strong> before this SDS can be generated.</div>`;
    verifyGenBtn.disabled = true;
    verifyGenBtn.innerHTML = '<i class="bi bi-file-earmark-pdf me-2"></i>Generate PDF';
  } else if (report.status === 'warn') {
    summary.innerHTML = `<div class="alert alert-warning mb-0"><i class="bi bi-exclamation-triangle me-2"></i>`
      + `<strong>${warns.length} item(s) to review.</strong> You can still generate, but confirm these first.</div>`;
    verifyGenBtn.disabled = false;
    verifyGenBtn.innerHTML = '<i class="bi bi-file-earmark-pdf me-2"></i>Generate anyway';
  } else {
    summary.innerHTML = `<div class="alert alert-success mb-0"><i class="bi bi-check-circle me-2"></i>`
      + `<strong>All checks passed.</strong> Ready to generate.</div>`;
    verifyGenBtn.disabled = false;
    verifyGenBtn.innerHTML = '<i class="bi bi-file-earmark-pdf me-2"></i>Generate PDF';
  }

  if (!checks.length) {
    list.innerHTML = '';
    return;
  }
  list.innerHTML = '<ul class="list-group">' + checks.map(c => {
    const s = SEVERITY[c.severity] || SEVERITY.pass;
    const sec = c.section ? `<span class="badge bg-secondary ms-2">Section ${c.section}</span>` : '';
    return `<li class="list-group-item d-flex align-items-start">`
      + `<i class="bi bi-${s.icon} text-${s.cls} me-2 mt-1"></i>`
      + `<div><span class="badge bg-${s.cls} me-2">${s.label}</span>${escHtml(c.message)}${sec}</div></li>`;
  }).join('') + '</ul>';
}

if (verifyGenBtn) {
  verifyGenBtn.addEventListener('click', () => {
    verified = true;                 // bypass the interceptor on the next submit
    if (verifyModal) verifyModal.hide();
    const btn = document.getElementById('generate-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Generating PDF…';
    statusMsg.textContent = 'Building SDS and rendering PDF…';
    form.submit();                   // native submit → hits /generate
  });
}

addBtn.addEventListener('click', addRow);

// ── Save to Library ──
const saveLibBtn = document.getElementById('save-lib-btn');
if (saveLibBtn) {
  saveLibBtn.addEventListener('click', async () => {
    const nameEl = document.querySelector('[name="product_name"]');
    if (!nameEl.value.trim()) {
      statusMsg.textContent = 'Enter a product name before saving.';
      nameEl.focus();
      return;
    }
    saveLibBtn.disabled = true;
    const original = saveLibBtn.innerHTML;
    saveLibBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving…';
    try {
      const resp = await fetch('/api/formulas', { method: 'POST', body: new FormData(form) });
      const data = await resp.json();
      statusMsg.textContent = resp.ok
        ? `Saved "${nameEl.value.trim()}" as revision ${data.revision}.`
        : (data.error || 'Save failed.');
    } catch (err) {
      statusMsg.textContent = 'Save failed. Check your connection.';
    } finally {
      saveLibBtn.disabled = false;
      saveLibBtn.innerHTML = original;
    }
  });
}

// ── Initial rows: prefill from a saved formula, else two blank rows ──
function seedRows() {
  const el = document.getElementById('prefill-data');
  if (el) {
    let comps = [];
    try { comps = JSON.parse(el.textContent) || []; } catch (e) { comps = []; }
    if (comps.length) {
      comps.forEach(c => {
        addRow();
        const row = tbody.lastElementChild;
        row.querySelector('.chem-name').value = c.name || '';
        row.querySelector('.chem-cas').value = c.cas || '';
        if (c.pct) row.querySelector('.chem-pct').value = c.pct;
      });
      updateTotal();
      return;
    }
  }
  addRow();
  addRow();
}
seedRows();
