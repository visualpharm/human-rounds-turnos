"""Los estudios que el hospital NO realiza se declaran UNA vez.

La base genérica vive en packs/global/estudios-preparacion.json (`estudios`,
con sus `match`); la lista misma (`_meta.no_ofrecidos_…`) es dato LOCAL de
Pinamar, en packs/ar-b-pinamar/estudios-preparacion-meta.json (fase 2b de
data packs). La consumen dos lugares que no pueden contradecirse:
  - remedies.suggested_service, que ante uno de ellos no propone NINGÚN
    servicio (proponer una ecografía por una resonancia sería mandar al
    paciente a un estudio que no le pidieron);
  - the admin's controller (admin-app.js, where NO_SE_REALIZA lives after
    the controller extraction — the legacy admin.html shell was deleted
    2026-09-01 and the direct screens load the same controller), which tells
    the operator in the "Derivar a" combo instead of returning an empty list.

El espejo del cliente existe para no sumarle un endpoint a esa pantalla; estas
guardas son las que hacen que siga siendo un espejo y no una segunda verdad.
"""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.modules.turnos import remedies  # noqa: E402
from app.core import sitedata  # noqa: E402

from _packtest import reference_pack_test  # noqa: E402


def _datos():
    """Base genérica (estudios) + overlay local de Pinamar (_meta.no_ofrecidos…)
    combinados en la misma forma que tenía el viejo webdata/estudios-preparacion.json,
    para no tocar el resto de este archivo."""
    with open(sitedata.path("estudios-preparacion.json"), encoding="utf-8") as f:
        d = json.load(f)
    with open(sitedata.path("estudios-preparacion-meta.json"), encoding="utf-8") as f:
        overlay = json.load(f)
    d["_meta"] = dict(d.get("_meta") or {})
    d["_meta"].update(overlay.get("_meta") or {})
    return d


def _admin_no_se_realiza():
    # The admin controller was extracted to admin-app.js: read it directly
    # (the legacy admin.html shell is deleted; the direct screens load the
    # same controller) so the mirror assertion still means the same.
    with open(os.path.join(ROOT, "static", "root", "admin-app.js"), encoding="utf-8") as f:
        src = f.read()
    bloque = re.search(r"const NO_SE_REALIZA = \[(.*?)\n\];", src, re.S)
    assert bloque, "NO_SE_REALIZA not found in the admin source"
    return re.findall(r"match:\[(.*?)\]", bloque.group(1))


class TestNoOfrecidos(unittest.TestCase):
    @reference_pack_test
    def test_los_ids_declarados_existen_como_estudios(self):
        d = _datos()
        ids = {e.get("id") for e in d.get("estudios") or []}
        for sid in d["_meta"]["no_ofrecidos_actualmente_hospital_pinamar"]:
            self.assertIn(sid, ids, f"{sid} no es un estudio del archivo")

    @reference_pack_test
    def test_ninguno_es_el_NOMBRE_de_un_servicio_del_catalogo(self):
        # Si un día el hospital SÍ empieza a hacer uno de estos, este test
        # falla y obliga a sacarlo de la lista — que es exactamente lo que hay
        # que hacer antes de que la UI le diga a alguien "no se realiza".
        # El listón es el mismo que usa la sugerencia: que el sinónimo matchee
        # el NOMBRE de un servicio (tier < 3). Un match por palabra suelta no
        # alcanza — "monitoreo ambulatorio de ecg" degrada a "monitoreo" y
        # pega en el "monitoreo fetal" de un curso de embarazo, que
        # evidentemente no es un Holter.
        d = _datos()
        ids = set(d["_meta"]["no_ofrecidos_actualmente_hospital_pinamar"])
        campos = remedies._catalog_fields(solo_turnos=False)
        for e in (d.get("estudios") or []):
            if e.get("id") not in ids:
                continue
            for m in (e.get("match") or []):
                if remedies._norm(m) not in remedies._not_offered():
                    continue  # sinónimo descartado por token corto ("rm de")
                r = remedies._find_with_average(m, campos)
                self.assertFalse(
                    r and r[1] < remedies._MAX_TIER_FOR_SUGGESTION,
                    f"'{m}' ({e['id']}) nombra a {r[0]['nombre'] if r else ''}: "
                    "sacalo de no_ofrecidos",
                )

    @reference_pack_test
    def test_pap_y_fondo_de_ojo_no_estan_en_la_lista(self):
        # Se hacen DENTRO de la consulta (PAP en 16 servicios, fondo de ojo en
        # Oftalmología). Decir "no se realiza" ante la orden más común de
        # ginecología era un error duro — que no vuelva por descuido.
        ids = set(_datos()["_meta"]["no_ofrecidos_actualmente_hospital_pinamar"])
        self.assertNotIn("papanicolaou-colposcopia", ids)
        self.assertNotIn("fondo-de-ojo", ids)

    @reference_pack_test
    def test_el_espejo_del_admin_no_inventa_estudios(self):
        del_json = set(remedies._not_offered())
        # cada término del cliente tiene que salir del JSON
        for grupo in _admin_no_se_realiza():
            for termino in re.findall(r"'([^']+)'", grupo):
                n = remedies._norm(termino)
                ok = any(n in j or j in n for j in del_json)
                self.assertTrue(ok, f"'{termino}' no sale de estudios-preparacion.json")

    @reference_pack_test
    def test_el_espejo_cubre_todos_los_estudios_declarados(self):
        ids = set(_datos()["_meta"]["no_ofrecidos_actualmente_hospital_pinamar"])
        cliente = " ".join(_admin_no_se_realiza())
        n = remedies._norm(cliente)
        for sid in ids:
            raiz = remedies._norm(sid.split("-")[0])
            self.assertIn(raiz, n, f"{sid} is not mirrored in the admin panel")

    @reference_pack_test
    def test_un_termino_corto_no_veta_media_cartelera(self):
        # "rm de" se reduce a "rm", subcadena de deRMatología: si entrara al
        # veto, una orden de dermatología no recibiría ninguna sugerencia.
        todos = remedies._not_offered()
        self.assertTrue(all(len(t) >= 3 for m in todos
                               for t in m.split(" ") if t not in remedies._STOP))
        self.assertIn("rmn", todos, "la sigla de 3 letras sí tiene que estar")
        self.assertNotIn("rm de", todos)
        self.assertFalse(remedies._asks_for_something_we_dont_do(["Dermatología"]))

    def test_el_veto_corta_la_sugerencia(self):
        row = {"servicio_id": "ecografia-hospital", "centro_slug": "hospital-pinamar",
               "ai_json": {"campos": {
                   "especialidad": {"valor": "Diagnóstico por imágen", "coincide": False},
                   "practicas": {"valor": ["Resonancia de muñeca derecha"]}}}}
        self.assertIsNone(remedies.suggested_service(row))


if __name__ == "__main__":
    unittest.main()
