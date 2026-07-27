Whole-library lists now fit. The upload limits were far too small.

**250,000 entries per file, up from 10,000.** Plenty of people keep lists longer than ten
thousand rows, and there was no good reason for the old ceiling — indexing was never the
slow part. On an ordinary machine, 250,000 rows takes about a second to index from a TXT
file and about eleven seconds from an XLSX workbook, and a PDF page costs roughly three
milliseconds. The new limits are set by what that work actually costs.

**Bigger files are accepted.** Uploads may now be 64 MB rather than 16 MB, PDFs may run to
5,000 pages rather than 500, and an import may resolve up to 250,000 tracks rather than
25,000. A file past the ceiling is still refused, and now fails as soon as it goes over
instead of being read into memory in full first.

**The main page stays fast.** A large import stores every indexed entry with its job, so
listing recent downloads no longer reads those payloads back — it reads only the few
columns it shows. Without that, drawing the home page after a few whole-library imports
would have meant loading hundreds of megabytes.

If you run RequestCast behind nginx, raise `client_max_body_size` to `64m` and keep a
generous `proxy_read_timeout`, since indexing happens inside the upload request.
