"""Central configuration. Everything tunable lives here or in .env (NFR-22)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# --- Paths ---------------------------------------------------------------
DATA_DIR = ROOT / "data"
FAQ_CSV = DATA_DIR / "faq.csv"
TICKETS_DB = DATA_DIR / "tickets.db"
GUIDES_DIR = DATA_DIR  # every *.pdf in data/ is treated as a guide
CHROMA_DIR = ROOT / "chroma_store"
LOG_DIR = ROOT / "logs"
INTERACTION_LOG = LOG_DIR / "interactions.jsonl"
HANDOFF_LOG = LOG_DIR / "handoffs.jsonl"

# --- Models --------------------------------------------------------------
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # FR-09, local
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")  # FR-13
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none")
LLM_TEMPERATURE = 0  # FR-12
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# --- Retrieval -----------------------------------------------------------
TOP_K_PER_COLLECTION = 3  # FR-07
# Chunks with a cosine relevance below this are discarded (FR-16).
MIN_RELEVANCE = float(os.getenv("MIN_RELEVANCE", "0.30"))
# If the best chunk across all sources is below this, the confidence gate fires (FR-22).
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.35"))
GUIDE_CHUNK_SIZE = 600  # FR-16 (PRD.md)
GUIDE_CHUNK_OVERLAP = 100

# --- Conversation --------------------------------------------------------
HISTORY_TURNS = 6  # FR-04
MAX_INPUT_CHARS = 1000  # NFR-18
PROMPT_VERSION = "v1.0"

# --- Refund agent (agent_PRD.md) ------------------------------------------
# Stands in for the authenticated session user; the customer is never asked for it (FR-15..FR-17).
DEMO_USER_ID = os.getenv("DEMO_USER_ID", "user_123")
REFUND_WINDOW_DAYS = 7  # FR-25
REFUND_APPROVAL_LIMIT = 499  # FR-36: refunds above this go to the Support Team
AGENT_MAX_STEPS = 4  # cap on LLM tool-calling rounds per turn

# --- Support channels (from the knowledge base; see PRD Q3) --------------
SUPPORT_PHONE = "611"
SELF_SERVE_APP = "MyTelecom app"
LIVE_CHAT_HOURS = (8, 22)  # 8am-10pm, per FAQ #25
