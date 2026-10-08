# Books Visual Check

- App viewport inspected: public `/books` at 390×844. Centered mobile layout, header, search, level/class/subject controls, empty state, and footer render without browser console warnings/errors.
- The provided reference HTML could not be opened by the browser because its `file://` URL is blocked by the browser security policy. No pixel-diff or side-by-side comparison is claimed.
- Admin desktop, upload dialogs, book-card states, reader, download sheet, all cover sizes, and dark mode still need the reference screenshot pass.
- Functional checks are recorded separately by `python -m unittest test_books_feature -v`.
