# PyInstaller spec — builds KatanaToneStream as a single-file Windows executable.
# Build with:  uv run pyinstaller --noconfirm KatanaToneStream.spec
from PyInstaller.utils.hooks import collect_all, collect_data_files

# litellm ships token-cost json and provider configs as data; pull it all in.
litellm_datas, litellm_binaries, litellm_hiddenimports = collect_all("litellm")

datas = [
    ("src/katana_tonestream/assets", "katana_tonestream/assets"),
    *litellm_datas,
    *collect_data_files("flet"),
]

hiddenimports = [
    *litellm_hiddenimports,
    # keyring resolves its OS backend at runtime via entry points.
    "keyring.backends.Windows",
    # tiktoken discovers encodings (cl100k_base etc.) via the tiktoken_ext
    # namespace package — PyInstaller can't see this dynamic plugin import.
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
]

a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=litellm_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KatanaToneStream",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon="src/katana_tonestream/assets/logo.ico",
)
