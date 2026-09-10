# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('data', 'data'), ('reference', 'reference'), ('templates', 'templates')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'keras',
        'sklearn', 'scikit-learn',
        'matplotlib', 'IPython', 'jupyter', 'notebook',
        'pytest', '_pytest',
        'pyarrow', 'duckdb', 'boto3', 'botocore',
        'sounddevice', 'soundfile', 'pydub',
        'transformers', 'datasets', 'onnxruntime',
        'plotly', 'kaleido', 'altair',
        'grpc', 'google.cloud', 'google.api_core',
        'psycopg2', 'sqlalchemy',
        'uvicorn', 'websockets',

    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PlanBA_Metre_Extractor',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PlanBA_Metre_Extractor',
)
