"""patient_web/ — the public patient-facing site: page routes and their
content backbone (`patient_web/site_*.py`, the site's biggest, most-imported
file — home, service pages, medico/centro directory, wizard shell), the
editable content overlay (`catalog.py`: services/centers/doctors/knowledge
CRUD source), the knowledge-base/RAG helper (`knowledge.py`), the two
legal documents (`legal.py`: terms + privacy, editable from the panel),
SEO meta helpers (`seo.py`), the generated sitemap (`sitemap.py`), the
municipality's editable color/logo branding (`branding.py`), the shared
small-round-portrait atlas (`avatar_sprite.py`), and per-doctor profile
photo upload/serve (`doctor_photos.py`).

Tier 2 batch H (docs/roadmap/tier2-physical-modularization-plan.md): a
domain package whose shared site infrastructure is platform core. The
availability directory is the deliberate exception: its data owner and
native router are declared by `modules/turnos/module.json`, so that route
is disabled with the turnos module. `legal.py` and `branding.py` remain
unconditional platform routes alongside `auth/`, `ai/`, and `cuentas/`.
The package deliberately has no re-export facade; importers use explicit
submodules:

    from patient_web import patient_web/site_*.py       # page routes: home, service pages, directory, wizard shell
    from patient_web import patient_site_router # FastAPI twin: /api/services, /api/doctors, /api/centers, /api/directory, /service/<id>...
    from patient_web import availability_directory  # read-only availability directory data
    from patient_web import availability_directory_router  # native FastAPI GET /api/specialties
    from patient_web import availability_pages  # late public /<specialty>[/<doctor>] pages
    from patient_web import health_data       # platform-core /datos renderer
    from patient_web import health_data_router # native FastAPI GET /datos
    from patient_web import catalog            # editable content overlay: services/centers/doctors/knowledge CRUD source
    from patient_web import knowledge          # knowledge-base / RAG context helper for the chat
    from patient_web import legal              # términos + privacidad, editable from the panel
    from patient_web import legal_router       # FastAPI twin
    from patient_web import seo                # meta description/OG tag helpers
    from patient_web import sitemap            # GET /sitemap.xml, built from the live catalog
    from patient_web import sitemap_router     # FastAPI twin
    from patient_web import branding           # municipality color + logo, editable from the panel
    from patient_web import branding_router    # FastAPI twin
    from patient_web import avatar_sprite      # ONE shared atlas for every small round doctor portrait
    from patient_web import avatar_sprite_router  # FastAPI twin
    from patient_web import avatar_thumb       # per-doctor page portrait, drawn size
    from patient_web import avatar_thumb_router  # FastAPI twin
    from patient_web import doctor_photos      # per-doctor full-size profile photo upload/serve
    from patient_web import doctor_photos_router  # FastAPI twin
"""
