import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from target import answer

if answer() != 2:
    raise SystemExit(f'synthetic regression failure: expected 2, got {answer()}')
