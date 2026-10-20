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
| File input | `#fc-new_comment_field` in `form#new_comment_form` (`#new_comment_form input[type=file]`); `find` names it a "file" button |
| Upload placeholder | `Uploading…` in the textarea until done (a small PNG finishes at once) |
| Image result | `<img width="…" height="…" alt="<name>" src="https://github.com/user-attachments/assets/<uuid>" />` in the textarea |
| Video result | the bare `https://github.com/user-attachments/assets/<uuid>` URL |
| Rendered image | `src` rewritten to a signed `https://private-user-images.githubusercontent.com/…` URL |

Accepted: PNG, JPG, GIF and SVG images; MP4, MOV and WEBM videos; text files
such as `.log` and `.txt` (they come back as a link). Make a big or other
video an MP4 under 10 MB first (`ffmpeg -i in -vf scale=1280:-2 -crf 28 out.mp4`).

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

Last verified: 2026-10-19, on casthewiz/Kip#10 (public repo) through Claude
in Chrome: a PNG uploaded and rendered in the PR body at its real size for a
signed-in viewer; no comment posted. Video not yet checked.
