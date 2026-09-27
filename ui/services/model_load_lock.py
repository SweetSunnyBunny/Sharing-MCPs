"""Process-wide lock serializing heavyweight ML model construction."""

# ANAM GUIDE: SHARED AI-MODEL LOADING LOCK
# What: One shared "take turns" lock so two AI models never load into memory at the same instant (which crashes them).
# Called by: services/embedding_service.py, services/local_tts.py, and core/lifespan.py at startup.
# Edit here when: almost never — but any NEW heavyweight model loader should import and hold this lock while loading.

import threading

MODEL_LOAD_LOCK = threading.Lock()
