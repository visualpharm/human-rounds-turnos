# First-paint reservation must end at its wizard step

The scheduled booking page reserves space while its canonical dependencies load.
An actually empty agenda keeps that space while rendering the time-preference
fallback, preventing support and footer jumps. The fallback class originally
survived into Account, imposing a 594px minimum height at 1280x900.

Independent GLM review identified the source path, but inferred its later-screen
geometry. Root reproduced the class and computed-height leak in the real browser
at 360, 390, 768, 1024 and 1280px before accepting the diagnosis. The first test
accidentally selected the hidden photo-upload input; the corrected red control
waits for the visible DNI field and fails specifically on the retained class.

`afterHorario()` now retires the reservation before advancing to referral or
Account. Existing small, large, empty and rejected-load CLS assertions remain;
the empty-agenda journey additionally advances through registration and the
shorter sign-in form, checking computed minimum height and horizontal overflow.
The real seeded agenda/API drives the empty response; no renderer is substituted.

Run `python3 scripts/turnos_managed_exec.py --profile e2e-asgi`; preserve the
failed and corrected geometry/screenshots separately. A source review alone
does not establish a visual defect or its repair.

Screenshot review then caught the fixed chat launcher covering the help phone
number on the 390px sign-in screen. A real text-range/launcher intersection
assertion reproduced that failure. The help region now leaves horizontal room
for the existing launcher (compact below its actual 480px breakpoint), retaining
normal document flow rather than hiding support or relocating the chat control.

Later live hit-testing found that leaving room for the support text did not
protect the time-slot buttons. The launcher now uses a server-rendered normal-flow
anchor below the booking form through 1100px, including before its native import
arrives. Its wider failed-import recovery uses that anchor at every width: the
desktop launcher fitted beside the form, but its recovery box covered two slots
at 1280px. Test every real slot with scrolling and hit-testing, independently for
the delayed launcher and rejected import, not only the successful widget.
