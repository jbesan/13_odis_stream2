#!/usr/bin/env python3
"""
Deadcode Hunter - A wrapper around vulture for ODIS Stream 2.
Detects unused code while accounting for Streamlit specifics (session state, callbacks).
"""

import subprocess
import sys
import os
from typing import List

# Path to the project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files/Folders to exclude
EXCLUDES = [
    ".venv",
    "tests",
    ".agent",
    "data",
    "data_private",
    "notebooks",
    "__pycache__",
    ".git"
]

# Patterns that are allowed to be "unused" (false positives in Streamlit/Agents context)
WHITELIST_PATTERNS = [
    "ui_heb_cb_",      # Dynamic session state keys for checkboxes
    "ui_classe_enfant_", 
    "ui_metiers_adult_",
    "ui_formations_adult_",
    "chat_history_",   # Dynamic session state for chat
    "chat_input_ia_",
]

def run_vulture() -> List[str]:
    """Runs vulture and returns the raw output lines."""
    cmd = [
        "python3", "-m", "vulture", 
        ".", 
        "--exclude", ",".join(EXCLUDES),
        "--min-confidence", "60"
    ]
    
    try:
        # vulture returns non-zero exit code if it finds dead code
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
        return result.stdout.splitlines()
    except Exception as e:
        print(f"Error running vulture: {e}")
        return []

def filter_vulture_output(lines: List[str]) -> List[str]:
    """Filters out known false positives from vulture output."""
    filtered = []
    for line in lines:
        is_whitelisted = False
        for pattern in WHITELIST_PATTERNS:
            if pattern in line:
                is_whitelisted = True
                break
        
        if not is_whitelisted:
            filtered.append(line)
    return filtered

def main():
    print("🏹 Hunting for dead code in ODIS...")
    raw_lines = run_vulture()
    filtered_lines = filter_vulture_output(raw_lines)
    
    if not filtered_lines:
        print("✅ No dead code found (after filtering).")
        sys.exit(0)
    
    print(f"⚠️ Found {len(filtered_lines)} potential dead code items:")
    for line in filtered_lines:
        print(f"  - {line}")
    
    sys.exit(1)

if __name__ == "__main__":
    main()
