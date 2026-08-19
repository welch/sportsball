# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Events vanished from the past the day after they happened. Cron replaces the
  snapshot with whatever the adapters just returned, so an event survived only
  as long as its source kept listing it — and Ticketmaster's Discovery API
  drops events once they are over: a past window returns nothing where an
  upcoming one returns dozens. Every concert and comedy night was being erased
  a day after the fact. It stayed invisible because MLB publishes a whole
  season, so Giants games kept showing up on past days and the calendar never
  looked empty.

  A source dropping a *future* event still means something — a cancellation, a
  reschedule — and still removes it. A source dropping a past one now means
  nothing: the event is carried forward from the previous snapshot instead.
  Retention is bounded to a year back, matching the browsable date space,
  since keeping what nobody can navigate to would only grow the blob. Events
  are matched on `(source, source_id)` and a still-reported event always takes
  its fresh copy, so history is added without ever shadowing an update.

  This stops the loss; it cannot undo it. Ticketmaster events from before
  2026-08-19 are gone, beyond whatever is recoverable from the bucket's
  soft-deleted snapshots.

### Changed

- The webfont blocks rather than swaps. On a genuinely cold visit — nothing
  cached, and the cache is per-origin so each domain pays it once — the
  headline could still be seen arriving, because `swap` paints the fallback
  and replaces it. `block` holds the text instead, briefly and invisibly, so
  the face is the first thing a reader sees rather than the second. The wait
  is bounded at roughly 3s by the browser and is tens of milliseconds here:
  19.8 KB, same origin, preloaded. The metric-matched fallback still stands
  behind it for that expiry, for a glyph the latin subset lacks, and for a
  font that never arrives at all.

## [0.9.0] - 2026-08-18

### Changed

- The site is set in Amatic SC rather than Permanent Marker, on every domain.
  The polite domain was meant to be handed to a stranger, and the marker hand
  is an in-joke a first-time reader has no way into; Amatic is odd enough to
  still be a voice and quiet enough not to be a punchline. Sizes come up about
  a third to match — it is a condensed caps face and sets much smaller at the
  same px — which as a bonus keeps the question on one line where the old face
  wrapped it.
- The date and the verdict now sit a notch under the question rather than
  matching it. They had shared the h1's size since there was nothing else to
  compare them against; Amatic's extra height made the flatness obvious. Net
  of both changes the answer lands *higher* on a phone than before — the
  verdict's bottom edge measures 558px at 390px wide, against 571px prior.
- The about page no longer offers the typography as something a fork could
  improve on. It was accurate about Permanent Marker and is not about what
  replaced it, and a site that apologises for its own design invites the
  reader to agree. The sentence also gains the full stop it never had.
- The footer link keeps the system face on purpose: it is the one link into
  `/about`, which is set in that same stack, so it previews where it goes. It
  thins to weight 300 and darkens to `#555` so it sits beside Amatic's stroke
  instead of shouting next to it.

### Removed

- `NAV_HINTS`, and with it `NAV_HINT_STYLES`, the `?nav=` override and the
  trailing-glyph machinery. It shipped in 0.8.0 on the theory that the
  everyday domain wanted its links undressed and only a domain handed to a
  stranger needed telling. Both domains wanted telling, which makes a
  per-domain setting a knob nothing turns — the kind of configuration that
  later reads as significant when it is only vestigial. The dotted rule is
  unconditional now, and the glyph that trailed it is gone: once the rule was
  carrying the affordance the glyph was noise, and it was the one mark on the
  page no hand-drawn face could actually draw.

### Fixed

- The headline still flickered after the font was self-hosted, more often on
  the calendar and now rendering *smaller* rather than larger. Two causes. The
  fallback's `size-adjust` had been derived from a sample typed in capitals,
  and Amatic is a caps face while Arial's capitals are much wider than its
  lowercase — so 54% fitted the test string and set the real lowercase
  headline 13% narrow. Measured against the strings the site actually renders
  the range is 58-72%, and 60% holds both headlines inside 4%. Second, the
  font could not begin downloading until the stylesheet had been fetched and
  parsed, and static files were served with `no-cache`, so every navigation
  revalidated and reopened the window. The font is now preloaded — it starts
  with the HTML, alongside the stylesheet rather than behind it — and
  `/static` is cached for a year, which is safe because the stylesheet carries
  a content hash and the font's filename carries its version.
- The headline flashed in the fallback face before settling, roughly one
  navigation in three. `font-display: swap` paints fallback text until the
  webfont arrives, and the font itself lives on `fonts.gstatic.com` — a second
  origin whose DNS lookup and TLS handshake could not begin until the font CSS
  had come back from `fonts.googleapis.com`. Whether that connection was still
  warm is what made it intermittent. Arial sets this headline 1.86x as wide as
  Amatic, wide enough to wrap to two lines on a phone and rewrap on arrival,
  so the swap read as a flash of something much larger.

  The font is now served from this origin (latin subset, 19.8 KB, OFL 1.1 with
  the licence alongside it), which removes both third-party origins from every
  page load. A companion `@font-face` gives the fallback Amatic's proportions
  via `size-adjust` and matching vertical overrides, so a swap that does happen
  changes the shapes without moving anything: the headline measures within
  0.5% of Amatic's width in the fallback, against 86% wider before.

- The dotted rule under a nav link vanished on mouseover instead of firming up
  to solid. Making the affordance unconditional dropped it from
  `body.nav-hints a.nav-link` to a bare `a.nav-link`, which is less specific
  than the `a.plain:hover` reset the date link also matches — so the hover
  rule kept winning the style and colour while losing the underline itself.
  Both rules now state `text-decoration-line` outright.
- `prefers-reduced-motion: reduce` disabled a link glyph's 130ms transition
  and left the answer window's infinite pulse running, which had it exactly
  backwards. It now stops the glow, the only motion left on the page.
- Every phone got a few pixels of sideways scroll. Under the 480px breakpoint
  the body is `width: 95%` with `1rem` of padding, and with no `box-sizing`
  rule anywhere that padding landed outside the percentage — a 390px viewport
  rendered a 403px document. Set `border-box` globally; the document now
  matches the viewport exactly.

## [0.8.0] - 2026-08-18

### Added

- `NAV_HINTS`, per-domain navigation affordances. The site's links are
  undressed on purpose — a blue underline would wreck the marker-pen hand —
  which is right for a page someone visits daily and wrong for one they've
  never seen, where nothing says the page responds at all. A domain handed
  to a stranger can now dress the links carrying a `nav-link` class (the
  date → month, the footer → about) while the everyday domain stays as it
  was. The one style, `chevron`, is a dotted rule that firms up on hover,
  trailing the `›` the calendar's pager already uses — Permanent Marker
  has no `→`, so an arrow drops to a fallback font and lands beside the
  marker text as an obviously foreign thin line, while `›` is in the face
  and arrives in the same hand. Which links are navigation is a template
  decision and how they look is entirely `8ball.css`'s, so reworking the
  visual doesn't touch Python. `?nav=<style>` overrides for one request —
  `?nav=off` for the undressed original — which is how to try one without
  a restart. The canonical tag is built from the path alone, so the query
  variants stay out of search.
- `HOST_VERBS` — a map from domain to verb (`ismydayfucked.com=fucked,
  ismydayhosed.fun=hosed`), so one deployment serves several domains that
  differ only in the word they render. The second domain exists to be
  linkable from a résumé; a second App Engine service would have meant a
  duplicated config and a second set of instances drawing down the same
  per-app free instance-hour quota, for a one-word difference. Any host
  outside the map is 301-redirected, and a `www.` prefix goes to the domain
  beneath it when that domain is in the map: `www.ismydayhosed.fun` reaches
  `ismydayhosed.fun`, rather than folding onto the first entry and bouncing
  someone who deliberately typed the polite name over to the impolite one.
  Everything else — appspot.com, bare IPs, stale aliases, and `www.` of a
  domain the map doesn't carry — still consolidates onto the first entry.
  Unmapped hosts render the first entry's verb, so local dev still looks
  like the live site.

### Changed

- The footer link is now called "about" rather than named after the
  timestamp it carried ("last updated Thu Aug 13, 6:00 AM PDT"), which told
  a first-time visitor nothing about where the link went. It also renders
  unconditionally: it used to appear only when the cache had a timestamp to
  show, so the site's one link to `/about` vanished on an instance that had
  never loaded a snapshot.
- The refresh time moved to the about page, under what the site tracks,
  where a visitor asking how current the data is will actually be looking.
  The page now primes the event cache the way `/health/<token>` does, since
  a visitor can land there first and it would otherwise have no time to
  report. Operators still get the precise picture on the health page.
- Each configured domain now canonicalizes to itself rather than to a
  single host. Folding them together would consolidate the search signal,
  but it would also mean someone who found the polite domain in search got
  pointed at the impolite one — the one thing the polite domain exists to
  prevent. The pages aren't true duplicates anyway; the verb differs
  throughout.
- The day page's `description` and `og:title` now carry the requesting
  domain's verb, as its `<title>` already did. Both were written as
  literals, from back when there was only one domain to write, so the
  polite domain served a polite title alongside a blunt search snippet and
  a blunt chat unfurl — which is the part most people see before they ever
  load the page, and so the part that decides whether the domain is
  safe to put on a résumé at all. The calendar page already did this right.

### Removed

- The verb path segment (`/fucked/`, `/fucked/2026-12-25`,
  `/fucked/calendar/2026-12`), inherited from a mirroring scheme the host
  no longer supports. Nothing in the site linked to it and the domain
  carries the verb now, but `VerbConverter` matched any letters-only
  segment, which made every page reachable at unboundedly many URLs — the
  reason the canonical tags had to strip a path segment and robots.txt
  needed a second `/*/calendar/` rule. All of that goes with it, along with
  `_nav_urls`, which existed only to thread the visitor's verb through
  every in-page link. Those URLs now 404.
- `VERB` and `CANONICAL_HOST`, both subsumed by `HOST_VERBS`. A deployment
  carrying the old pair loses its redirect and falls back to the default
  verb (`hosed`) until the new variable is set.

### Fixed

- `__version__` had been left at 0.6.0 since the 0.7.0 cut. The
  Ticketmaster adapter puts it in the outbound `User-Agent`, so the app
  introduced itself as `sportsball/0.6.0` to the one API that reads it.
  It now tracks the release, and cutting a version updates it.

## [0.7.0] - 2026-08-09

### Fixed

- `/health/<token>` returned a 500 instead of a page. The traffic summary
  counts a 24-hour window by walking Cloud Logging entries one at a time —
  there's no server-side count — at roughly 2.75ms apiece, so the cost of
  rendering the page scaled with how much traffic the site had. A crawler
  flood (below) took the window to 93,451 entries and the scan to ~182s,
  well past gunicorn's 30s timeout, so the worker was killed mid-request.
  The existing `except Exception` guard couldn't degrade the page the way
  it was meant to, because a killed worker raises `SystemExit`, which isn't
  an `Exception`. The scan now stops at a 5s deadline (with a 20k-entry
  backstop) and flags the result, and the page renders the counts with a
  trailing `+` plus a warning that they're floors rather than totals. The
  guard stays `except Exception` on purpose: swallowing a worker abort
  would leave a process running that gunicorn is trying to kill. Bounding
  the work is what prevents the abort.

### Changed

- The browsable date range is bounded to a year either side of the current
  one, rounded out to whole calendar years. The calendar's chevrons used to
  step forever in both directions, and every day cell linked to a day view
  that linked back to a month — an unbounded URL space. In August 2026
  GPTBot walked it out to `/calendar/9241-09` and sustained ~7,000 requests
  an hour for over a day, which is what pushed the health page's log scan
  past its timeout. Dates outside the range 404, and at the edges the
  chevron and the spill-over day cells render as plain text rather than
  links, so a crawler finds nothing to follow. The bound is far past any
  adapter's horizon — MLB and NBA publish a season, Ticketmaster about a
  year — so browsing in good faith never reaches it.
- Added `robots.txt`, closing `/health/`, `/healthz`, `/tasks/`, and the
  month view — the calendar exists to be clicked from the day page rather
  than found cold in search, and it's the densest part of the crawlable
  space. Closing it takes two rules: `Disallow: /calendar/` matches only
  paths that begin that way, so the verb-prefixed `/fucked/calendar/2026-08`
  needs the wildcard form as well. The advertised crawl delay is advisory
  and Google ignores it outright; the real defence is the bounded range.
- Every page now carries a `<link rel="canonical">` pointing at its
  verb-less URL. `VerbConverter` matches any letters-only segment, so
  `/fucked/`, `/banana/` and every other word render the same content at a
  different URL — an unbounded set of duplicates sitting behind the bounded
  date space. Nothing links to an arbitrary verb, so a crawler starting at
  `/` never wanders in, but one shared `/fucked/` link is enough to pull
  that entire subtree into the index. The tag is `<head>` metadata only:
  in-page links still carry the visitor's verb, so someone who arrived at
  `/fucked/` stays there as they navigate. Built against `$CANONICAL_HOST`
  so the tag names the real domain rather than whatever `Host` header
  arrived.

## [0.6.0] - 2026-07-29

### Changed

- Day colors now answer two questions on two channels instead of conflating
  them. Hue says *where* — orange for Oracle Park, blue for Chase Center.
  Texture says *what* — a soft glow for a home game, a dashed ring in the
  same hue for anything else at that venue. They compose, so a Giants game
  plus a concert at Oracle Park draws an orange glow with an orange dash
  through it, which the old scheme couldn't express at all. Purple retires.
  This started as a bug: Ticketmaster files monster trucks under segment
  "Sports", so `_to_event` handed back the same classification a Warriors
  game got and Aug 15 drew a solid Warriors ring over a monster truck
  rally. Segment was always the wrong thing to key on — whether a home team
  is playing is something the *adapter* knows, so `Event.category`
  (`sports`/`concert`, inferred from a genre string) is replaced by
  `Event.kind` (`home`/`event`, asserted at the source). Giants and Warriors
  adapters always say `home`; Ticketmaster says `home` only for the WNBA
  subGenre, the one home team still arriving through that feed.
  Snapshots written before the rename still load — a validator maps the old
  `category` via `source` — so the hours between deploy and the next cron
  don't send every cold start back to the adapters.
- The dashed rings are built from a repeating conic gradient masked by a
  radial one, rather than a `dashed` border that offered no control over
  frequency and stamped out a hard stroke fighting the soft glows. Both
  venues' rings share a single band and interleave: each draws across the
  first half of every period, Chase starting half a period later so its
  dashes land in Oracle's gaps. One venue busy reads as a dashed ring;
  both composite into a complete ring alternating orange and blue, so the
  density itself says "both buildings". The phase is derived from the
  period so the 8-ball's shorter period can't desync the two.

### Added

- The health page's header line now links both of its facts to their own
  evidence. The build string points at the commit it was built from —
  `bin/deploy` already encoded the short SHA into the GAE version ID, so
  `v0.5.0+008a635` becomes a link to `$REPO_URL/commit/008a635`. A dirty
  deploy still links its commit, since "(dirty)" is what says the running
  code isn't exactly that tree; an ID that carries no SHA (timestamp-style,
  or local dev) falls back to the repo root. The "as of" timestamp links to
  the day page for that date, so a health check is one click from what the
  site was actually saying at the time.
- Warm instances now notice a new snapshot within a minute instead of
  waiting out the 12-hour cache. `_events()` polls the storage blob's GCS
  generation number on a 60-second interval — a metadata GET, not the
  payload — and re-downloads only when it differs from the generation it
  loaded. `bin/refresh` used to reach only the instance that served
  `/tasks/refresh`; every other instance sat on stale events until it cold
  started. The old TTL stays as a backstop. An unreadable generation counts
  as "unchanged", so a GCS hiccup leaves instances serving cache rather
  than falling through to the adapters, and with no `EVENTS_BUCKET` the
  check costs nothing.
- `REPO_URL` joins the `env.yaml` settings. It isn't secret, and it's
  configuration rather than a constant for one reason: a fork should link
  its own source, not this repo's. Unset, the build string renders plain —
  the right answer for a deploy whose source isn't public.

- `bin/refresh` forces the event snapshot to rebuild without waiting for
  the 06:00 cron, and `bin/deploy --refresh` runs it once a deploy lands.
  `/tasks/refresh` is gated on the `X-Appengine-Cron` header, which GAE
  strips from anything off the internet — but nothing strips it locally, so
  the script runs the app on a loopback port, sends the header itself, and
  lets the ordinary handler do the work. This replaces the Cloud Console's
  "Run now" button, which no longer appears for classic App Engine cron
  jobs (they aren't mirrored into Cloud Scheduler either). Refuses to start
  if `EVENTS_BUCKET` resolves to nothing, since the write would be a silent
  no-op, and `--refresh` only fires after a successful deploy — a snapshot
  written by newer code can carry fields the running version can't read.
- Venue names in the 8-ball's event descriptions are tinted with their own
  hue, matching the day's rings — "at Oracle Park" in orange, "at Chase
  Center" in blue. Useful on days that list both. An untracked venue
  renders plain rather than guessing at a color.
- Monthly calendar view at `/calendar/<YYYY-MM>` (and `/calendar/` for the
  current month). Every day wears the same colored rings as the 8-ball
  halo, so a month's worth of parking misery reads at a glance — see the
  ring scheme under Changed. Chevrons step a month in either
  direction with no bound; clicking a day drops into the 8-ball view for
  that date, and clicking the date under the 8-ball opens its month. A
  verb in the path (`/fucked/…`) survives every hop. Days spilling in from
  the adjacent months are dimmed but still live, since a game on the 1st
  matters when you're looking at the 31st.
  The halo rules moved to `aggregator.day_halos()` and the `.halo-*` CSS
  dropped its `.ball-frame` qualifier, so both pages read one definition
  of "what color is this day?" rather than two that can drift apart.

- "...but what about <random MLB city>?" — the about page's "other cities"
  header now picks a random MLB home city per request from the 27 current
  homes (San Francisco intentionally excluded). Refreshing the page is
  surprisingly entertaining.
- Hero photo on `/about`: heavy game-day traffic outside Oracle Park with
  a singer mid-song atop a car. Placed directly under the centered title;
  the rest of the page reads as commentary on the picture. ~260 KB JPEG,
  cache-busted like the other static assets.

### Changed

- Warriors schedule now comes from ESPN's public API
  (`site.api.espn.com/.../teams/gs/schedule`) instead of `cdn.nba.com`.
  Akamai escalated from header sniffing to blocking by source IP and began
  403'ing App Engine's egress ranges on Jun 16 — a block no header dressing
  can clear from a datacenter IP (the request still 200s from a residential
  IP). ESPN serves the same games and isn't gated that way. The adapter
  fetches all three season phases (preseason / regular / postseason) and
  computes the season label itself, since ESPN splits them across payloads.
  `Event.source` for these is now `espn_nba`.
- `/about` blockquote no longer name-checks a specific artist — just
  "a concert at the Chase Center" so the joke doesn't date.
- Defensive headers on the other two upstream adapters too. Giants (MLB
  Stats API, also Akamai-fronted) now sends a Chrome-shaped UA plus
  `Origin`/`Referer` for `www.mlb.com`. Ticketmaster Discovery API gets
  a `sportsball/0.5.0` UA and explicit `Accept: application/json` — the
  API key already authenticates us so no need to masquerade as a browser
  there. Verified all four adapters still 200 with the new headers and
  return their expected event counts.

### Fixed

- Ticketmaster adapters no longer die on events with no announced start
  time. Discovery omits `dates.start.dateTime` entirely when `timeTBA` is
  set, and `_to_event` reached straight for that key — so a single
  time-TBA event (a Dec 20 college basketball doubleheader, listed Jul 16)
  raised `KeyError: 'dateTime'` and took down all 29 Chase Center events
  with it. Such events now fall back to midnight venue-local on their
  announced `localDate`, which lands them on the right day, and carry a
  new `Event.time_tba` flag so the 8-ball and health pages print "time
  TBA" instead of a fictitious 12:00 AM. An entry with neither
  `dateTime` nor `localDate` (`dateTBD`) is logged and skipped rather
  than crashing the run.
- Warriors adapter unblocked from Akamai's WAF in front of `cdn.nba.com`.
  The endpoint started 403'ing requests that didn't look like real
  nba.com-fetched XHRs. Adding browser-context headers (Chrome-shaped
  User-Agent, `Origin: https://www.nba.com`, `Referer: https://www.nba.com/`,
  `Accept`, `Accept-Language`, and the `sec-ch-ua` / `sec-fetch-*` signals
  the actual nba.com client sends) gets us through. Tested live: 89 GSW
  events fetched.
- Cron now preserves historical `last_success_at` for adapters that fail
  this run. The handler loads the prior snapshot's `adapter_stats` before
  calling `fetch_all`, so `record_adapter_failure(...)` keeps the
  previous success metadata. Before this, a single bad day wiped out
  "when did this adapter last work?" on the health page — exactly the
  signal we needed when cdn.nba.com started 403'ing.

## [0.5.0] - 2026-05-08

### Added

- Cloud Storage backing for the events snapshot. Cron now fetches all
  adapters, writes the resulting events list + `fetched_at` timestamp
  to `gs://$EVENTS_BUCKET/events.json` (one ~200 KB JSON blob), and
  updates the local cache. Serving instances read that blob on cold
  start instead of re-fetching adapters individually — cuts a fresh
  instance's first-request latency from a multi-second multi-adapter
  fetch (the NBA league feed alone is ~8 MB) to a single small
  Storage read.
- `_events()` reads from Cloud Storage on cache miss and falls back to
  direct adapter fetching when the bucket is unset (local dev) or the
  blob is missing/unreadable. Local dev keeps working.

### Changed

- The page's "last updated" timestamp now reflects the cron's
  `fetched_at` (read out of the stored blob), not each instance's own
  first-fetch time. So a fresh 9 AM instance still shows "last updated
  06:00" — matching the actual upstream-data refresh frequency.
- `tests/conftest.py` now clears env-driven configuration (`VERB`,
  `CANONICAL_HOST`, `HEALTH_TOKEN`, `EVENTS_BUCKET`) before each test
  so the suite is hermetic regardless of the developer's local `env.yaml`.
- Cron now persists not just `(events, fetched_at)` but also
  `previously_unseen` — the events whose `(source, source_id)` wasn't
  in the prior cron's snapshot. The health page renders that list as a
  "New since previous cron" table so you can see exactly what each cron
  brought in. First-ever run flags everything as new (no prior to diff
  against).

### Added

- `/about` page — plain-language explanation of the site for casual
  visitors. The "last updated" footer link in the page footer now points
  here instead of straight at the GitHub repo. The repo link lives at
  the bottom of `/about` for anyone who wants the code.
- "Page views" tile on the health page — a separate count of 2xx
  responses in the last 24h, distinct from the all-status total. Most
  3xx hits are unfollowed canonical-host redirects from legacy-URL bot
  probes, and most 4xx hits are vulnerability scanners. The 2xx-only
  number is the real "humans loaded my page" metric.
- `bin/deploy` — wrapper around `gcloud app deploy app.yaml` that
  encodes the current git state (`<tag>-<sha>-<clean|dirty>`) into the
  GAE version ID. The deployed app reads `$GAE_VERSION` (auto-set by GAE)
  and renders it on the health page, so "what's actually running" is
  visible at a glance. Bare `gcloud app deploy` still works; the
  timestamp-shaped ID it produces is shown raw, which itself signals
  "this wasn't deployed via bin/deploy."

### Fixed

- Per-adapter stats (last success / last failure / event count) now
  survive instance lifecycle. Cron persists a `snapshot_adapter_stats()`
  alongside the events blob; the storage-read path calls
  `stats.load_adapter_stats(...)` so any serving instance reflects the
  cron's view of adapter health. Without this the health page on a
  fresh-after-scale-to-zero instance showed every adapter as "never
  ran" — accurate-but-misleading because the cron *had* run, just on
  an instance that no longer existed.
- HTTP-request counts on the health page are now queried from Cloud
  Logging (with a 5-minute in-process cache) rather than tracked in an
  in-process deque. The deque was per-instance and reset to zero every
  time GAE scaled to zero, which made the count non-monotonic and
  effectively useless. Cloud Logging is GAE's existing log stream — no
  new infrastructure to manage. The query filter excludes `/health/`,
  `/healthz`, and `/tasks/refresh` so operator polling, GAE health
  probes, and cron invocations don't inflate the user-traffic count.
  Adds a `roles/logging.viewer` IAM grant requirement for the App Engine
  default service account; documented in the README setup section.
- Canonical-host redirect now skips requests whose `Host` is `localhost`,
  `127.0.0.1`, or `0.0.0.0`, so a developer with the production
  `CANONICAL_HOST` in their local `env.yaml` can still hit
  `http://localhost:PORT` without being bounced to production.

## [0.4.0] - 2026-05-06

### Added

- `/health/<token>` status page, gated by the `HEALTH_TOKEN` env var
  (wrong/missing token 404s — never reveals the endpoint exists). Renders
  a plain HTML snapshot of: per-adapter last-success / last-failure
  timestamps + event count + error message; rolling 24-hour HTTP request
  totals broken down by status-code class (2xx/3xx/4xx/5xx); cached event
  count and last-refresh age in human-friendly form. Backed by a new
  `sportsball.stats` module — thread-safe in-process telemetry over a
  pruned `deque`, no new dependencies. `aggregator.fetch_all` now takes
  named adapter tuples and records success/failure per source. The health
  endpoint itself is excluded from the request counter so reloading it
  doesn't pollute the numbers.
- 301-redirect non-canonical hosts to the host configured via the
  `CANONICAL_HOST` env var. `before_request` hook reads the canonical
  bare hostname from `os.environ` and redirects to
  `https://$CANONICAL_HOST<path>?<query>` whenever `request.host`
  differs. No-ops when the env var is unset (local dev). `/healthz` and
  requests with `X-Appengine-Cron: true` are exempt so GAE health checks
  and cron continue to work on the appspot host. Busts stale 301s
  cached by browsers from the previous reverse-direction redirect.
- Pulsing-glow effect on the 8-ball's answer window. A duplicate of the
  ball image is layered over the base, clipped to a circle around the
  answer window (`circle(23% at 50% 47%)`), and animated with a 3-second
  filter pulse (drop-shadow + brightness + contrast) that holds at peak
  for a third of the cycle. The base image stays static, so only the
  "SIGNS POINT TO YES" / "OUTLOOK NOT SO GOOD" / etc. region shimmers.
- Footer "last updated …" timestamp now links to the GitHub repo
  (`https://github.com/welch/sportsball`). Anchor opens in a new tab
  (`target="_blank"` + `rel="noopener noreferrer"`) and inherits the
  footer's grey color with no underline across all link states, so the
  footer looks identical to before — just clickable.

## [0.3.0] - 2026-05-05

### Added

- `/tasks/refresh` endpoint that invalidates the in-memory event cache
  and refills it. Gated by the `X-Appengine-Cron` header (GAE strips this
  from external requests), so external callers can't trigger refetch
  storms. `cron.yaml` schedules it daily at 06:00 PT.
- Page-date row under the 8-ball: full date in the title font, colored
  using the same day-color (Giants orange / Warriors blue / concert
  purple) as the verb and halo. Aids in eyeballing arbitrary `/<isodate>`
  pages.
- Footer: small grey "last updated &lt;ts&gt;" timestamp showing when the
  in-process event cache was last refreshed. Doubles as visible
  verification that `/tasks/refresh` actually ran.

### Fixed

- Halo radial-gradient stops were being measured against the wrapper's
  diagonal (`farthest-corner`, the CSS default), which placed inner-ring
  colors fully behind the 8-ball image. Switched to `closest-side` so
  percentages correspond to the visible halo zone (60–100%); two- and
  three-halo days now show all rings as intended.
- Static-asset cache-busting. All `<link>`/`<img>` URLs now carry a
  `?v=<hash>` query, where `<hash>` is a content hash of every file in
  the static directory. Computed once at startup. When any CSS or image
  changes, the hash rotates and browsers refetch automatically — no more
  manual cache-clearing after deploys.
- Ticketmaster Discovery adapter, single module covering both venues
  (`fetch_oracle_park_events()` / `fetch_chase_center_events()`). Filters
  out `subGenre` `MLB` and `NBA` so it doesn't duplicate the team adapters;
  WNBA Valkyries home games and Motorsports/Racing events pass through.
  Reads `TICKETMASTER_API_KEY` from `os.environ`.
- Per-adapter resilience in `aggregator.fetch_all()` — one failing source
  no longer blanks the page; the failure is logged and the remaining
  adapters' events render. Ticketmaster's API has been observed returning
  502s on retry, so this isn't theoretical.
- `Event.category` (Literal `"sports"` | `"concert"`) and per-category
  page theming. Sports events keep their team color (Giants orange at
  Oracle Park, Warriors blue at Chase Center). Concert/non-sports events
  get a third color (purple `#7E2F8E`) for both verb and halo. WNBA
  Valkyries are tagged `sports` (they're a team game) and share the
  Warriors blue. Halos compose: a day with a Giants game and a concert
  shows orange + purple concentric rings.

## [0.2.0] - 2026-05-05

### Added

- Production deploy on GAE Standard python312 working at
  `https://sports-ball.appspot.com/`. Required:
  - Generate `requirements.txt` from `uv.lock` (the python312 buildpack
    reads `requirements.txt`, not `uv.lock`). Pre-commit hook keeps it
    in sync; CI fails on drift.
  - `--pythonpath src` in the gunicorn entrypoint so the `src/` package
    layout resolves at runtime.
  - One-time IAM grant of `roles/cloudbuild.builds.builder` to the App
    Engine default service account on this older project (modern projects
    auto-grant; pre-second-gen ones don't).
  - One-time enable of `artifactregistry.googleapis.com` and
    `cloudbuild.googleapis.com`.
- `VERB` env var sets the default verb in the page title, read at request
  time. Falls back to `hosed` when unset. URL-segment verbs still override
  per-request. Lets the rude default stay out of git via `env.yaml`.
- 8-ball page wired up over the multi-adapter aggregator, in three states:
  - **Today fucked**: lists every event happening today; ends with
    "No peace and quiet until [tomorrow / Monday / Monday, May 12]."
  - **All clear, future event scheduled**: title-sized line "All clear
    until [day/date]:" with each upcoming event in parentheses.
  - **All clear, nothing scheduled**: "All clear. No future events scheduled."
- URL routes: `/`, `/<verb>/`, `/<isodate>`, `/<verb>/<isodate>`. `<verb>`
  is letters-only; `<isodate>` is `YYYY-MM-DD` and 404s on invalid dates.
  Querying a date treats that date as "today" for status computation.
- Page modernizations vs the v0 port: mobile viewport meta, semantic
  `<ul>`/`<li>`/`<time>` markup, color tied to active venue (Giants
  orange / Warriors blue applied only to the verb in the title and to
  the team names "San Francisco Giants" / "Golden State Warriors" inline).
- Per-venue halo around the 8-ball — radial-gradient glow showing which
  team(s) own the day. Two-team days draw concentric halos (orange inner,
  blue outer). Halo only renders on today-fucked days.
- `aggregator` module: `fetch_all()` rolls up adapters and filters to
  tracked venues; `compute_status()` returns a `Status` with today's
  events, the next future-event date, and the next quiet date.
- 12-hour in-process cache around the adapter fetch (NBA league feed is
  ~8 MB; refetching per request would be unworkable).
- Pydantic `Event` model and Giants adapter against the MLB Stats API
  (`statsapi.mlb.com`). One `Event` per game with venue name preserved
  so home/away is filterable by `venue == "Oracle Park"`.
- Warriors adapter against the NBA league-wide CDN feed
  (`cdn.nba.com/static/json/staticData/scheduleLeagueV2.json`). Filters to
  GSW; same `Event` shape as Giants. Home/away by `venue == "Chase Center"`.
- Secrets pattern: single gitignored `env.yaml` at the repo root, deployed
  to GAE via `app.yaml` `includes:` and loaded into `os.environ` at app
  startup locally. One source of truth, no `.env`/YAML duplication.

### Removed

- `icalendar` dependency. None of the four planned feeds use iCal.

## [0.1.0] - 2026-05-04

### Added

- Initial repository scaffolding: `uv` project, `ruff` + `ty` + `pytest` + `pre-commit`,
  GitHub Actions CI, GAE `app.yaml` / `cron.yaml`, MIT license.
- Minimal Flask skeleton (`/`, `/healthz`) so the deploy target builds and serves.
