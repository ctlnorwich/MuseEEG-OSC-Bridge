# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


hiddenimports = []
hiddenimports += collect_submodules('muselsl')
hiddenimports += collect_submodules('bleak')
hiddenimports += collect_submodules('bitstring')
hiddenimports += collect_submodules('bitarray')
hiddenimports += collect_submodules('pygatt')

datas = []
datas += collect_data_files('muselsl')
datas += collect_data_files('pylsl')  # includes pylsl/lib/liblsl.dll|dylib|so


a = Analysis(
    ['gui_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='Muse-OSC-Bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file='entitlements.plist',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Muse-OSC-Bridge',
)
app = BUNDLE(
    coll,
    name='Muse-OSC-Bridge.app',
    icon=None,
    bundle_identifier='com.museoscbridge.app',
    info_plist={
        'NSBluetoothAlwaysUsageDescription': 'Required to connect to the Muse EEG headband over BLE.',
        'NSBluetoothPeripheralUsageDescription': 'Required to connect to the Muse EEG headband over BLE.',
        'CFBundleShortVersionString': '0.1.0',
        'CFBundleVersion': '1',
        'LSMinimumSystemVersion': '11.0',
    },
)
