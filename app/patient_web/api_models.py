"""api_models.py — Pydantic models of the admin's branding and legal-texts JSON
API (`branding_router.py`, `legal_router.py`) and their family answers to a
body they cannot read. They document the API and let `core.api_body` tell a
body the panel never sends from one it does; the domain still gets the body
as it came — see `core/api_body.py`.

Also the answers of the public site's JSON API (`patient_site_router.py`,
`availability_directory_router.py`) and of a doctor's own portrait
(`doctor_photos_router.py`, `/api/mi/foto`). The catalog's rows carry the
data pack's own fields as they are (a pack may add more): the models name the
ones every pack has and the ones the site computes.
"""

import json

from app.core.api_body import BodyContract, BodyModel, ResponseModel
from app.core.http_json import json_response

LEGAL = BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                     respond=lambda _request, payload, status: json_response(payload, status=status),
                     catches=(ValueError, TypeError),
                     decoder=lambda raw: json.loads(raw) if raw else {})
"""PUT /api/admin/legal/{doc}: decodes the raw bytes (a BOM or UTF-16 is read), and
answers «JSON inválido: <reason>»."""


class BrandingColorIn(BodyModel):
    """The accent colour (`#rrggbb`); null goes back to the theme's."""
    color: str | None = None


class BrandingLogoIn(BodyModel):
    """The logo as a data URL or bare base64 (PNG, JPG, WEBP or SVG)."""
    data_b64: str | None = None


class LegalTextIn(BodyModel):
    cuerpo: str | None = None


class PhotoUploadIn(BodyModel):
    """A doctor's portrait or a centre's photo (`doctor_photos_router.py`): the image as base64 (a data URL
    allowed) with its file name and type, or — a doctor's portrait only — an `action` on the one already
    saved: `restore` the original, `process` it again, `rotate` it by `rotation` degrees."""
    filename: str | None = None
    mime: str | None = None
    data_b64: str | None = None
    action: str | None = None
    rotation: int | None = None


class BrandingColorOut(ResponseModel):
    ok: bool
    color: str | None
    derivados: dict | None


class BrandingLogoOut(ResponseModel):
    ok: bool
    logo: bool


class LegalTextOut(ResponseModel):
    ok: bool
    item: dict


class BrandingStateOut(ResponseModel):
    """The saved accent (null: the theme's), the one in use, its derived
    tokens (null without a saved accent) and whether a logo is uploaded."""
    color: str | None
    logo: bool
    color_efectivo: str
    derivados: dict | None


class LegalDocOut(ResponseModel):
    """One legal document as the panel edits it: `original` is the shipped
    text, `editado` whether a saved override replaces it."""
    id: str
    titulo: str
    pagina: str
    cuerpo: str
    original: str
    actualizado: str
    actualizado_por: str | None
    editado: bool


class LegalListOut(ResponseModel):
    """Both legal documents, each a `LegalDocOut`."""
    items: list[LegalDocOut]


# ---- the public site's catalog ------------------------------------------------

class NextAvailability(ResponseModel):
    """The «Próxima disponibilidad» cell (`site_availability._next_from`): `kind`
    "turno" (a real slot: `dia`, `hora`), "walkin" (by order of arrival: `nota`,
    the sign's `horario`) or "solicitud" (the center confirms the date;
    `habilita` says when turnos open again). `v` is the text, `sort` orders the
    cells (real slots first)."""
    v: str
    kind: str
    sort: str
    dia: str | None = None
    hora: str | None = None
    nota: str | None = None
    horario: str | None = None
    habilita: dict | None = None
    # the next step when nothing can be booked: {"texto", "href"} (remedies)
    remedio: dict | None = None


class ServiceItem(ResponseModel):
    """A service of the catalog as the pack (with the panel's edits) holds it,
    plus its next availability and its weekly hours (a popularity signal)."""
    id: str
    nombre: str
    centro: str | None = None
    modo: str | None = None
    cluster: str | None = None
    categoria: str | None = None
    descripcion: str | None = None
    cartel: str | None = None
    horario: str | None = None
    palabras: list[str] | None = None
    agenda: dict | None = None
    edad: dict | None = None
    pami: str | None = None
    sede: str | None = None
    consultorio: str | None = None
    foto: str | None = None
    top: int | None = None
    top_label: str | None = None
    turnos_activos_max: int | None = None
    apertura_mismo_dia: bool | None = None
    proximo: NextAvailability
    horas: float


class ServicesOut(ResponseModel):
    servicios: list[ServiceItem]


class DoctorItem(ResponseModel):
    """A professional as the listings show it (`site_listing._doctor_listing_row`):
    links to their page, to booking and to their specialty and center; `orden`
    null when it cannot be told whether a referral is needed; the portrait as
    an `avatar` style, or initials (`avatar_ini`) on a colour (`avatar_c`)."""
    id: str
    nombre: str
    proximo: NextAvailability
    href: str
    turno_href: str
    info_href: str
    especialidad: str
    especialidad_todas: list[str]
    esp_href: str
    centro: str
    centro_href: str
    demo: bool
    palabras: list[str]
    publico: str
    sexo: str | None
    orden: bool | None
    horas: float
    slot_href: str | None = None
    avatar: str | None = None
    avatar_ini: str | None = None
    avatar_c: int | None = None


class DoctorsOut(ResponseModel):
    medicos: list[DoctorItem]


class CenterItem(ResponseModel):
    """A center of the pack with what the site adds: its photo, the route and
    the address in Google Maps, its locality, the street address it shares
    with others (`place`, the site's main center first: `place_main`) and its
    point on the pack's map (`map_x`/`map_y`, fractions)."""
    slug: str
    name: str
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    description: str | None = None
    jtbd: str | None = None
    jobs: list[str] | None = None
    bookable: bool | None = None
    foto: str | None = None
    href: str | None = None
    directions_url: str
    maps_url: str
    locality: str
    place: str | None = None
    place_main: bool | None = None
    map_x: float | None = None
    map_y: float | None = None


class CentersMap(ResponseModel):
    """The pack's map of the centers: the image, its size and the named areas on it."""
    svg: str
    width: float
    height: float
    areas: list[dict]
    attribution: str
    attribution_url: str


class CentersOut(ResponseModel):
    centros: list[CenterItem]
    mapa: CentersMap | None = None
    """Only when the pack has a map and a center lands on it."""


class DirectoryCluster(ResponseModel):
    """A specialty (cluster) card: how many doctors and services, up to six
    portraits (`avatares`, CSS styles); a walk-in cluster (`walkin`) promises
    no faces and carries one description (`desc`) from the pack instead."""
    id: str
    nombre: str
    top: int | None
    n_medicos: int
    n_servicios: int
    avatares: list[dict]
    icon: str
    pinned: int | None
    catch_all: bool
    walkin: bool = False
    desc: str | None = None


class DirectoryOut(ResponseModel):
    clusters: list[DirectoryCluster]
    total_medicos: int


class LinkItem(ResponseModel):
    """An informational search result (telemedicine, zoonosis, blood donation,
    the referral reader): where it goes, its search words, and the content of
    its own page when it has one."""
    id: str
    nombre: str
    descripcion: str | None = None
    href: str
    accion_texto: str | None = None
    accion_href: str | None = None
    disponibilidad: str | None = None
    palabras: list[str] | None = None
    palabras_excluidas: list[str] | None = None
    pagina: dict | None = None


class LinksOut(ResponseModel):
    enlaces: list[LinkItem]


class Slot(ResponseModel):
    dia: str
    hora: str


class SlotBlock(ResponseModel):
    """The slots of one professional (or, unnamed, of the service's schedule) and
    where they are seen. From an external slot source the block also carries
    its own `proximo` slot."""
    medico: str
    turnos: list[Slot]
    medico_id: str | None = None
    medico_href: str | None = None
    centro: str | None = None
    centro_nombre: str | None = None
    centro_href: str | None = None
    sede: str | None = None
    lugar: str | None = None
    especialidad: str | None = None
    esp_href: str | None = None
    proximo: Slot | None = None


class ServiceSlotsOut(ResponseModel):
    """The wizard's time grid: `{}` when the service offers no slot to choose
    (walk-in, an unknown id); `fuente` "agenda" / "agenda-medico" for the
    local schedules, or the external source's own fields (`centro`,
    `especialidad`, `generado`, `edad_horas`); `preferido` the doctor asked
    for, listed first; `sin_turnos` when the grid is empty (when turnos open
    again, if that can be told)."""
    medicos: list[SlotBlock] | None = None
    fuente: str | None = None
    preferido: str | None = None
    sin_turnos: dict | None = None
    centro: str | None = None
    especialidad: str | None = None
    generado: str | None = None
    edad_horas: float | None = None


class SpecialtyGroup(ResponseModel):
    name: str
    count: int
    subs: list[dict]
    fotos: list[dict]
    fotos_total: int
    wait_days: int | None = None


class SpecialtiesOut(ResponseModel):
    """The specialties of the availability directory, grouped, with the clinics
    and professions it lists; all empty when the installation has none."""
    total_medicos: int
    con_online: int
    groups: list[SpecialtyGroup]
    clinicas: list[dict]
    profesiones: list[dict]


# ---- a doctor's own portrait (/api/mi/foto) ---------------------------------

class DoctorPhotoOut(ResponseModel):
    """The portrait editor's preview: whether there is an upload, the round
    avatar (`style`, or initials `ini` on `color`) and the portrait's state
    (`status` "original" or "procesada", its rotation, whether the uploaded
    original is kept)."""
    ok: bool
    has_upload: bool
    foto_url: str | None
    style: str
    ini: str
    color: int
    status: str
    rotation: int
    has_original: bool


class DoctorPhotoSavedOut(ResponseModel):
    """The saved portrait's address and, for a doctor, its new state."""
    ok: bool
    foto_url: str | None
    status: str | None = None
    rotation: int | None = None
    has_original: bool | None = None


class EntityPhotoOut(ResponseModel):
    """The panel's photo editor preview of a doctor or a center (`DoctorPhotoOut`
    without the portrait's state for a center). `?original=1` answers the
    doctor's kept original image instead."""
    ok: bool
    has_upload: bool
    foto_url: str | None
    style: str
    ini: str
    color: int
    status: str | None = None
    rotation: int | None = None
    has_original: bool | None = None
