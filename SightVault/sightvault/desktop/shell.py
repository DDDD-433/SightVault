"""Native window (pywebview) around the loopback server.

No Python functions are exposed to page JavaScript through pywebview's js_api.
Desktop actions go through the token-guarded /desktop/api routes instead, and
the token is injected only into pages of the app's own loopback origin.
"""
import html
import logging
import os

from . import APP_NAME
from .controller import same_origin

log = logging.getLogger(__name__)

_PAGE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>'
         '<style>body{{font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f4ef;'
         'color:#22201c;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}'
         'main{{max-width:560px;padding:24px}}h1{{font-size:20px}}code{{word-break:break-all}}</style></head>'
         '<body><main><h1>{title}</h1>{body}</main></body></html>')


def page(title, body_html):
    return _PAGE.format(title=html.escape(title), body=body_html)


STARTING = page('Starting SightVault', '<p>Opening your local library. Models load only when they are needed.</p>')


def failure_page(paths, detail):
    return page('SightVault could not start', (
        f'<p>{html.escape(detail)}</p><p>The log file is at <code>{html.escape(str(paths.log_file))}</code>.</p>'
        '<p>Quit and reopen SightVault to try again. Your library was not changed.</p>'))


class WindowShell:
    def __init__(self, controller, server, startup_timeout=90.0, ui_smoke=False):
        self.controller = controller
        self.server = server
        self.startup_timeout = startup_timeout
        self.window = None
        self.ui_smoke = ui_smoke
        self.smoke_result = {'ready': False, 'error': 'Native window did not acknowledge startup.'}
        self._redirects = 0
        self._blocked = False

    def _settings(self, webview):
        settings = getattr(webview, 'settings', None)
        if isinstance(settings, dict):
            # target=_blank links (for example "Open full size") open in the system browser.
            settings.update({'OPEN_EXTERNAL_LINKS_IN_BROWSER': True, 'ALLOW_DOWNLOADS': False,
                             'OPEN_DEVTOOLS_IN_DEBUG': False})

    def on_loaded(self, *args):
        controller, window = self.controller, self.window
        if not controller.ready.is_set() or window is None or self._blocked:
            return
        try:
            current = window.get_current_url()
        except Exception:
            log.exception('Could not read the window URL')
            return
        if not current:
            # Unknown URL: do not inject the token, and do not risk a reload loop.
            log.warning('Window URL unavailable; desktop integration not injected')
            return
        if not same_origin(current, controller.url):
            # Only the local app may be shown in this window; its token never reaches other pages.
            self._redirects += 1
            log.warning('Blocked navigation away from the local app')
            if self._redirects <= 3:
                window.load_url(controller.url + '/')
            elif not self._blocked:
                # Stop redirecting; the static notice page itself is never redirected or given the token.
                self._blocked = True
                window.load_html(page('Navigation blocked', '<p>This window only shows your local SightVault '
                                      'library. Quit and reopen SightVault to continue.</p>'))
            return
        self._redirects = 0
        try:
            self.controller.window_renderer = getattr(getattr(window, 'gui', None), 'renderer', None)
            # evaluate_js wraps its input in eval(), which violates script-src 'self'.
            # run_js executes this trusted native-injected script without that wrapper.
            window.run_js(controller.inject_script())
        except Exception:
            log.exception('Desktop integration script failed; the typed folder path still works')

    def on_closing(self, *args):
        message = self.controller.busy_message()
        if not message:
            return True
        ask = getattr(self.window, 'create_confirmation_dialog', None)
        if not callable(ask):
            return True
        try:
            return bool(ask('Quit SightVault?', message))
        except Exception:
            log.exception('Quit confirmation failed')
            return True

    def _after_start(self):
        controller, window = self.controller, self.window
        probe_root = str(controller.config.root)
        from .loopback import probe_status
        if self.server.wait_ready(self.startup_timeout, probe=lambda url: probe_status(url, probe_root)):
            controller.ready.set()
            window.load_url(controller.start_url())
            if self.ui_smoke:
                ready = controller.window_ready.wait(self.startup_timeout)
                self.smoke_result = {'ready': ready, 'native_bridge_ready': ready,
                                     'renderer': controller.window_renderer, 'url': controller.url,
                                     'error': None if ready else 'Native page script did not acknowledge startup.'}
                window.destroy()
        else:
            detail = str(self.server.error) if self.server.error else 'The local server did not respond in time.'
            log.error('Startup failed: %s', detail)
            window.load_html(failure_page(controller.paths, detail[:500]))
            if self.ui_smoke:
                self.smoke_result = {'ready': False, 'error': detail[:500]}
                window.destroy()

    def run(self):
        import webview
        self._settings(webview)
        self.window = webview.create_window(APP_NAME, html=STARTING, width=1280, height=840, min_size=(900, 600),
                                            background_color='#f6f4ef', text_select=True, hidden=self.ui_smoke)
        self.controller.attach_window(self.window)
        self.window.events.loaded += self.on_loaded
        self.window.events.closing += self.on_closing
        debug = os.environ.get('SIGHTVAULT_DESKTOP_DEBUG') == '1'
        options = {'private_mode': True, 'debug': debug}
        if os.name == 'nt':
            # Require the Edge WebView2 engine; never fall back to a legacy engine that cannot run the UI.
            options['gui'] = 'edgechromium'
        try:
            # private_mode keeps no cookies or web storage between launches; the UI does not need them.
            webview.start(self._after_start, **options)
        except Exception as exc:
            log.exception('The native window could not start')
            if not self.ui_smoke:
                native_alert('SightVault could not open its window',
                         'The system web view failed to start. On Windows, install or repair the Microsoft Edge '
                         f'WebView2 Runtime. Details are in {self.controller.paths.log_file}.\n\n{str(exc)[:300]}')
            raise


def native_alert(title, message):
    """Last-resort message when the web view itself is unavailable (Windows only)."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        log.exception('Could not show the startup error dialog')
