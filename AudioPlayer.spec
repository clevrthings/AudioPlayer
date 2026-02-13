# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

rtmidi_datas, rtmidi_binaries, rtmidi_hiddenimports = collect_all('rtmidi')
mido_datas, mido_binaries, mido_hiddenimports = collect_all('mido')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=rtmidi_binaries + mido_binaries,
    datas=[('assets/app_icon.png', 'assets')] + rtmidi_datas + mido_datas,
    hiddenimports=['rtmidi', 'rtmidi._rtmidi', 'mido.backends.rtmidi']
        + rtmidi_hiddenimports + mido_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AudioPlayer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/AudioPlayer.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AudioPlayer',
)
app = BUNDLE(
    coll,
    name='AudioPlayer.app',
    icon='assets/AudioPlayer.icns',
    bundle_identifier=None,
)
