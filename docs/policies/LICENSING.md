# Human Rounds licensing and commercial boundary

Copyright © 2026 Generated Media, Inc.

Generated Media, Inc. owns and stewards the Human Rounds project. It is the
copyright holder and licensor for the Human Rounds code that it owns, including
the Sacoturno code incorporated into this repository. An individual maintainer
does not own the project merely because they hold a maintainer role.

Human Rounds Core is distributed under the
[GNU Affero General Public License version 3](../../LICENSE). A file carrying a
different compatible notice remains governed by that notice. Third-party
contributors retain copyright in their contributions unless they separately
assign it; contributions accepted into this repository are licensed under
AGPL-3.0.

## Assets that are not under AGPL-3.0

- The interface icons in `static/icons/` and the workshop illustrations
  `adolescentes.jpg`, `ansiedad.jpg`, `estimulacion-cognitiva.jpg`,
  `estimulacion-temprana.jpg` and `taller-escritura.jpg` in
  `packs/example-town/talleres/` come from [Icons8](https://icons8.com) and
  stay under the Icons8 licence. They ship so the application works out of
  the box; reusing them outside Human Rounds needs an Icons8 licence.
- The emblem of the Municipality of Pinamar (`static/root/pinamar-emblema.svg`)
  belongs to the municipality and is not licensed by this project; it is the
  mark of the `pinamar` theme of the reference deployment. Another installation
  uses its own mark.
- The demo's doctor portraits and center photos (`scripts/gen_demo_*`), its
  referral images (`synthetic` in `packs/example-town/ordenes-demo/assets.json`),
  the usage-tip drawings in `static/consejos/` and the other workshop
  illustrations are AI-generated for this project and depict no real person or
  place; they are covered by AGPL-3.0 with the code.

## AGPL Core

Human Rounds Core is intended to be a complete, genuinely useful hospital
application rather than a restricted demonstration. Its open product boundary
includes the standalone clinical workflow, basic staff workflows and roles,
the responsive and localized web interface, authentication and authorization,
the database and migrations, backup and export, basic auditability,
self-hosting documentation, basic clinical AI assistance, and the provider
interface that lets an operator bring its own model or API key.

The intended clinical workflow includes registration, intake, triage, patient
queues, assignment, the encounter workspace, notes, differential suggestions,
basic orders and tasks, discharge, handoffs, and patient history. This is a
product-boundary commitment, not a claim that every listed capability has
already reached a stable release.

Everything currently published in this repository is part of AGPL Core unless
its file carries a separate license notice. That includes the connector
contracts and the connector implementations already present here. An existing
AGPL component does not become proprietary because an Enterprise service uses
it.

## Enterprise services

Generated Media may separately license new Enterprise services that connect
Human Rounds to an institution. Suitable Enterprise scope includes certified
or custom HIS, EHR, HL7, FHIR, laboratory, imaging, pharmacy, identity,
messaging, billing and insurance integrations; SSO, SAML, directory sync,
advanced institutional administration and multi-hospital control; compliance
tooling, fleet monitoring, managed disaster recovery and upgrades; and
contracted support or service levels.

Enterprise code should be a separate program or service communicating with
Core through documented, versioned APIs, events or sockets. It must not depend
on private Python imports, dynamic linking into Core, shared internal data
structures, or undocumented direct writes to the Core database. The public API
and connector contracts remain in Core. An API boundary is an architectural
rule, not an automatic legal conclusion; a proposed proprietary integration
that needs intimate access to Core internals requires specialist licensing
review before release.

## Human Rounds Cloud

Human Rounds Cloud may sell managed infrastructure, upgrades, backups,
monitoring, managed AI, managed integrations and support. Hosting is not an
exception to AGPL-3.0. A party operating a modified network version of Core
must meet the license's Corresponding Source obligations.

Anyone may self-host or commercially host AGPL Core while complying with its
license. The commercial advantage of Generated Media is the Enterprise
capability, operational expertise, service quality and Human Rounds brand, not
an exclusive right to host the open application.

## Commercial principle

**Open the medicine. Charge for the institution.**

A small clinic or public hospital must be able to run a useful clinical and
staff workflow without buying a proprietary extension. An institution that
needs custom system integration, centralized administration, guaranteed
operations or contracted support is the Enterprise customer.
