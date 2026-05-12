import sys
import os
print(f"CWD: {os.getcwd()}")
print(f"PATH: {sys.path}")
try:
    import core.models
    print("Import core.models: SUCCESS")
except ImportError:
    print("Import core.models: FAILED")
try:
    from app.core.models import CriteriaItem as CI1
    from core.models import CriteriaItem as CI2
    print(f"CI1 is CI2: {CI1 is CI2}")
except ImportError as e:
    print(f"Double import failed: {e}")
