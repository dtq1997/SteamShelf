# -*- mode: python ; coding: utf-8 -*-
"""SteamShelf PyInstaller spec — 最小验证版"""

import os
import sys
import certifi
import pypinyin

block_cipher = None

# pypinyin 数据文件（拼音字典）
pypinyin_dir = os.path.dirname(pypinyin.__file__)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        (certifi.where(), 'certifi'),
        (pypinyin_dir, 'pypinyin'),
    ],
    hiddenimports=[
        'pypinyin',
        'certifi',
        'websocket',
        'websocket._abnf',
        'websocket._core',
        'websocket._exceptions',
        'websocket._http',
        'websocket._logging',
        'websocket._socket',
        'websocket._ssl_compat',
        'websocket._url',
        'multiprocessing.resource_tracker',
        'multiprocessing.popen_spawn_posix',
        'multiprocessing.popen_fork',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'PIL', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SteamShelf',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='SteamShelf',
)

app = BUNDLE(
    coll,
    name='SteamShelf.app',
    bundle_identifier='com.steamshelf.app',
)
