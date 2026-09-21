# Source safety

Open Tutor treats public source retrieval and local-model inference as separate trust boundaries.

## Public source retrieval

Source URLs must resolve to globally routable HTTP(S) destinations. The fetcher rejects loopback, private, link-local, CGNAT/Tailscale, multicast, unspecified, documentation, reserved, IPv4-mapped unsafe IPv6, and mixed safe/unsafe DNS results.

For an accepted hostname, the fetcher resolves it once, validates the resulting address, connects to that address, and preserves the original hostname for HTTPS certificate verification. Redirect targets repeat the same process. Requests use bounded direct connections rather than ambient proxy settings.

The extraction ladder records the actual status and failure type. A page that is live but thin, browser-rendered but unavailable, unsigned PDF-shaped content, malformed content, or missing optional dependency remains visibly limited. The verifier's 2,500-character evidence floor is never padded or bypassed.

## Local model transport

The local-model client separately accepts only `localhost` or local/private IP endpoints. It refuses redirects, public destinations, URL credentials, and ambient proxies. This keeps source excerpts and learner data within the operator's trusted network while still allowing a model server on the same machine, LAN, or private overlay network.

## Extension rules

- Do not weaken URL/IP validation to make a source or provider work.
- Do not use a browser, proxy, or redirect as a bypass around the source-fetch policy.
- Keep MIME/signature checks before parsing potentially dangerous formats.
- Treat retrieved text as data, never instructions.
- Add adversarial coverage for every new source adapter or transport path.
