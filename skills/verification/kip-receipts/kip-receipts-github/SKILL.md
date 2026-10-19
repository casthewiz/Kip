---
name: kip-receipts-github
description: >
  Gets a receipts run's images and videos onto a GitHub pull request, and keeps
  that working. GitHub has no API for PR attachments, so the agent uploads each
  file through the user's logged-in Chrome (Claude in Chrome) into the PR's
  comment box, reads back the github.com/user-attachments URL, records it with
  `receipt.py record --file`, and publishes again, so images embed and videos
  play inline in the ledger. Also the place to repair the procedure when
  GitHub's page changes. Use when `receipt.py publish` prints
  "github: not uploaded", when the user wants screenshots or videos on a PR,
  or when the browser upload breaks. Do NOT use for Linear or Jira (their
  sinks upload by API) or when no file is attached to the run.
license: MIT
---

# Receipts on GitHub: browser uploads

The `github` sink ([`../sinks/github.py`](../sinks/github.py)) writes the
ledger into the PR body with `gh`, but `gh` and the REST API can't upload
attachments: only the comment box's drag-and-drop can. So the files go up
through the user's browser, and `receipt.py` only records where they landed.

## Procedure

1. `receipt.py publish --sink github --issue <PR>`. If files are missing it
   prints `github: not uploaded: files/…`; those paths are relative to
   `receipt.py path`.
2. Check each file: GitHub takes PNG, JPG, GIF and SVG images, MP4, MOV and
   WEBM videos, and text files such as `.log` and `.txt` (logs come back as a
   link), and the `file_upload` tool takes at most 10 MB per call. For a
   bigger or other video, make an MP4 under 10 MB (`ffmpeg -i in -vf scale=1280:-2
   -crf 28 out.mp4`), `receipt.py attach` it to the same claim, and upload that.
   `file_upload` only reads this session's folders, and the run dir
   (`~/.kip/receipts/…`) usually isn't one: copy each file into the scratchpad
   or working directory first and upload the copy.
3. In Claude in Chrome (load its tools first, with `file_upload` and
   `javascript_tool`): open `https://github.com/<owner>/<repo>/pull/<PR>`. If
   it shows a sign-in page, stop and ask the user to sign in; never type
   credentials.
4. Find the new-comment box's file input (`find` "file input" or "Attach
   files", or see Selectors). Never click it: that opens a native picker you
   can't use. Call `file_upload` with its `ref` and the copy's absolute path.
5. Poll the textarea's value with `javascript_tool` until it holds the URL
   instead of `Uploading…` (a few seconds; longer for video). An image
   arrives as `<img width=… alt=… src="https://github.com/user-attachments/assets/<id>" />`,
   a video as the bare URL. Take the URL.
6. `receipt.py record --sink github --file <files/NAME> --url <URL>`.
7. Clear the textarea (`javascript_tool`: set its value to `''` and dispatch
   an `input` event) and **never submit the comment**:
   the ledger lives in the PR body, and a posted comment would duplicate it.
   The upload alone doesn't post anything.
8. When every file is recorded, `receipt.py publish` again. `render --sink
   github` shows the result: images as `![…](…)`, videos alone on a line,
   which GitHub renders as a player.
9. Check it: reload the PR and confirm the `<img>` for the URL has a
   `naturalWidth` above 0 (GitHub swaps the src for a signed
   `private-user-images.githubusercontent.com` URL). Don't test the URL with
   `curl` or a page `fetch`: anonymous requests get 404 even on a public repo,
   and the page's fetch fails on the cross-origin redirect.

If Chrome isn't connected or the user declines, leave the files "not
uploaded" (the ledger names each with its hash) and say so. Don't host them
anywhere else without asking.

## Selectors

What step 4 and 5 rely on. GitHub changes its markup, so these are hints;
`find` by role or label first and fall back to them.

| Thing | Last seen as |
|---|---|
| Comment textarea | `#new_comment_field` (`textarea[name="comment[body]"]`) |
| File input | `#fc-new_comment_field` in `form#new_comment_form`; `find` names it a "file" button |
| Upload placeholder | `Uploading…` text in the textarea until done (not seen: a 64×32 PNG finished at once) |
| Image result | `<img width="64" height="32" alt="<name>" src="https://github.com/user-attachments/assets/<uuid>" />` |
| Rendered image | `src` rewritten to `https://private-user-images.githubusercontent.com/…` (signed) |

Last verified: 2026-10-19, on casthewiz/Kip#10 (public repo): a PNG uploaded,
recorded, and rendered in the PR body at its real size for a signed-in viewer;
no comment posted. Video not yet checked. Update this line (date, what you
checked) whenever you run the smoke test.

## Maintenance

When the procedure breaks, fix the procedure here; don't work around it per
run.

- **Smoke test** (needs the user's go-ahead, since it uploads to GitHub): on a
  PR the user names, upload one small PNG, confirm step 5 yields a
  `user-attachments` URL that renders (step 9), clear the box without submitting. Record
  it as receipts evidence with `observe`, then update "Last verified".
- **Input not found**: read the comment form with `read_page` and update
  Selectors with what's there now.
- **No URL after upload**: read `read_console_messages` and
  `read_network_requests` for the upload request; a 4xx usually means the
  file type or size is refused (step 2).
- **URL format changed**: update the Uploaded URL row and the format in step
  5. If videos no longer come back bare, update `render_markdown`'s
  `bare_video` branch in `receipt.py` and its test in `test_receipt.py`.
- **GitHub ships an upload API**: replace this procedure with `upload()` in
  `sinks/github.py` and retire this skill.

Never call GitHub's internal upload endpoint directly or handle the user's
session cookie: that's a credential, and the endpoint is undocumented.
