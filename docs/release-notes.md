musicdl joins the download chain, and its platforms' URLs now work.

**A second fallback before YouTube.** When Deezer cannot supply a track — or no Deezer
ARL is configured — RequestCast now asks [musicdl](https://github.com/CharlesPikachu/musicdl)
before resorting to YouTube: it searches the configured platforms for a clean
artist/title match and downloads the best result. YouTube via yt-dlp remains the last
resort, so nothing that worked before is lost. The fallback is on by default; turn it
off in Settings or with `REQUESTCAST_MUSICDL_ENABLED=0`, and choose which platforms it
searches with `REQUESTCAST_MUSICDL_SOURCES` (the default is musicdl's own reliable
five: Migu, NetEase, QQ, Kuwo, and Qianqian).

**URLs from twenty more platforms.** Paste a track or playlist URL from NetEase,
QQ Music, Kugou, Kuwo, Migu, Qianqian, Spotify, SoundCloud, TIDAL, Qobuz, Apple Music,
JOOX, JioSaavn, Jamendo, Soda, StreetVoice, FMA, Suno, MOOV, 5SING, Bodian, or Bilibili
and RequestCast downloads it through musicdl directly — same tagging, same quality
checks, same destination as everything else. The same URLs work inside uploaded TXT,
XLSX, PDF, and playlist files.

**Downloaded files keep the same treatment.** However the audio arrives, it is tagged
with the track's metadata, verified with ffprobe, and made readable by every account
on the machine before it lands in the download folder or the AzuraCast library.

Note: musicdl is licensed for non-commercial use; see its repository for the terms.
