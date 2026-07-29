Searches bring back much more, refused downloads recover on their own, and the download
tools install and update themselves.

**Deno is installed automatically, and YouTube works again.** YouTube now answers with
JavaScript challenges yt-dlp cannot solve by itself; it hands them to an external
JavaScript runtime, and enables Deno by default. Without one, YouTube downloads lose the
better formats and fail with 403 errors no amount of retrying clears. RequestCast now
treats Deno as a required tool: it is fetched into the `tools` folder alongside yt-dlp and
ffmpeg, kept current, and passed to yt-dlp explicitly, since our copy is not on PATH. A
403 with no Deno present now says so instead of blaming rate limiting.

**The download tools set themselves up.** yt-dlp, ffmpeg, and Deno are installed in the
background as soon as RequestCast is configured — nobody has to find a button first — and
a download that arrives before they are ready waits for the install rather than failing.

**yt-dlp and musicdl keep themselves up to date.** Both stop working as the sites they
read change, and an out-of-date yt-dlp is the most common cause of downloads that fail
while the same track plays fine in a browser. RequestCast checks daily by default and
installs what is newer. The interval is configurable, automatic updating can be turned
off, and Preferences has a "check for updates now" button that reports what each tool did.

**Searches pull in much more.** Result counts were fixed at 25 from YouTube and 12 per
type from Deezer. The number is now a setting, defaulting to 50 per source and per result
type, and every search page has a "results per source" control, so one search can ask for
200 without changing anything permanently. Deezer results are paged rather than truncated
at the API's 100-row ceiling. musicdl can also join searches now — its platforms carry
plenty that YouTube and Deezer do not — either always, or per search from the source list.

**Failed downloads have real retry settings.** Bulk runs — a channel, a discography, a
playlist import — get refused in batches with 403 or "video unavailable" even though the
same tracks download fine later. That is rate limiting, and there was no way to tune it.
Now there is:

- **Extra attempts per track**, each asking YouTube as a different client, which is what
  usually clears a 403 outright.
- **A gap between tracks**, which is the most effective way to avoid being rate limited to
  begin with.
- **A cooldown** after several refusals in a row, and again before a final pass over
  everything that failed, so a batch that was refused is retried once the site has settled
  instead of being written off.
- **A requeue limit** for a whole download that fails outright.
- **A "try this download again" button** on every finished download. Tracks already in the
  library are recognised and skipped, so a retry only fetches what is missing.

Failures are also explained now: a bare `HTTP Error 403: Forbidden` in the history says
what it means and what to change.
