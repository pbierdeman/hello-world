'use strict';

// Formula library: load a saved revision into the builder, generate a single
// PDF, batch-generate selected formulas as a ZIP, and delete formulas.
(function () {
  const table = document.querySelector('table');
  const statusEl = document.getElementById('lib-status');
  const batchBtn = document.getElementById('batch-btn');
  const checkAll = document.getElementById('check-all');
  if (!table) return;

  function rowRev(tr) {
    const sel = tr.querySelector('.rev-select');
    return sel ? sel.value : '';
  }

  // ── Row actions (event delegation) ──
  table.addEventListener('click', async (e) => {
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    const id = tr.dataset.id;
    const rev = rowRev(tr);

    if (e.target.closest('.load-btn')) {
      e.preventDefault();
      window.location.href = `/formula?load=${id}&rev=${rev}`;
    } else if (e.target.closest('.gen-btn')) {
      e.preventDefault();
      await generateSingle(id, rev, tr.dataset.name);
    } else if (e.target.closest('.del-btn')) {
      e.preventDefault();
      if (!confirm(`Delete "${tr.dataset.name}" and all its revisions?`)) return;
      const resp = await fetch(`/api/formulas/${id}`, { method: 'DELETE' });
      if (resp.ok) { tr.remove(); syncBatchBtn(); }
      else showStatus('danger', 'Delete failed.');
    }
  });

  // Single-PDF: fetch the revision, rebuild the form, submit to /generate.
  async function generateSingle(id, rev, name) {
    showStatus('info', `Generating SDS for "${name}"…`);
    try {
      const resp = await fetch(`/api/formulas/${id}?rev=${rev}`);
      if (!resp.ok) { showStatus('danger', 'Could not load formula.'); return; }
      const data = await resp.json();

      const f = document.createElement('form');
      f.method = 'POST';
      f.action = '/generate';
      f.style.display = 'none';
      const scalars = ['product_name', 'product_code', 'country', 'language',
        'intended_use', 'company_name', 'company_address', 'company_phone',
        'company_email', 'emergency_phone'];
      scalars.forEach(k => addField(f, k, data[k] || ''));
      (data.components || []).forEach(c => {
        addField(f, 'chem_name', c.name || '');
        addField(f, 'chem_cas', c.cas || '');
        addField(f, 'chem_pct', c.pct != null ? c.pct : '');
      });
      document.body.appendChild(f);
      f.submit();
      setTimeout(() => f.remove(), 1000);
      showStatus('success', `SDS for "${name}" (rev ${rev}) is downloading.`);
    } catch (err) {
      showStatus('danger', 'Generation failed.');
    }
  }

  function addField(form, name, value) {
    const inp = document.createElement('input');
    inp.type = 'hidden';
    inp.name = name;
    inp.value = value;
    form.appendChild(inp);
  }

  // ── Batch selection ──
  if (checkAll) {
    checkAll.addEventListener('change', () => {
      document.querySelectorAll('.row-check').forEach(c => { c.checked = checkAll.checked; });
      syncBatchBtn();
    });
  }
  table.addEventListener('change', (e) => {
    if (e.target.classList.contains('row-check')) syncBatchBtn();
  });

  function selectedItems() {
    return Array.from(document.querySelectorAll('.row-check'))
      .filter(c => c.checked)
      .map(c => {
        const tr = c.closest('tr[data-id]');
        return { formula_id: parseInt(tr.dataset.id, 10), revision: parseInt(rowRev(tr), 10) };
      });
  }

  function syncBatchBtn() {
    if (batchBtn) batchBtn.disabled = selectedItems().length === 0;
  }

  if (batchBtn) {
    batchBtn.addEventListener('click', async () => {
      const items = selectedItems();
      if (!items.length) return;
      batchBtn.disabled = true;
      const original = batchBtn.innerHTML;
      batchBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Generating…';
      showStatus('info', `Generating ${items.length} SDS document(s)…`);
      try {
        const resp = await fetch('/generate-batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          showStatus('danger', err.error || 'Batch generation failed.');
        } else {
          const blob = await resp.blob();
          downloadBlob(blob, 'SDS_batch.zip');
          showStatus('success', `Downloaded ${items.length} SDS document(s) as a ZIP.`);
        }
      } catch (err) {
        showStatus('danger', 'Batch generation failed.');
      } finally {
        batchBtn.innerHTML = original;
        syncBatchBtn();
      }
    });
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function showStatus(kind, msg) {
    statusEl.innerHTML = `<div class="alert alert-${kind} py-2">${msg}</div>`;
  }
})();
