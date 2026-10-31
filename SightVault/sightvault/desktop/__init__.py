"""Native desktop shell around the local SightVault FastAPI app.

The shell opens the existing web UI in a native webview, runs the server on a
loopback port inside the same process and keeps user data in per-user folders.
Model inference, retrieval and the web UI are unchanged.
"""

APP_NAME = 'SightVault'
APP_VERSION = '0.1.0'
# Reverse-DNS identifier used for the macOS bundle and Windows AppUserModelID.
APP_ID = 'io.github.dddd-433.sightvault'
