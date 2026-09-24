"""
audit_dom_and_js.py - Programmatic Verification of HTML DOM IDs and JS bindings.
"""
import re
import os
import sys

def audit_html():
    html_path = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'index.html')
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract all id="..." from HTML tags
    dom_ids = set(re.findall(r'\bid=[\'"]([a-zA-Z0-9_-]+)[\'"]', content))
    
    # Extract all document.getElementById('...') from JS
    js_ids = set(re.findall(r'getElementById\([\'"]([a-zA-Z0-9_-]+)[\'"]\)', content))

    missing = js_ids - dom_ids
    print(f"Total DOM IDs defined in HTML: {len(dom_ids)}")
    print(f"Total IDs queried via getElementById: {len(js_ids)}")
    print(f"Missing IDs count: {len(missing)}")
    if missing:
        for m in sorted(missing):
            print(f"  [MISSING DOM ID]: {m}")
        return False
    else:
        print("[SUCCESS] 100% of getElementById calls match defined DOM elements!")
        return True

if __name__ == '__main__':
    ok = audit_html()
    sys.exit(0 if ok else 1)
