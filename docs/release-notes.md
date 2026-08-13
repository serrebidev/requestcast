A deployment fix and a Soulseek default change.

**musicdl stops failing to update itself on hardened servers.** On a server
deployment the Python environment is typically root-owned and made read-only by
systemd hardening, so the automatic musicdl update tried to write into it every
cycle and failed with "Read-only file system". RequestCast now recognises a venv
it cannot write to — and a read-only error from pip itself — and leaves the
package alone with a clear note, because such an install is updated from
requirements.txt at deploy time. A genuinely writable install still updates
itself exactly as before.

**Soulseek shares the AzuraCast media library by default.** When Soulseek is
enabled, the folder peers can download from is now the station's AzuraCast media
folder when one is configured, falling back to the download folder in local mode.
The radio's music library is shared back to the network out of the box, while
files RequestCast is still working on stay private.
