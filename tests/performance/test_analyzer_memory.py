"""Memory/time guard for the analyzer on a large synthetic document.

Run explicitly: pytest -m performance
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.patent_fixtures import build_large

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.performance
def test_large_document_bounded_memory(tmp_path):
    path = build_large(tmp_path / "large_en.docx", "en", claims=500, images=8, image_px=1000)  # ~23 MB
    code = (
        "import sys;sys.path.insert(0,'src');"
        "from patent_converter.config.settings import load_settings;"
        "from patent_converter.config.claim_patterns import PatternRegistry;"
        "from patent_converter.analyzer.docx_analyzer import DocxAnalyzer;"
        "s=load_settings('config/app.dev.yaml');p=PatternRegistry.from_file(s.claim_patterns_file).get('example');"
        f"r=DocxAnalyzer(s,p).analyze(r'{path}','en');"
        "assert r['claims']['count']==500, r['claims']['count'];"
        # VmHWM belongs to the new address space after exec; ru_maxrss would inherit the parent's peak.
        "print([l.split()[1] for l in open('/proc/self/status') if l.startswith('VmHWM')][0])"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    peak_mb = int(out.stdout.strip()) / 1024
    assert peak_mb < 150, f"peak RSS {peak_mb:.0f} MB"
