import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("scrape", pathlib.Path(__file__).parents[1] / "scripts" / "scrape_conocimiento.py")
scrape = importlib.util.module_from_spec(spec); spec.loader.exec_module(scrape)

class ScrapeKnowledgeTest(unittest.TestCase):
    def test_allowlist_and_extract(self):
        self.assertTrue(scrape.allowed("https://sub.gba.gob.ar/salud"))
        self.assertFalse(scrape.allowed("http://gba.gob.ar/x"))
        self.assertFalse(scrape.allowed("https://gba.gob.ar.evil/x"))
        self.assertEqual(scrape.extract_readable("<nav>nav</nav><p>Hola <b>mundo</b></p><script>x</script>"), "Hola mundo")
