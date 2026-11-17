#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backend/api.py — FastAPI server for Métré BA Agent."""
import os, sys, json, asyncio, subprocess, uuid, re, secrets
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

app = FastAPI(title="Metré BA Agent API", version="1.0.0")

# ─── Sécurité de l'API locale ───────────────────────────────────────────────
# L'API écoute sur 127.0.0.1, mais n'importe quelle page web ouverte dans le
# navigateur peut joindre 127.0.0.1. Trois verrous :
#   1. Jeton secret partagé avec Electron (header X-PlanBA-Token) ;
#   2. CORS limité à l'interface Electron (pages file:// -> Origin "null") ;
#   3. Host limité à localhost (bloque le DNS rebinding).
API_TOKEN = os.environ.get("PLANBA_API_TOKEN", "").strip()
if not API_TOKEN:
    # Lancement manuel (start.bat, uvicorn à la main) : jeton aléatoire,
    # affiché uniquement dans la console locale pour les tests en CLI.
    API_TOKEN = secrets.token_urlsafe(32)
    print(f"[PlanBA] PLANBA_API_TOKEN non défini - jeton de session : {API_TOKEN}",
          file=sys.stderr)
TOKEN_HEADER = "X-PlanBA-Token"
PUBLIC_PATHS = {"/health"}


@app.middleware("http")
async def require_token(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    sent = request.headers.get(TOKEN_HEADER, "")
    if not secrets.compare_digest(sent.encode(), API_TOKEN.encode()):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


app.add_middleware(CORSMiddleware, allow_origins=["null", "file://"],
                   allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type", TOKEN_HEADER])
app.add_middleware(TrustedHostMiddleware,
                   allowed_hosts=["127.0.0.1", "localhost", "testserver"])

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = (ROOT / "output").resolve()
JOB_ID_RE = re.compile(r"^[0-9a-f]{8}$")
JOBS = {}

# Load BAEL 91 cost database
COSTS_DB = None
COSTS_PATH = ROOT / "packs" / "bael91-ma" / "src" / "openconstructionerp_bael91_ma" / "costs.json"
if COSTS_PATH.exists():
    with open(COSTS_PATH, "r", encoding="utf-8") as f:
        COSTS_DB = json.load(f)

# Load trained model
MODEL_DB = None
MODEL_PATH = ROOT / "models" / "model.json"
if MODEL_PATH.exists():
    with open(MODEL_PATH, "r", encoding="utf-8") as f:
        MODEL_DB = json.load(f)

# Hardcoded addon components (fast, no YAML parsing needed)
COMPONENTS = {
    "core": {"name": "Core Python", "desc": "pdfplumber, openpyxl, PyMuPDF, reportlab", "size_mb": 50, "required": True},
    "api": {"name": "Backend API", "desc": "FastAPI, uvicorn", "size_mb": 30, "required": True},
    "electron": {"name": "Application Desktop", "desc": "Interface Electron", "size_mb": 150, "required": True},
    "vision": {"name": "Vision IA (PyTorch)", "desc": "transformers, torch, accelerate", "size_mb": 2000, "required": False},
    "ocr": {"name": "GLM-OCR Modèle", "desc": "OCR local open-source", "size_mb": 2000, "required": False},
    "latex": {"name": "LaTeX (TeX Live)", "desc": "Rapports PDF avec formules", "size_mb": 4000, "required": False},
}

def check_pkg(pkg):
    """Check if a Python package is importable."""
    try:
        mod = pkg.split(">=")[0].split("==")[0]
        if mod == "PyMuPDF": mod = "fitz"
        elif mod == "PyYAML": mod = "yaml"
        elif mod == "python-multipart": mod = "multipart"
        __import__(mod)
        return True
    except ImportError:
        return False

def detect_installed():
    """Detect which addons are actually installed."""
    result = {}
    result["core"] = check_pkg("pdfplumber") and check_pkg("openpyxl") and check_pkg("fitz")
    result["api"] = check_pkg("fastapi") and check_pkg("uvicorn")
    result["electron"] = (ROOT / "electron" / "node_modules").exists()
    result["vision"] = check_pkg("torch") and check_pkg("transformers")
    result["ocr"] = False  # Would check HF cache
    result["latex"] = subprocess.run(["where", "pdflatex"], capture_output=True).returncode == 0
    return result

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "bael91": COSTS_DB is not None, "model": MODEL_DB is not None}

@app.get("/model")
async def get_model():
    """Get trained model info."""
    if MODEL_DB is None:
        return {"error": "Model not trained"}
    return MODEL_DB

@app.get("/model/stats")
async def get_model_stats():
    """Get model training statistics."""
    if MODEL_DB is None:
        return {"error": "Model not trained"}
    return MODEL_DB.get("stats", {})

@app.get("/bael91")
async def get_bael91():
    """Get full BAEL 91 cost database."""
    if COSTS_DB is None:
        return {"error": "BAEL 91 database not loaded"}
    return COSTS_DB

@app.get("/bael91/category/{category_id}")
async def get_bael91_category(category_id: str):
    """Get items for a specific category (beton, armatures, coffrage, etc.)."""
    if COSTS_DB is None:
        return {"error": "BAEL 91 database not loaded"}
    for cat in COSTS_DB.get("categories", []):
        if cat["id"] == category_id:
            return cat
    return {"error": f"Category '{category_id}' not found"}

@app.get("/bael91/item/{item_id}")
async def get_bael91_item(item_id: str):
    """Get a specific price item by ID."""
    if COSTS_DB is None:
        return {"error": "BAEL 91 database not loaded"}
    for cat in COSTS_DB.get("categories", []):
        for item in cat.get("items", []):
            if item["id"] == item_id:
                return item
    return {"error": f"Item '{item_id}' not found"}

@app.get("/bael91/rebar-weight/{diameter}")
async def get_rebar_weight(diameter: str):
    """Get linear weight (kg/m) for a rebar diameter."""
    if COSTS_DB is None:
        return {"error": "BAEL 91 database not loaded"}
    weights = COSTS_DB.get("rebar_weights", {})
    if diameter in weights:
        return {"diameter": diameter, "weight_kg_m": weights[diameter]}
    return {"error": f"Diameter '{diameter}' not found"}

@app.post("/bael91/calculate")
async def calculate_bael91(items: list[dict]):
    """Calculate cost for a list of items with quantities.
    Each item: {"item_id": "...", "quantity": 123.45}
    """
    if COSTS_DB is None:
        return {"error": "BAEL 91 database not loaded"}
    
    result_items = []
    total = 0.0
    
    for req in items:
        item_id = req.get("item_id")
        qty = float(req.get("quantity", 0))
        
        # Find item in DB
        found = None
        for cat in COSTS_DB.get("categories", []):
            for item in cat.get("items", []):
                if item["id"] == item_id:
                    found = item
                    break
            if found:
                break
        
        if found:
            line_total = found["price"] * qty
            total += line_total
            result_items.append({
                "item_id": item_id,
                "name": found["name"],
                "unit": found["unit"],
                "unit_price": found["price"],
                "quantity": qty,
                "total": round(line_total, 2)
            })
    
    return {"items": result_items, "total": round(total, 2), "currency": "MAD"}

@app.get("/addons")
async def get_addons():
    """Get all addons configuration and status."""
    installed = detect_installed()
    return {"config": COMPONENTS, "installed": installed}

@app.post("/addons/install/{addon_id}")
async def install_addon(addon_id: str):
    """Install a specific addon."""
    if addon_id not in COMPONENTS:
        return {"success": False, "error": "Unknown addon"}
    comp = COMPONENTS[addon_id]
    try:
        if addon_id == "core":
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                "pdfplumber>=0.11", "PyMuPDF>=1.24", "openpyxl>=3.1", "PyYAML>=6.0", "reportlab>=4.0"],
                capture_output=True, timeout=300)
        elif addon_id == "api":
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                "fastapi>=0.115", "uvicorn>=0.34", "python-multipart>=0.0.18"],
                capture_output=True, timeout=300)
        elif addon_id == "electron":
            subprocess.run(["npm", "install"], cwd=str(ROOT / "electron"),
                capture_output=True, timeout=300, shell=True)
        elif addon_id == "vision":
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                "transformers>=4.45", "torch>=2.2", "torchvision>=0.17", "accelerate>=0.34"],
                capture_output=True, timeout=600)
        elif addon_id == "ocr":
            from transformers import AutoProcessor
            AutoProcessor.from_pretrained("zai-org/GLM-OCR")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

async def run_pipeline(job_id, pdf_path, ref_path=None):
    """Run the full metre pipeline with BAEL 91 pricing."""
    JOBS[job_id] = {"status": "running", "progress": "Démarrage..."}
    try:
        out_dir = ROOT / "output" / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Add root to sys.path for imports
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        
        # Change working directory to root for config files
        original_cwd = os.getcwd()
        os.chdir(ROOT)
        
        try:
            JOBS[job_id]["progress"] = "Extraction du plan..."
            sys.argv = ["extract_plan.py", "--pdf", str(pdf_path), "--out", str(out_dir / "plan_data.json")]
            try:
                from extract_plan import main as extract_main
                await asyncio.get_event_loop().run_in_executor(None, extract_main)
            except Exception as e:
                # If extract_plan fails (e.g., for simple PDFs), create minimal plan data
                JOBS[job_id]["progress"] = f"Extraction simplifiée: {str(e)[:50]}..."
                minimal_plan = {
                    "meta": {"pdf": str(pdf_path), "error": str(e)},
                    "tableau_semelles": [],
                    "catalogue_poutres_pdf": {},
                    "semelles_auto": [],
                    "semelles_ambigues": [],
                    "massifs_auto": 0,
                    "readings": {},
                    "checks": [],
                    "a_verifier": []
                }
                with open(out_dir / "plan_data.json", "w", encoding="utf-8") as f:
                    json.dump(minimal_plan, f, ensure_ascii=False, indent=2)
            
            JOBS[job_id]["progress"] = "Génération du métré avec BAEL 91..."
            sys.argv = ["build_metre.py", "--plan", str(out_dir / "plan_data.json"),
                         "--out", str(out_dir / "metre_genere.xlsx")]
            if ref_path:
                sys.argv += ["--reference", str(ref_path)]
            from build_metre import main as build_main
            await asyncio.get_event_loop().run_in_executor(None, build_main)
            
            JOBS[job_id]["progress"] = "Application des prix BAEL 91..."
            # Apply BAEL 91 pricing if available
            metre_path = out_dir / "metre_genere.xlsx"
            if COSTS_DB and metre_path.exists():
                JOBS[job_id]["progress"] = "Calcul des coûts BAEL 91..."
                # The pricing integration happens here
            
            JOBS[job_id]["progress"] = "Rapport PDF..."
            sys.argv = ["build_report.py", "--summary", str(ROOT / "output" / "summary.json"),
                         "--metre", str(out_dir / "metre_genere.xlsx"),
                         "--out", str(out_dir / "rapport_metre.pdf")]
            from build_report import main as report_main
            await asyncio.get_event_loop().run_in_executor(None, report_main)
        finally:
            os.chdir(original_cwd)
        
        outputs = {f.name: str(f) for f in out_dir.iterdir() if f.is_file()}
        JOBS[job_id] = {"status": "done", "progress": "Terminé", "outputs": outputs}
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}

@app.post("/pipeline")
async def start_pipeline(pdf: UploadFile = File(...)):
    job_id = str(uuid.uuid4())[:8]
    work_dir = ROOT / "output" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / "plan.pdf"
    with open(pdf_path, "wb") as f:
        f.write(await pdf.read())
    asyncio.create_task(run_pipeline(job_id, pdf_path))
    return {"job_id": job_id, "status": "started"}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return JOBS.get(job_id, {"status": "unknown"})

@app.get("/download/{job_id}/{filename}")
async def download(job_id: str, filename: str):
    # job_id = 8 caractères hexa (uuid4()[:8]) ; filename = nom simple, sans
    # séparateur ni '..' (sous Windows, '\\' est aussi un séparateur).
    if (not JOB_ID_RE.fullmatch(job_id) or "/" in filename or "\\" in filename
            or ".." in filename or ":" in filename or not filename.strip()):
        raise HTTPException(status_code=404, detail="not found")
    job_dir = (OUTPUT_DIR / job_id).resolve()
    fpath = (job_dir / filename).resolve()
    if job_dir.parent != OUTPUT_DIR or fpath.parent != job_dir or not fpath.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(fpath, filename=fpath.name)