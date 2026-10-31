# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the SightVault desktop app (onedir; .app bundle on macOS).

Driven by build_macos.sh / build_windows.ps1, which set:
  SIGHTVAULT_STAGE   staging folder written by stage_resources.py
  SIGHTVAULT_TARGET  macos-arm64 | windows-x64
  SIGHTVAULT_CODESIGN_IDENTITY  optional macOS Developer ID identity (unset: ad-hoc signature)
No model weights, virtual environments or user data are collected.
"""
import os
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]
STAGE = Path(os.environ['SIGHTVAULT_STAGE']).resolve()
TARGET = os.environ['SIGHTVAULT_TARGET']
if TARGET not in ('macos-arm64', 'windows-x64'):
    raise SystemExit('SIGHTVAULT_TARGET must be macos-arm64 or windows-x64')
MAC = TARGET == 'macos-arm64'
sys.path.insert(0, str(ROOT))
from sightvault.desktop import APP_ID, APP_NAME, APP_VERSION  # noqa: E402

datas = [
    (str(ROOT / 'sightvault' / 'web'), 'sightvault/web'),
    (str(ROOT / 'sightvault' / 'desktop' / 'web'), 'sightvault/desktop/web'),
    (str(STAGE / 'samples'), 'samples'),
    (str(STAGE / 'build-info.json'), '.'),
    (str(ROOT / 'scripts' / 'prepare_mac_models.py'), 'scripts'),
    (str(ROOT / 'docs' / 'DESKTOP_RELEASE.md'), 'docs'),
]
for name in ('MODEL_MANIFEST.json', 'MAC_CONVERTED_MODEL_MANIFEST.json', 'SAMPLE_MANIFEST.json',
             'DEMO_EVIDENCE_MANIFEST.json', 'download_pinned_models.py', 'verify_transfer.py'):
    datas.append((str(ROOT / 'handoff' / name), 'handoff'))
if (STAGE / 'demo' / 'sightvault.sqlite3').is_file():
    datas.append((str(STAGE / 'demo'), 'demo'))

binaries = []
hiddenimports = [
    'sightvault.server', 'sightvault.models', 'sightvault.desktop.worker',
    # Imported lazily by the setup worker and the runtime.
    'huggingface_hub', 'rapidocr_onnxruntime', 'onnxruntime',
]


def add_all(package):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hidden)


# Packages that load submodules, configs or model code by name at runtime.
for package in ('sentence_transformers', 'rapidocr_onnxruntime', 'webview'):
    add_all(package)
hiddenimports += collect_submodules('transformers.models')
if MAC:
    for package in ('mlx', 'mlx_vlm'):
        add_all(package)

# Version checks at import time (transformers, mlx-vlm provenance) read package metadata.
REQUIRED_METADATA = ['torch', 'transformers', 'tokenizers', 'safetensors', 'huggingface-hub',
                     'sentence-transformers', 'numpy', 'pillow', 'fastapi', 'uvicorn', 'starlette', 'pydantic',
                     'onnxruntime', 'rapidocr-onnxruntime', 'pywebview']
# Transitive packages whose presence depends on the resolved lock; copied when installed.
OPTIONAL_METADATA = ['torchvision', 'accelerate', 'tqdm', 'regex', 'requests', 'httpx', 'packaging',
                     'filelock', 'pyyaml', 'sentencepiece', 'protobuf', 'einops']
if MAC:
    REQUIRED_METADATA += ['mlx', 'mlx-metal', 'mlx-vlm']
for distribution in REQUIRED_METADATA:
    datas += copy_metadata(distribution)
from importlib.metadata import PackageNotFoundError, distribution as _installed  # noqa: E402
for name in OPTIONAL_METADATA:
    try:
        _installed(name)
    except PackageNotFoundError:
        print(f'Package metadata not installed, skipped: {name}')
        continue
    datas += copy_metadata(name)

a = Analysis(
    [str(ROOT / 'packaging' / 'desktop' / 'entry.py')],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    excludes=['tkinter', 'pytest', '_pytest', 'IPython', 'matplotlib', 'playwright', 'notebook', 'jupyter'],
    # These packages inspect their own source files at runtime.
    module_collection_mode={'torch': 'pyz+py', 'transformers': 'pyz+py', 'sentence_transformers': 'pyz+py',
                            **({'mlx_vlm': 'pyz+py'} if MAC else {})},
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
identity = os.environ.get('SIGHTVAULT_CODESIGN_IDENTITY') or None
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    icon=str(ROOT / 'packaging' / 'desktop' / 'icons' / ('SightVault.icns' if MAC else 'SightVault.ico')),
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64' if MAC else None,
    codesign_identity=identity if MAC else None,
    entitlements_file=str(ROOT / 'packaging' / 'desktop' / 'entitlements.plist') if (MAC and identity) else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)
if MAC:
    app = BUNDLE(
        coll,
        name=APP_NAME + '.app',
        icon=str(ROOT / 'packaging' / 'desktop' / 'icons' / 'SightVault.icns'),
        bundle_identifier=APP_ID,
        version=APP_VERSION,
        info_plist={
            'CFBundleName': APP_NAME,
            'CFBundleDisplayName': APP_NAME,
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            'LSMinimumSystemVersion': '14.0',
            'LSArchitecturePriority': ['arm64'],
            'LSApplicationCategoryType': 'public.app-category.productivity',
            'NSHighResolutionCapable': True,
            # The window only loads the app's own http://127.0.0.1 server; no arbitrary loads.
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
        },
    )
