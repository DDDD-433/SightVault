'use strict';
(function () {
  const $ = id => document.getElementById(id);
  const bytes = value => {
    const units = ['B', 'KiB', 'MiB', 'GiB'];
    let size = Number(value) || 0, unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  };
  let desktop = null, poll = null;

  function notice(message) { $('setup-notice').textContent = message || ''; $('setup-notice').hidden = !message; }
  function cell(text, code) {
    const td = document.createElement('td');
    if (code) { const c = document.createElement('code'); c.textContent = text; td.appendChild(c); } else td.textContent = text;
    return td;
  }
  function term(list, name, value) {
    const dt = document.createElement('dt'); dt.textContent = name;
    const dd = document.createElement('dd'); dd.textContent = value === null || value === undefined ? '—' : String(value);
    list.append(dt, dd);
  }

  function renderModels(data) {
    const rows = $('model-rows');
    rows.replaceChildren();
    for (const group of data.models) {
      const tr = document.createElement('tr');
      const name = `${group.repo || group.name}${group.required ? '' : ' (optional)'}`;
      tr.append(cell(name), cell(String(group.revision || '').slice(0, 12), true),
        cell(`${group.present} / ${group.files}${group.complete ? ' · present' : ''}`), cell(bytes(group.bytes)));
      rows.appendChild(tr);
    }
    const required = data.models.filter(group => group.required);
    const missing = required.filter(group => !group.complete).reduce((sum, group) => sum + group.bytes - group.present_bytes, 0);
    const summary = $('model-summary');
    summary.className = data.required_models_present ? 'state-ok' : 'state-missing';
    summary.textContent = data.required_models_present
      ? 'All required model files are present (sizes checked; use Verify checksums for a full SHA-256 check).'
      : `Required model files are missing. About ${bytes(missing)} will be downloaded from Hugging Face at the pinned revisions.`;
    const mlx = data.mlx;
    $('action-convert').hidden = !mlx.supported;
    $('mlx-note').hidden = !mlx.supported;
    if (mlx.supported) {
      const ready = mlx.conversions.every(item => item.present);
      $('mlx-note').textContent = ready
        ? 'Apple Silicon MLX checkpoints are present; OCR and image analysis use them.'
        : 'Recommended on this Mac: prepare the downloaded MiniCPM models for the tested Metal runtime. This runs locally after the download.';
    }
    $('demo-card').hidden = !(data.demo.bundled && data.demo.library_assets === 0);
  }

  function renderDiagnostics(data) {
    const list = $('diagnostics');
    list.replaceChildren();
    term(list, 'App', `${data.app.name} ${data.app.version}${data.app.frozen ? '' : ' (source)'}`);
    term(list, 'Platform', `${data.app.platform} · ${data.app.machine} · Python ${data.app.python}`);
    term(list, 'Local server', `${data.server.url} (loopback only)`);
    term(list, 'Offline inference', `HF_HUB_OFFLINE=${data.offline.HF_HUB_OFFLINE}, TRANSFORMERS_OFFLINE=${data.offline.TRANSFORMERS_OFFLINE}`);
    term(list, 'Device setting', data.device_setting);
    const acc = data.accelerators || {};
    term(list, 'PyTorch backends', acc.error ? `unavailable: ${acc.error}` : `MPS available ${acc.mps_available} · CUDA available ${acc.cuda_available} (availability only; model status shows actual devices)`);
    for (const [name, state] of Object.entries(data.runtime_status || {})) {
      if (state && typeof state === 'object') term(list, `Model: ${state.name || name}`, `${state.loaded ? 'loaded on ' + state.device : state.available ? 'present, loads on demand' : state.availability_reason || 'not present'}${state.runtime ? ' · ' + state.runtime : ''}${state.error ? ' · last error: ' + state.error.slice(0, 160) : ''}`);
    }
    term(list, 'Data folder', data.paths.data);
    term(list, 'Model folder', `${data.paths.models}${data.paths.models_writable ? '' : ' (read-only)'}`);
    term(list, 'Log file', data.paths.log_file);
    $('diagnostics-json').textContent = JSON.stringify(data, null, 2);
  }

  function renderJob(job) {
    const running = job.state === 'running';
    $('task-card').hidden = job.state === 'idle';
    for (const id of ['action-download', 'action-verify', 'action-convert', 'action-demo']) $(id).disabled = running;
    $('action-cancel').hidden = !running;
    const states = {idle: 'No setup task has run in this session.', running: `${job.label} — running…`,
      succeeded: `${job.label} — finished successfully.`, failed: `${job.label} — failed (exit code ${job.returncode}). See the log below.`,
      cancelled: `${job.label} — stopped.`};
    $('task-state').textContent = states[job.state] || job.state;
    if (job.state !== 'idle') $('task-log').textContent = job.log_tail || '';
    return running;
  }

  async function refresh() {
    try {
      const data = await desktop.call('/desktop/api/diagnostics');
      renderModels(data); renderDiagnostics(data);
      const running = renderJob(data.setup_job);
      clearTimeout(poll);
      if (running) poll = setTimeout(refresh, 1500);
    } catch (error) { notice('Could not read setup status: ' + error.message); }
  }

  async function start(action) {
    notice('');
    try { renderJob(await desktop.call(`/desktop/api/setup/${action}`, {method: 'POST', body: '{}'})); }
    catch (error) { notice(error.message); }
    refresh();
  }

  function connect() {
    if (desktop || !window.sightvaultDesktop) return;
    desktop = window.sightvaultDesktop;
    $('bridge-missing').hidden = true;
    for (const button of document.querySelectorAll('[data-action]')) button.addEventListener('click', () => start(button.dataset.action));
    $('action-cancel').addEventListener('click', async () => {
      try { renderJob(await desktop.call('/desktop/api/setup/cancel', {method: 'POST', body: '{}'})); } catch (error) { notice(error.message); }
      refresh();
    });
    $('action-demo').addEventListener('click', async () => {
      $('action-demo').disabled = true; notice('');
      try {
        const result = await desktop.call('/desktop/api/demo/import', {method: 'POST', body: '{}'});
        notice(`Imported ${result.imported} demo images. Open the library to search them.`);
      } catch (error) { notice(error.message); }
      refresh();
    });
    refresh();
  }

  window.addEventListener('sightvault-desktop-ready', connect);
  document.addEventListener('DOMContentLoaded', () => {
    connect();
    setTimeout(() => { if (!desktop) $('bridge-missing').hidden = false; }, 3000);
  });
})();
