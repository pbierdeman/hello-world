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

  // Allow pressing Enter in name/CAS field to trigger lookup
  [row.querySelector('.chem-name'), row.querySelector('.chem-cas')].forEach(el => {
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        row.querySelector('.lookup-btn').click();
      }
    });
  });
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

// Form submit feedback
form.addEventListener('submit', () => {
  const btn = document.getElementById('generate-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Generating PDF…';
  statusMsg.textContent = 'Looking up chemicals and building SDS. This may take 10–30 seconds…';
  // Re-enable after timeout in case of error
  setTimeout(() => {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-file-earmark-pdf me-2"></i>Generate SDS PDF';
    statusMsg.textContent = '';
  }, 60000);
});

// Start with two blank rows
addBtn.addEventListener('click', addRow);
addRow();
addRow();
