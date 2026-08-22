import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LEDGER_PATH = DATA_DIR / "ledger.jsonl"
MEMORY_PATH = DATA_DIR / "memory.json"

APP_NAME = "MatVerse STACK_API + Geometric Memory + MNBs"
APP_VERSION = "0.1.0"

STACK_API_URL = os.getenv("STACK_API_URL", "").strip()
STACK_API_KEY = os.getenv("STACK_API_KEY", "").strip()
STACK_API_TIMEOUT_SEC = float(os.getenv("STACK_API_TIMEOUT_SEC", "20"))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "7860"))

# SGSI specific configurations
SGSI_AGENT_NAME = os.getenv("SGSI_AGENT_NAME", "MatVerse-Agent")
SGSI_AGENT_ORCID = os.getenv("SGSI_AGENT_ORCID", None)
