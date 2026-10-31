(function (config) {
  'use strict';
  // Evaluated by the desktop shell only on pages of the app's own loopback origin.
  if (window.sightvaultDesktop) return;
  const call = async (path, options = {}) => {
    const response = await fetch(path, {
      ...options,
      headers: {'Content-Type': 'application/json', 'X-SightVault-Desktop-Token': config.token, ...(options.headers || {})}
    });
    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }
    if (!response.ok) throw new Error(data && typeof data.detail === 'string' ? data.detail : 'The desktop request failed.');
    return data;
  };
  Object.defineProperty(window, 'sightvaultDesktop', {value: Object.freeze({call, version: config.version})});

  const folder = document.getElementById('folder');
  if (folder && !document.getElementById('choose-folder')) {
    const button = document.createElement('button');
    button.id = 'choose-folder';
    button.type = 'button';
    button.className = 'button full';
    button.textContent = 'Choose folder…';
    button.style.marginTop = '8px';
    button.style.marginBottom = '8px';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const data = await call('/desktop/api/choose-folder', {method: 'POST', body: '{}'});
        if (data && data.path) {
          folder.value = data.path;
          folder.dispatchEvent(new Event('input', {bubbles: true}));
          folder.focus();
        }
      } catch (error) {
        const notice = document.getElementById('notice');
        if (notice) { notice.textContent = 'Could not open the folder picker: ' + error.message; notice.hidden = false; }
      } finally {
        button.disabled = false;
      }
    });
    folder.insertAdjacentElement('afterend', button);
  }
  const settings = document.querySelector('#settings .settings-body');
  if (settings && !document.getElementById('desktop-setup-link')) {
    const link = document.createElement('a');
    link.id = 'desktop-setup-link';
    link.className = 'text-button';
    link.href = '/desktop/setup';
    link.textContent = 'Setup and diagnostics';
    settings.appendChild(link);
  }
  window.dispatchEvent(new CustomEvent('sightvault-desktop-ready'));
})
