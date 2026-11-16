# SEO metadata and `llms.txt`

Public and private HTML pages have different search contracts:

- Public informational pages use `seo.head(title, description, path)` for one
  title, one 50–160 character description, `index,follow`, a canonical URL,
  Open Graph metadata, and a Twitter summary card.
- Account, admin, booking, upload, request-detail, offer, error, and local test
  pages use `seo.head(..., index=False)` or equivalent static tags. They must
  not expose canonical or social-preview metadata.
- Static public templates carry the same metadata directly. Their origin uses
  `{{sitio.public_origin}}`, backed by `TURNOS_PUBLIC_ORIGIN`, so another data
  package does not inherit Pinamar's canonical URLs.
- `/llms.txt` is owned and served by `sitio.py` as UTF-8 `text/plain`. It uses
  the same site placeholders and public origin as the HTML templates.
- The former raw templates (`app.html`, `panel.html`, `directorio.html`) are
  deleted. Their public routes still 302 home, so they are not indexed.

Regression coverage lives in `test/test_seo.py` and `test/test_sitio.py`.
Rendered verification should enumerate every center, service, booking flow,
doctor, cluster, and study route, then assert the appropriate public or private
contract rather than checking only the home page.
