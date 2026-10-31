"""Desktop-only HTTP routes, added to the existing app by the desktop shell.

Every /desktop/api route requires the per-launch token. The token is never
served over HTTP: the shell injects it into pages of the app's own origin, so
other local processes and pages from other origins cannot trigger setup tasks,
dialogs or browser launches. The app's existing cross-origin guard and CSP
still apply to these routes.
"""
from pathlib import Path
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .instance import TOKEN_HEADER

WEB = Path(__file__).resolve().parent / 'web'


class ExternalLink(BaseModel):
    url: str = Field(max_length=2048)


def install_desktop_routes(app: FastAPI, controller):
    def require_token(token: str | None = Header(default=None, alias=TOKEN_HEADER)):
        if not token or not secrets.compare_digest(token, controller.token):
            raise HTTPException(403, 'This action is only available from the SightVault desktop window.')

    guarded = [Depends(require_token)]

    @app.get('/desktop/setup', include_in_schema=False)
    def setup_page():
        return FileResponse(WEB / 'setup.html')

    @app.post('/desktop/api/focus', dependencies=guarded)
    def focus():
        controller.focus()
        return {'focused': True}

    @app.get('/desktop/api/diagnostics', dependencies=guarded)
    def diagnostics():
        return controller.diagnostics()

    @app.post('/desktop/api/window-ready', dependencies=guarded)
    def window_ready():
        if controller.window is None:
            raise HTTPException(409, 'No native window is attached.')
        controller.window_ready.set()
        return {'acknowledged': True}

    @app.post('/desktop/api/choose-folder', dependencies=guarded)
    def choose_folder():
        try:
            return {'path': controller.choose_folder()}
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/desktop/api/open-external', dependencies=guarded)
    def open_external(body: ExternalLink):
        try:
            controller.open_external(body.url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {'opened': True}

    @app.get('/desktop/api/setup', dependencies=guarded)
    def setup_status():
        return controller.runner.status()

    @app.post('/desktop/api/setup/cancel', dependencies=guarded)
    def setup_cancel():
        return controller.runner.cancel()

    @app.post('/desktop/api/setup/{action}', dependencies=guarded)
    def setup_start(action: str):
        try:
            return controller.runner.start(action)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/desktop/api/demo/import', dependencies=guarded)
    def demo_import():
        try:
            return controller.import_demo()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    app.mount('/desktop/static', StaticFiles(directory=WEB), name='desktop-static')
    return app
