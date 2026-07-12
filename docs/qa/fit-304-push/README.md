# FIT-304 push browser QA

Run `node docs/qa/fit-304-push/browser_qa.mjs` from the repository root. The
harness executes the shipped push state machine and service worker in isolated
browser-like JavaScript contexts. Pytest asserts its JSON evidence so changes to
the application code, rather than copied fixture logic, drive the result.

The automated matrix covers unsupported browsers, denied permission, an
installed-iOS requirement failure, granted permission without an active
subscription, granted permission with a matching active subscription, a
successful test-delivery response, and both notification-click branches:
focusing an exact matching window and opening the expected URL.

The setup proof wires the shipped Enable button handler and exercises the
granted path through permission request, service-worker readiness, browser
subscription, server persistence, success copy, and button re-enablement.

## Platform limits

This deterministic harness does not prove OS notification display or permission
prompt chrome. CI cannot fully emulate an installed iOS PWA, and WebKit push
requires a real Home Screen installation on a physical Apple device. It also
does not perform real VAPID push delivery through an external push service.

A release-device check should therefore install the PWA, grant permission from
the Settings button, send a test notification, observe the OS notification, and
tap it to confirm the installed app focuses or opens the supplied URL. The
committed harness covers the application and service-worker decisions around
those platform-owned steps without claiming that CI tested the OS behavior.
