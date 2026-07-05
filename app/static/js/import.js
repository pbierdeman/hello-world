'use strict';

// Formula import: drag-drop / browse / paste → parse via /api/import →
// populate the component table (reusing addRow from formula.js). Parsed data
// always lands in the editable grid; nothing is generated automatically.
(function () {
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('import-file');
  const browseBtn = document.getElementById('browse-btn');
  const togglePaste = document.getElementById('toggle-paste');
  const pasteArea = document.getElementById('paste-area');
  const pasteText = document.getElementById('paste-text');
  const parsePasteBtn = document.getElementById('parse-paste-btn');
  const statusEl = document.getElementById('import-status');
  const rowsBody = document.getElementById('component-rows');

  if (!dropZone) return;

  // ── File selection ──
  browseBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) sendFile(fileInput.files[0]);
  });

  // ── Drag & drop ──
  ['dragenter', 'dragover'].forEach(ev =>
    dropZone.addEventListener(ev, e => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    }));
  ['dragleave', 'drop'].forEach(ev =>
    dropZone.addEventListener(ev, e => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
    }));
  dropZone.addEventListener('drop', e => {
    const file = e.dataTransfer.files[0];
    if (file) sendFile(file);
  });

  // ── Paste ──
  togglePaste.addEventListener('click', () => {
    pasteArea.style.display = pasteArea.style.display === 'none' ? 'block' : 'none';
  });
  parsePasteBtn.addEventListener('click', () => {
    const text = pasteText.value.trim();
    if (!text) { showStatus('warning', ['Paste some rows first.']); return; }
    postImport({ body: JSON.stringify({ text }), headers: { 'Content-Type': 'application/json' } });
  });

  // ── Requests ──
  function sendFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    postImport({ body: fd });
  }

  async function postImport(opts) {
    setBusy(true);
    try {
      const resp = await fetch('/api/import', Object.assign({ method: 'POST' }, opts));
      const data = await resp.json();
      handleResult(data);
    } catch (err) {
      showStatus('danger', ['Import failed. Please try again or enter components manually.']);
    } finally {
      setBusy(false);
    }
  }

  // ── Populate the table ──
  function handleResult(data) {
    const comps = data.components || [];
    const warnings = data.warnings || [];

    if (!comps.length) {
      showStatus('warning', warnings.length ? warnings
        : ['No components could be read from that file.']);
      return;
    }

    if (rowsBody.querySelectorAll('.component-row').length &&
        hasAnyValue() &&
        !confirm(`Replace the current ${rowsBody.querySelectorAll('.component-row').length} row(s) `
                 + `with ${comps.length} imported component(s)?`)) {
      return;
    }

    // Clear existing rows, then add one per imported component
    rowsBody.querySelectorAll('.component-row').forEach(r => r.remove());
    comps.forEach(c => {
      addRow();                                   // global from formula.js
      const row = rowsBody.lastElementChild;
      row.querySelector('.chem-name').value = c.name || '';
      row.querySelector('.chem-cas').value = c.cas || '';
      if (c.pct) row.querySelector('.chem-pct').value = c.pct;
    });
    if (typeof updateTotal === 'function') updateTotal();

    const msgs = [`Imported ${comps.length} component(s) from ${data.source.toUpperCase()}. `
                  + `Looking up hazard data…`].concat(warnings);
    showStatus(comps.length && !warnings.length ? 'success' : 'info', msgs);

    // Fire lookups sequentially so we don't hammer the live PubChem fallback
    lookupSequentially(Array.from(rowsBody.querySelectorAll('.component-row')));
  }

  function lookupSequentially(rows, i = 0) {
    if (i >= rows.length) return;
    const btn = rows[i].querySelector('.lookup-btn');
    if (btn) btn.click();
    setTimeout(() => lookupSequentially(rows, i + 1), 350);
  }

  function hasAnyValue() {
    return Array.from(rowsBody.querySelectorAll('.component-row')).some(r =>
      r.querySelector('.chem-name').value.trim() ||
      r.querySelector('.chem-cas').value.trim());
  }

  // ── UI helpers ──
  function setBusy(busy) {
    dropZone.classList.toggle('busy', busy);
    if (busy) showStatus('info', ['Parsing…']);
  }

  function showStatus(kind, messages) {
    statusEl.innerHTML = `<div class="alert alert-${kind} mb-0 py-2 small">`
      + messages.map(m => `<div>${escapeHtml(m)}</div>`).join('')
      + `</div>`;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
})();
