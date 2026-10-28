# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/youss/OneDrive/Desktop/dossier génie civil/metre-ba-agent/config', 'config'), ('C:/Users/youss/OneDrive/Desktop/dossier génie civil/metre-ba-agent/templates', 'templates'), ('C:/Users/youss/OneDrive/Desktop/dossier génie civil/metre-ba-agent/assets', 'assets')]
binaries = []
hiddenimports = ['customtkinter', 'openpyxl', 'pydantic', 'fitz', 'ezdxf', 'PIL', 'reportlab', 'numpy', 'tkinterdnd2', 'sklearn', 'sklearn.cluster', 'sklearn.neighbors', 'shapely', 'shapely.geometry', 'ifcopenshell', 'pdfplumber', 'core.schemas', 'core.calculator', 'core.ingestion', 'core.paths', 'core.validator', 'core.local_extractor', 'core.roi_slicer', 'core.tokenizer', 'core.normalization', 'core.ocr_engine', 'core.populate_modele', 'core.civil_engine', 'core.text_analyzer', 'core.metre_rules', 'core.geometry_analyzer', 'core.dxf_extractor', 'core.ifc_extractor', 'core.level_clustering', 'core.bet_contracts', 'core.moroccan_bet_engine', 'core.populate_modele_bet', 'core.vision_bridge', 'core.surface_metre_excel', 'extractors', 'extractors.surface_extractor', 'extractors.vision_extractor', 'extractors.ocr_router', 'extractors.vlm_router', 'cv2', 'requests', 'ui.desktop_app', 'build_metre', 'optimisation_chantiers', 'rapport_metre', 'extract_plan']
tmp_ret = collect_all('ezdxf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rapidocr_onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/youss/OneDrive/Desktop/dossier génie civil/metre-ba-agent/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'matplotlib', 'scipy', 'statsmodels', 'torch', 'torchvision', 'torchaudio', 'tensorflow', 'keras', 'transformers', 'datasets', 'skimage', 'sklearn', 'xgboost', 'lightgbm', 'networkx', 'sympy', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'shiboken2', 'shiboken6', 'plotly', 'altair', 'dash', 'IPython', 'jupyter', 'notebook', 'pygments', 'lxml', 'pyarrow', 'duckdb', 'numba', 'llvmlite', 'sqlalchemy', 'psycopg2', 'boto3', 'botocore', 'google.cloud', 'google.api_core', 'grpc', 'sounddevice', 'soundfile', 'pydub', 'librosa', 'openvino', 'httpx', 'uvicorn', 'websockets', 'zmq', 'tornado', 'aiohttp', 'opentelemetry', 'mako'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
splash = Splash(
    'C:/Users/youss/OneDrive/Desktop/dossier génie civil/metre-ba-agent/assets/splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    text_size=12,
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
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
    icon=['C:/Users/youss/OneDrive/Desktop/dossier génie civil/metre-ba-agent/assets/app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    splash.binaries,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PlanBA_Metre_Extractor',
)
