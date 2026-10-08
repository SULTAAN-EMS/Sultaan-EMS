# Books Feature Deviations

## Verification status

- The implementation has not yet passed the full reference-vs-app screenshot matrix from the brief. Do not treat this feature as pixel-verified until the requested desktop, mobile, modal, reader, and cover comparisons have been captured.
- The local public page was inspected in a 390×844 browser viewport. The supplied reference is a local `file://` URL, which the browser security policy refused to open, so a same-browser pixel comparison and committed screenshot pair could not be produced. Reopen/provide the reference over an allowed local HTTP URL (or share screenshots for the target states) to complete this check. Admin visual states also remain unverified in-browser because this test session has no admin login; the protected route and form API are covered by isolated tests.
- PDF text selection and search highlighting use positioned spans derived from PDF.js text geometry rather than the PDF.js viewer's bundled text-layer component. This keeps the reader dependency self-hosted and small, but span placement can vary for unusual fonts, vertical text, and rotated pages. Replace this layer with the matching PDF.js viewer text-layer assets to close the gap.

## Server-side PDF page count

The upload endpoint validates the PDF header and EOF marker and counts ordinary `/Page` dictionaries. PDFs whose page tree is stored only inside compressed object streams may report zero or an incomplete page count. Rendering and reading still use the real PDF.js document page count in the browser. A server-side parser built on the already approved PDF.js dependency, or another explicitly approved server dependency, is needed to make stored page counts authoritative for every valid PDF.

## Reference comparison

The provided HTML remains untouched. Functional controls have been connected to real records and files; remaining visual deltas must be discovered and recorded during the required Playwright comparison at 1120×740, 1440×900, and 390×844, including light/dark and reader/download states.
