# GitHub (site)

What intents need from github.com. GitHub changes its markup, so selectors
are hints: `find` by role or label first and fall back to them.

## Attaching a file to a pull request

GitHub has no API for attachments: only a comment box's file input uploads,
and it hands back a `user-attachments` URL that works anywhere on GitHub.
The upload alone posts nothing.

| Thing | Last seen as |
|---|---|
| Page | `https://github.com/<owner>/<repo>/pull/<number>` (`gh pr view <number> --json url`) |
| Comment textarea | `#new_comment_field` (`textarea[name="comment[body]"]`) |
| File input | `#fc-new_comment_field` in `form#new_comment_form` (`#new_comment_form input[type=file]`), `hidden`, so `find` may not see it (below) |
| Upload placeholder | `Uploading…` in the textarea until done (a small PNG finishes at once) |
| Image result | `<img width="…" height="…" alt="<name>" src="https://github.com/user-attachments/assets/<uuid>" />` in the textarea |
| Video result | the bare `https://github.com/user-attachments/assets/<uuid>` URL |
| Rendered image | `src` rewritten to a signed `https://private-user-images.githubusercontent.com/…` URL |

Accepted: PNG, JPG, GIF and SVG images; MP4, MOV and WEBM videos; text files,
which come back as a `https://github.com/user-attachments/files/<id>/<name>`
link. A `.log` set through Claude in Chrome's `file_upload` is refused ("We
don't support that file type", the form gets `is-bad-file`) though GitHub lists
LOG, likely for the MIME type it's sent with: upload a byte-identical `.txt`
copy and record that URL against the run's `.log`. Make a WebM or a big
video an MP4 under 10 MB first: `kubrick.py mp4 VIDEO` (kip-kubrick).

- Find the file input: it carries the `hidden` attribute, so an
  accessibility-tree `find` can miss it and return the "Attach files"
  button instead, which `set_files` rejects ("not a file input"). `eval`
  `hidden = false` and an `aria-label` on `#fc-new_comment_field`, then
  `find` that label; the ref takes `set_files` for every file after. It
  posts nothing and is gone on reload.
- Read the URL by polling `eval` of the textarea's value until it matches
  `https://github.com/user-attachments/[^\s"')]+`.
- Clear it after: `eval` setting the textarea's value to `''` and
  dispatching an `input` event. **Never submit the comment.**
- Check a recorded image rendered: reload the page and `eval` that the
  `<img>` for it has `naturalWidth` above 0. Don't test the URL with `curl`
  or a page `fetch`: anonymous requests get 404, even on a public repo, and
  the page's fetch fails on the cross-origin redirect.

Never call GitHub's internal upload endpoint directly or read the session
cookie: the cookie is a credential and the endpoint is undocumented.

Last verified: 2026-10-24, on casthewiz/Kip#24 (public repo) through Claude
in Chrome: unhiding the file input still needed; four JPGs uploaded through one
ref and rendered in the PR body (`naturalWidth` 800); two `.log` files were
refused and went up as `.txt` copies, linked; no comment posted. Video:
2026-10-28, on casthewiz/Kip#42 through Claude in Chrome: a `kubrick.py mp4`
MP4 (41 KB) came back as a bare `user-attachments/assets` URL and, alone on
a line in its claim's details, played inline (`<video>` 1280×800,
`readyState` 4); no comment posted.
