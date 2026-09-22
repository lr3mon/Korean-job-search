import json
from pathlib import Path
import subprocess
import sys
import unittest
from korean_job_search.registry import load_companies, load_portals, validate_companies

ROOT=Path(__file__).resolve().parents[1]

class CatalogueTests(unittest.TestCase):
    def test_exact_employer_coverage_and_names(self):
        records=load_companies()
        self.assertEqual(validate_companies(records)["count"],100)
        manifest=json.loads((ROOT/"data/research/selection.json").read_text(encoding="utf-8"))
        self.assertEqual({r["id"]:r["name_ko"] for r in records},{r["id"]:r["name_ko"] for r in manifest["companies"]})
        self.assertEqual(next(r for r in records if r["id"]=="kr-024")["name_ko"],"SKC")
    def test_three_named_portals(self):
        self.assertEqual({r["id"] for r in load_portals()},{"saramin","jobkorea","wanted"})
    def test_packaged_catalogue_in_sync(self):
        result=subprocess.run([sys.executable,str(ROOT/"scripts/build_catalog.py"),"--check"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=="__main__":unittest.main()
