"""«Cargar lista» parser: formats, headers or none, aliases and the size limits (no database)."""
import io
import unittest

from app.admin_panel import doctor_import as importer
from app.admin_panel.native_json import ApiError

SPECIALTIES = [{'nombre': 'Clínica médica'}]
CENTERS = [{'slug': 'sur', 'name': 'Centro Sur'}]


def parse(text, name='roster.csv'):
    raw = text.encode('utf-8-sig') if isinstance(text, str) else text
    return importer.parse_file(raw, name, SPECIALTIES, CENTERS)


def row(**fields):
    return {f: fields.get(f, '') for f in importer.FIELDS}


class Parse(unittest.TestCase):
    def test_csv_delimiters_aliases_and_blank_lines(self):
        for sep in (',', ';', '\t'):
            text = sep.join(['Nombre Completo', 'Especialidad', 'Centro', 'Correo']) + '\n'
            text += sep.join(['Ana', 'Clínica médica', 'Sur', 'ana@example.test']) + '\n\n'
            self.assertEqual(parse(text), [(2, row(nombre='Ana', especialidad='Clínica médica',
                                                   centro='Sur', email='ana@example.test'))])

    def test_nombre_is_the_first_name_only_next_to_an_apellido_column(self):
        self.assertEqual(parse('Nombre;Apellido;Email\nAna;Pérez;ana@example.test\n')[0][1],
                         row(nombre_pila='Ana', apellido='Pérez', email='ana@example.test'))
        self.assertEqual(parse('nombre,especialidad\nAna Pérez,Clínica médica\n')[0][1],
                         row(nombre='Ana Pérez', especialidad='Clínica médica'))

    def test_a_list_without_header_is_guessed_cell_by_cell(self):
        rows = parse('ana@example.test;Ana;Pérez;clinica medica;Centro Sur\nBeto Ruiz\n', 'lista.txt')
        self.assertEqual(rows, [(1, row(nombre_pila='Ana', apellido='Pérez', email='ana@example.test',
                                        especialidad='clinica medica', centro='Centro Sur')),
                                (2, row(nombre='Beto Ruiz'))])

    def test_email_column_is_optional_and_dni_is_ignored(self):
        rows = parse('nombre,DNI,especialidad,centro\nAna,30000111,Clínica médica,Sur\n')
        self.assertEqual(rows[0][1]['email'], '')
        self.assertNotIn('30000111', str(rows))

    def test_a_header_without_name_or_email_or_with_a_repeated_column_fails(self):
        for text in ('especialidad,centro\nX,Y\n', 'nombre,nombre,especialidad,centro\na,b,c,d\n'):
            with self.assertRaises(ApiError):
                parse(text)

    def test_empty_and_header_only_fail(self):
        for text in ('', '\n\n', 'nombre,especialidad,centro\n'):
            with self.assertRaises(ApiError):
                parse(text)

    def test_limits(self):
        with self.assertRaises(ApiError) as big:
            importer.parse_file(b'x' * (importer.MAX_BYTES + 1), 'a.csv')
        self.assertEqual(big.exception.status, 413)
        lines = 'nombre,especialidad,centro\n' + 'a,b,c\n' * (importer.MAX_ROWS + 1)
        with self.assertRaises(ApiError) as many:
            parse(lines)
        self.assertEqual(many.exception.status, 413)
        with self.assertRaises(ApiError):
            parse('x', 'roster.pdf')

    def test_xlsx_roundtrip_and_garbage(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(['nombre', 'especialidad', 'centro', 'email'])
        book.active.append(['Ana', 'Clínica médica', 'Sur', None])
        out = io.BytesIO()
        book.save(out)
        self.assertEqual(parse(out.getvalue(), 'roster.xlsx')[0][1]['nombre'], 'Ana')
        with self.assertRaises(ApiError):
            parse(b'not a zip', 'roster.xlsx')


if __name__ == '__main__':
    unittest.main()
