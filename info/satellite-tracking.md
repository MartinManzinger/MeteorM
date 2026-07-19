# Satellite tracking

## Interfaces and dependencies

`TLEProvider.get_cached_tle(satellite)` reads validated orbital data without
network I/O. `TLEProvider.get_tle(satellite, force_refresh=True)` owns explicit
online retrieval and cache policy. `CelestrakTLEProvider` is the current
implementation. `SatelliteTracker` accepts a validated `TLERecord` and performs
propagation only; it never performs file or network I/O.
`SatelliteTrackingService` coordinates these operations on a worker thread and
publishes immutable `OrbitSnapshot` values through a bounded queue.

`SettingsStore` is the single persistence owner. The provider receives its
small cache interface instead of opening YAML itself, which keeps downloading,
propagation, and persistent storage replaceable.

Skyfield 1.49 or newer supplies the SGP4 propagator and WGS84 conversion. Its
built-in timescale is used, so propagation does not trigger a second download.

## Satellite catalog

The internal catalog currently defines:

| Application name | CelesTrak name | NORAD ID | Default LRPT tune | Map color |
| --- | --- | ---: | ---: | --- |
| METEOR-M N2-3 | METEOR-M2 3 | 57166 | 137.9000 MHz | cyan |
| METEOR-M N2-4 | METEOR-M2 4 | 59051 | 137.9000 MHz | magenta |

Both spacecraft have independent activation checkboxes, workers, cache records,
manual fetch actions, information windows, and SDR tune actions. The mandatory
map labels each marker and uses matching colored tracks and a legend. WMO OSCAR
lists 137.1, 137.9, and 137.9125 MHz as assigned LRPT frequencies for these
spacecraft, so the displayed 137.9000 MHz tune is a reception default rather
than a guarantee that a spacecraft has not switched frequency.

## TLE source and cache policy

After the Satellite tracking panel is loaded, each enabled worker first asks
only for its own validated cached record. It does not automatically refresh
stale data. Each **Fetch TLE** action requests only that catalog object from:

```text
https://celestrak.org/NORAD/elements/gp.php?CATNR=<catalog-id>&FORMAT=TLE
```

CelesTrak documents a two-hour minimum between requests and recommends using
the last successful download by default. A manual request made less than two
hours after the last successful retrieval therefore returns the cache without
contacting CelesTrak. When refresh fails, the current validated cache remains
available and the GUI reports the failure.

The GUI action sets an event for the tracking worker; it never downloads in the
GUI thread. Every refresh, skip, fallback, and validation failure is sent to the
application log. On a fresh installation with no cached TLE, the tracking panel
asks the user to use this action rather than silently making an online request.

The human-readable cache is stored inside the repository-root settings file:

```text
settings.yaml → cache → tle
```

Receiver, location, satellite, interface, and cached TLE updates all use the
same lock and atomic file replacement. No application-specific directory is
created under the user's home folder.

Downloaded data is accepted only when the response contains one matching
catalog object, both TLE lines are exactly 69 characters, catalog IDs agree,
and both checksums pass. HTTP errors are not retried in a loop.

## Accuracy assumptions

Positions are geodetic WGS84 latitude/longitude and height above the WGS84
ellipsoid. Ground tracks cover 60 minutes before through 120 minutes after the
current time at 12-second spacing (901 propagated points, ten times the original
resolution). The map draws the current position as a circle. Small crosses and
local-time labels mark interpolated positions at every wall-clock `XX:00` and
`XX:30`; the historical line uses a steep opacity falloff toward the oldest
point to make travel direction visible while reserving more of the track for
future planning. Right-clicking a current-position circle opens a map context
action that applies that satellite's configured LRPT frequency to the SDR
settings. Skyfield notes that satellite elements are generally useful
only near their epoch, so the GUI exposes whether the last valid cache was used
instead of presenting stale data as newly downloaded data.

Calculated coordinates are deliberately not persisted. A stored position is
valid only for its calculation time, while calculating a new position from a
cached TLE is local and inexpensive. Persisting the TLE therefore supplies the
useful offline behavior without displaying a stale coordinate as current.

This is display and receive-planning data, not flight-safety data. Visible-pass
prediction relative to the configured receiver, including azimuth, elevation,
rise, culmination, and set times, is intentionally a separate next step.

## References

- [CelesTrak GP query formats](https://celestrak.org/NORAD/documentation/gp-data-formats.php)
- [CelesTrak current weather satellites](https://celestrak.org/NORAD/elements/table.php?GROUP=weather)
- [Skyfield Earth satellite documentation](https://rhodesmill.org/skyfield/earth-satellites.html)
- [WMO OSCAR Meteor-M N2-3](https://space.oscar.wmo.int/satellites/view/meteor_m_n2_3)
- [WMO OSCAR Meteor-M N2-4](https://space.oscar.wmo.int/satellites/view/meteor_m_n2_4)
