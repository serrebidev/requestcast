A settings file that cannot be written no longer costs you the tool update it was trying
to record.

**Tool updates survive a read-only settings file.** When RequestCast installs or updates
yt-dlp or Deno, it writes down where the new copy landed. If the settings file cannot be
written — a service confined to a few writable paths, a portable copy on read-only media —
that write failed loudly enough to take the update with it: the background updater thread
died the first time it succeeded at anything, so yt-dlp was never checked again for as long
as the program stayed running, and the "check for updates now" button returned an error
after having updated the tools. Installing a missing tool mid-download failed the same way,
reporting that the tool could not be installed when it just had been.

The tool is installed either way, and RequestCast looks in its own `tools` folder before
PATH, so an unwritable settings file now costs nothing but the note of where the tool went.
