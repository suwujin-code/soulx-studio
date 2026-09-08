# Contributing

Start with a reproducible issue or a focused pull request. Use synthetic, consented data and keep upstream license notices.

Run `python -m pytest -q tests`, `cd desktop-installer && go test ./...`, and `cd frontend && npm run build` where toolchains are installed. Build the embedded payload first with `python scripts/build_desktop_installers.py --payload-only`.

Separate claims in pull requests: source compiles; unit tests; target-system install; genuine model generation; human Cantonese listening review. None of these imply the next. Dry-run tones are not speech acceptance.

Never add real credentials, personal voice assets, private Drive references, or model weights. Do not add false performance numbers, user counts, or unsupported emotion/accent controls to the UI.
