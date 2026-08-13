RequestCast now recognises live events that it did not start — a scheduled show
like A State of Trance, a remote DJ, or another tool feeding the live harbor —
and yields to them.

**Outside events are detected and shown.** The home page now reports the live
event currently on air (from the station's nowplaying API) alongside RequestCast's
own relay. RequestCast's own relay is told apart by matching its broadcast start
time, so it never shows up twice.

**Live events take priority.** When a live event is already on air, "Add &
request" on a YouTube livestream now reports that the event has priority instead
of connecting a second source over it. While a relayed stream is playing, the
title/metadata loop stops the moment an outside event takes over, so RequestCast
never overwrites the event's own title; and when a relay ends, the watchdog only
marks the live over if no outside event has taken over the harbor — so an
incoming show is not wrongly reported as ended.
