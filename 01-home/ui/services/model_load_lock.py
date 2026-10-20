"""Process-wide lock serializing heavyweight ML model construction."""


import threading

MODEL_LOAD_LOCK = threading.Lock()
