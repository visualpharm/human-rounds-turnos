"""The AI register and the editable prompts read in the visitor's language.

The register's canonical text stays Spanish in code (the dossier fingerprints and
the export hash it); only the screen goes through the catalog. These tests pin
that every canonical text has its key, that the Spanish catalog is the code's
text word for word, and that the English one is real English."""
import copy
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPANISH_LETTERS = re.compile('[áéíóúñ¿¡]')
TEXT_FIELDS = ('purpose', 'human_oversight', 'pii_handling', 'risk_level', 'risk_rationale',
               'retention_risk', 'runtime_state')


# Canonical register texts that were already English words in the code.
ALREADY_ENGLISH = {'ai.data.transcript'}


def _catalog(lang):
    return json.loads((ROOT / 'locales' / lang / 'ai.json').read_text(encoding='utf-8'))


class TestRegisterCatalog(unittest.TestCase):
    def test_spanish_catalog_is_the_code_text_word_for_word(self):
        from app.core import ai_register
        es = _catalog('es')
        for text, key in ai_register.TEXT_KEYS.items():
            with self.subTest(key=key):
                self.assertEqual(es[key], text)
        self.assertEqual(es[ai_register.RETENTION_FREE_KEY], ai_register._RETENTION_FREE + '{models}')
        for entry in ai_register.FUNCTIONS:
            with self.subTest(function=entry['id']):
                self.assertEqual(es['ai.function.' + entry['id']], entry['purpose'])
                self.assertEqual(es['ai.oversight.' + entry['id']], entry['human_oversight'])

    def test_english_catalog_has_every_key_and_it_is_not_spanish(self):
        from app.core import ai_register
        es, en = _catalog('es'), _catalog('en')
        keys = set(ai_register.TEXT_KEYS.values()) | {ai_register.RETENTION_FREE_KEY}
        keys |= {prefix + entry['id'] for entry in ai_register.FUNCTIONS
                 for prefix in ('ai.function.', 'ai.oversight.')}
        for key in sorted(keys):
            with self.subTest(key=key):
                self.assertIn(key, en)
                if key not in ALREADY_ENGLISH:
                    self.assertNotEqual(en[key], es[key])
                self.assertIsNone(SPANISH_LETTERS.search(en[key]))

    def test_every_register_text_is_known_to_the_catalog(self):
        from app.core import ai_register
        for row in ai_register.inventory():
            for field in ('pii_handling', 'risk_level', 'risk_rationale', 'runtime_state'):
                with self.subTest(function=row['id'], field=field):
                    self.assertTrue(row[field] in ai_register.TEXT_KEYS, row[field])
            for item in row['data_categories']:
                with self.subTest(function=row['id'], data=item):
                    self.assertIn(item, ai_register.TEXT_KEYS)
            for item in row['kill_switch']:
                with self.subTest(function=row['id'], switch=item):
                    # Env names and the operator commands made of module / prompt ids.
                    machine = re.fullmatch(r'[A-Z0-9_]+(=\S+)?( .*)?|(module|restore) [a-z_ /]+', item)
                    self.assertTrue(machine or item in ai_register.TEXT_KEYS, item)

    def test_english_screen_rows_carry_no_spanish(self):
        from app.core import ai_register
        rows = ai_register.localized(ai_register.inventory(), 'en')
        self.assertTrue(rows)
        for row in rows:
            for field in TEXT_FIELDS:
                with self.subTest(function=row['id'], field=field):
                    self.assertIsNone(SPANISH_LETTERS.search(row[field]), row[field])
            for field in ('data_categories', 'kill_switch'):
                for item in row[field]:
                    with self.subTest(function=row['id'], field=field, item=item):
                        self.assertIsNone(SPANISH_LETTERS.search(item), item)

    def test_free_model_retention_keeps_the_model_list(self):
        from app.core import ai_register
        text = ai_register._RETENTION_FREE + 'a/b:free, c/d:free'
        self.assertTrue(ai_register.display(text, 'en').endswith('Models: a/b:free, c/d:free'))
        self.assertEqual(ai_register.display(text, 'es'), text)

    def test_canonical_inventory_stays_spanish(self):
        """Translating the screen must not move the dossier's fingerprints."""
        from app.core import ai_register
        # one read: the rows carry the runtime model, which a neighbour's config may change between two reads
        before = [dict(row) for row in ai_register.inventory()]
        snapshot = copy.deepcopy(before)
        ai_register.localized(before, 'en')
        self.assertEqual(before, snapshot)
        self.assertIn('ai.pii.exception', ai_register.TEXT_KEYS.values())
        self.assertEqual(ai_register.display('texto que el registro no conoce', 'en'), 'texto que el registro no conoce')


class TestPromptCatalog(unittest.TestCase):
    def test_every_prompt_text_has_a_spanish_and_an_english_entry(self):
        from app.ai import prompts
        es, en = _catalog('es'), _catalog('en')
        for item in prompts.REGISTRY:
            fields = {'title': item['titulo'], 'does': item['que_hace'], 'where': item['donde']}
            fields.update({'var.' + v['nombre']: v['que_es'] for v in item['variables']})
            for field, spanish in fields.items():
                key = 'ai.prompt.%s.%s' % (item['clave'], field)
                with self.subTest(key=key):
                    self.assertEqual(es[key], spanish)
                    self.assertIn(key, en)
                    self.assertNotEqual(en[key], spanish)
                    self.assertIsNone(SPANISH_LETTERS.search(en[key]))

    def test_prompt_status_reads_in_the_bound_language(self):
        from app.ai import prompts
        from app.core import i18n
        for lang, expected in (('en', 'Reading the medical referral'), ('es', 'Lectura de la orden médica')):
            with self.subTest(lang=lang):
                _, token = i18n.bind_lang(lang)
                try:
                    status = prompts._row_to_status('orden_analisis', None)
                finally:
                    i18n.unbind(token)
                self.assertEqual(status['titulo'], expected)
                self.assertEqual([v['nombre'] for v in status['variables']], ['hoy', 'jurisdiccion'])
        self.assertEqual(prompts._BY_KEY['orden_analisis']['titulo'], 'Lectura de la orden médica')


if __name__ == '__main__':
    unittest.main()
