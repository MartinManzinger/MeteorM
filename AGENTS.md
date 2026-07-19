# AGENTS.md

## Purpose of this file

This is the working brief for contributors and coding agents. It records the
current product state, the required repository structure, architectural rules,
and the next unfinished milestones. Read it before changing the project.

## Project goal

Build a receive-only desktop Python application for receiving, tracking,
decoding, and displaying live Meteor-M N2-3 and N2-4 LRPT weather-satellite
data with a HackRF One.

The final application should combine these functions in one GUI:

- receiver-location configuration;
- satellite tracking and pass planning;
- HackRF control through a GNU Radio receive pipeline;
- live spectrum and waterfall visualization;
- live IQ constellation visualization;
- receiver and demodulator status;
- LRPT demodulation and packet decoding;
- incremental reconstruction and display of weather imagery.

The program must never enable transmission. It is a receive-only station.

## Current implementation status

The project currently provides a functional hardware-free desktop mockup and
the complete configuration/tracking/map foundation. It starts without a
connected HackRF.

### Application shell and persistence

- Python and PySide6 provide the desktop application.
- The root `launcher` starts `src/main.py` and is intended to be double-clicked.
- Root `MeteorM.desktop` is the icon-bearing Linux launcher and delegates to
  `launcher`; the shell launcher remains the portable fallback.
- The program does not use or create a virtual environment.
- All persistent settings and cached runtime data live in the single
  repository-root `settings.yaml` file.
- YAML writes are lock-protected and use atomic file replacement.
- Persisted data includes receiver settings, receiver coordinates, enabled
  satellites, tracking intervals, window size, loaded panels, log verbosity,
  appearance mode, and validated TLE cache entries.
- Green is the standard appearance: a dark base with green-tinted chrome,
  controls, map, and signal renderers. Dark and Normal remain available from
  **Settings -> Appearance**. Theme changes apply immediately to standard
  widgets and every custom renderer and are persisted in YAML.

### Modular GUI

- The GUI uses a fixed six-column/eight-row panel grid.
- A compact custom title strip contains `info/master_icon.png`, the Settings
  menu, a geometrically centered application title, and
  minimize/maximize/close controls. Do not restore a separate menu row beneath
  it.
- The World map and Application log panels are mandatory and cannot be closed.
- Optional panels are imported lazily. Their inactive space remains clickable
  in the assigned grid position; there is no separate panel-loader area.
- Closing an optional panel unloads its widget and restores its clickable
  placeholder. Import or dependency failures remain retryable and are written
  to the mandatory log.
- Satellite tracking and Receiver location share the upper-left grid slot.
  Header selectors switch between them. Loading one unloads the other, so only
  one can consume the space or request services at a time.
- Loaded optional panels are restored from `settings.yaml` on the next launch.
- Panels communicate only through `AppEventBus` signals and immutable/latest
  state in `PanelContext`. Panels must not directly call one another.

The current panel files and responsibilities are:

- `panel_map.py` — mandatory offline world map and orbit overlays;
- `panel_log.py` — mandatory application log, verbosity selection, and text
  export;
- `panel_location.py` — manual coordinates, pasted coordinates/Google Maps URL
  parsing, and synchronization with map-selected coordinates;
- `panel_satellite_tracking.py` — two independent Meteor satellite controls,
  TLE status, manual fetch, information, and SDR tune actions;
- `panel_sdr.py` — simulation/real-HackRF selection, discovery, synchronized
  numeric/slider controls with immediate settings updates, lifecycle controls,
  live sample counts, and backend errors;
- `panel_spectrum.py` — aligned custom spectrum and bounded waterfall renderers
  with one frequency axis, click-to-tune interaction, and active-source status;
- `panel_constellation.py` — custom raw/simulated-IQ point-cloud renderer;
- `panel_picture.py` — custom incremental image renderer, currently fed by
  simulated image data.

### Receiver location

- Latitude/longitude can be entered manually.
- Plain coordinates and common Google Maps coordinate URLs can be pasted.
- Right-clicking elsewhere on the map offers **Set receiver position here**.
- Location changes are routed through the event bus and persisted immediately.

### Satellite tracking

- Both METEOR-M N2-3 (NORAD 57166) and N2-4 (NORAD 59051) are supported.
- Each satellite has an independent activation checkbox, cached TLE record,
  worker, map color, information action, manual fetch action, and SDR tune
  action.
- `TLEProvider` separates orbital-data retrieval/cache policy from propagation.
- `CelestrakTLEProvider` is the current replaceable provider.
- Startup reads only validated cached TLE data and performs no automatic
  network request.
- Online retrieval occurs only after the user presses that satellite's fetch
  button. The provider enforces CelesTrak's two-hour minimum request interval,
  validates catalog IDs, line lengths, prefixes, and checksums, and falls back
  to a valid cache when possible.
- `SatelliteTracker` performs local Skyfield/SGP4 propagation and WGS84
  geodetic conversion with no file or network I/O.
- Each enabled satellite runs in its own worker thread and publishes through a
  bounded latest-value queue. Tracking starts only while the Satellite panel is
  loaded and stops when that panel is unloaded or switched to Receiver.

### World map

- The map is a custom Qt renderer using bundled Natural Earth land geometry;
  it does not require network access or a paid map API.
- It displays the receiver, distinct satellite colors, current-position
  circles, projected tracks, and a lower-left satellite legend.
- Ground tracks contain 901 propagated points at 12-second spacing.
- The displayed window covers 60 minutes of history and 120 minutes of future
  trajectory.
- Historical track opacity falls off steeply toward the oldest point, making
  the direction of travel visible while emphasizing the future path.
- Small crosses and local-time labels mark interpolated satellite positions at
  every wall-clock `XX:00` and `XX:30` in the past and future portions.
- Right-clicking a current satellite circle offers an action to tune the SDR to
  that satellite's configured LRPT center frequency. Right-clicking elsewhere
  retains receiver-position selection.

### Receiver integration, simulation, and visualization

- `SimulationBackend` provides a mock QPSK-like IQ stream and incremental image
  so the interface can be developed without hardware.
- `ReceiverManager` selects simulation or the real HackRF backend from the
  persisted device setting while preserving one orchestrator contract.
- Startup configures the selected backend but intentionally leaves it stopped;
  acquisition begins only after an explicit **Start receiver** request.
- `HackRFController` performs `hackrf_info` discovery and owns settings,
  lifecycle, status, error handling, and IQ processing on a worker thread.
- `GNUradioBridge` lazily builds a receive-only GNU Radio 3.10 `gr-soapy`
  flowgraph inside a dedicated child process: one complex-float Soapy HackRF
  source passes through compiled stream-to-vector and keep-one-in-N blocks to a
  custom bounded latest-IQ sink. About 12 analysis vectors per second cross the
  bounded process queue regardless of the raw sample rate. There is no radio
  sink or transmit API.
- `GNUradioProcessBridge` terminates an unresponsive native receiver child after
  USB removal, guaranteeing that its device handle is released before a fresh
  receiver process is started.
- Sample rate is constrained to full 1 MHz steps from 2 to 20 MS/s. Changing it
  while running rebuilds the flowgraph; other RF settings update the live source.
- A three-second IQ timeout ends a failed hardware run so reconnecting the
  HackRF and pressing **Start receiver** creates a fresh worker and flowgraph.
- Live HackRF IQ feeds the existing `SpectrumProcessor`, spectrum/waterfall,
  and raw-IQ constellation path. Simulation remains the default and requires no
  native SDR dependencies or connected device.
- Receiver snapshots travel through a bounded queue.
- `SpectrumProcessor` prepares NumPy FFT data outside GUI widgets.
- `ConstellationProcessor` creates a bounded, normalized IQ point cloud.
- Spectrum, waterfall, constellation, and image displays are custom application
  renderers; the final application must not depend on GNU Radio GUI sinks.
- Real HackRF acquisition has been verified with a connected HackRF One at
  2, 3, and 10 MS/s: discovery, source construction, bounded complex64 IQ
  delivery, live sample-rate rebuilding, spectrum/constellation snapshots,
  status transitions, and clean shutdown all completed successfully. LRPT
  demodulation, decoding, and real image
  reconstruction are not implemented; picture data is still simulated.

### Logging and tests

- The mandatory log receives application errors, warnings, informational
  messages, and debug output.
- The user can select ERROR, WARNING, INFO, or DEBUG verbosity.
- The visible filtered session log can be saved as `.log` or `.txt`.
- Hardware-free tests live in `src/test` and run with:

  ```bash
  python3 -m pytest -q src/test
  ```

- At the time of this update, the full suite contains 52 passing tests.
- GUI tests use Qt's offscreen platform through `src/test/conftest.py`.

## Required directory and code structure

Keep the repository deliberately flat and understandable:

```text
MeteorM/
├── AGENTS.md          Current contributor/agent brief
├── README.md          Fresh-system setup and user quick start
├── launcher           Double-clickable application launcher
├── MeteorM.desktop    Icon-bearing Linux desktop launcher
├── settings.yaml      The only application settings/cache file
├── .gitignore
├── info/              Dependencies, licenses, and technical documentation
│   ├── architecture.md
│   ├── dependencies.txt
│   ├── hackrf-integration.md
│   ├── licenses.md
│   ├── master_icon.png
│   ├── natural-earth-license.txt
│   └── satellite-tracking.md
└── src/
    ├── main.py
    ├── gui.py
    ├── panel_map.py
    ├── panel_log.py
    ├── panel_location.py
    ├── panel_satellite_tracking.py
    ├── panel_sdr.py
    ├── panel_spectrum.py
    ├── panel_constellation.py
    ├── panel_picture.py
    ├── ne_110m_land.geojson
    └── test/
        └── test_*.py
```

Structural requirements:

- Production Python code belongs directly in `src`; do not introduce
  production package subdirectories.
- `src` should contain only `main.py`, `gui.py`, and exactly one Python file for
  each visible panel.
- Tests are the only Python code allowed in the `src/test` subdirectory.
- Panel-specific helper classes belong inside that panel's file rather than in
  additional helper modules.
- `main.py` is the composition root and contains non-visual services,
  processing classes, settings ownership, worker lifecycle, and orchestration.
- `gui.py` contains shared data contracts, application state, the event bus,
  panel registry/slots, theme shell, and `MainGUI` composition.
- Each `panel_*.py` exports a `Panel(QWidget)`-compatible class constructed with
  `PanelContext`.
- Keep all user settings and cached data in root `settings.yaml`; do not create
  hidden application settings/cache directory trees.
- Dependency lists, licenses, provider assumptions, and external protocol notes
  belong in `info`.

## Architecture and implementation rules

- Use Python, PySide6, NumPy, PyYAML, and Skyfield unless a different technology
  is explicitly approved.
- Prefer small classes with clear responsibilities over excessive abstraction.
- Keep hardware control, file/network I/O, orbit propagation, demodulation, and
  high-rate processing out of GUI widgets and out of the GUI thread.
- Use bounded queues and latest-value delivery for streaming data to prevent
  uncontrolled memory growth.
- Avoid unnecessary IQ-array copies.
- Keep hardware, TLE, map, decoder, and data-source boundaries replaceable and
  mockable.
- A panel may issue requests and consume snapshots through `AppEventBus`; it
  must not own application services or reference another panel directly.
- Keep TLE downloading, cache validation, orbit propagation, and map rendering
  separate.
- Preserve development and GUI testing without connected hardware.
- Do not add any transmit path or expose HackRF transmission controls.
- Prefer freely usable offline/open map data. Do not introduce a paid Google
  Maps dependency without explicit approval.
- Do not silently install packages or modify system configuration. This project
  does not use a virtual environment; when installation is explicitly required,
  use normal system/user package installation as appropriate and report it.
- Do not commit changes unless the user explicitly requests a commit.
- Preserve unrelated user changes in a dirty working tree.
- Document external protocols, data formats, licensing, and uncertain technical
  assumptions. Do not guess unresolved LRPT details.
- Before a major subsystem is implemented, describe its interface and external
  dependencies.

## GNU Radio and HackRF integration constraints

GNU Radio is the intended owner of HackRF sample acquisition and the real-time
receiver pipeline.

- Treat a generated GNU Radio Python flowgraph as a backend component.
- Never mix application GUI code into generated GNU Radio code.
- Expose data and control through a documented bridge such as callbacks,
  bounded queues, or ZeroMQ.
- Keep the bridge replaceable so simulation remains available.
- Continue using the application's custom spectrum, waterfall, constellation,
  map, and image renderers instead of GNU Radio GUI sinks.
- The bridge and flowgraph must remain receive-only.

## Open TODOs

The main unfinished items are:

1. Extend hardware verification to physical disconnect/reconnect behavior, USB
   contention, long-duration operation, and effective sample-rate/bandwidth
   reporting from the driver.
2. Research and document the exact Meteor-M N2-3/N2-4 LRPT modulation,
   symbol-rate, synchronization, interleaving, error-correction, packet, and
   image formats before implementing them.
3. Implement `LRPTDemodulator` outside the GUI thread, including carrier/symbol
   synchronization and useful receiver/demodulator status snapshots.
4. Implement `LRPTDecoder` for frame synchronization, error correction, packet
   parsing, and channel data. Do not invent uncertain protocol values.
5. Implement `ImageAssembler` for incremental real LRPT image reconstruction
   and replace the simulated picture source.
6. Add receiver-relative visible-pass prediction: azimuth, elevation, rise,
   culmination, set time, maximum elevation, and practical visibility cues.
7. Verify current satellite transmission status/frequency handling and consider
   a documented way to update frequency information without aggressive online
   polling.
8. Add hardware-in-the-loop tests, disconnect/reconnect handling, malformed
    stream handling, and end-to-end recorded-IQ fixtures.
9. Evaluate an optional OpenStreetMap/Leaflet backend only if it materially
    improves the map while preserving a free/offline-capable fallback.
10. Improve packaging and fresh-system compatibility after the real native GNU
    Radio/HackRF dependency set is known.

## Development sequence

Completed foundation:

1. GUI layout and simulated data.
2. Configuration and receiver-location handling.
3. TLE caching, local propagation, satellite controls, and map display.
4. Custom spectrum, waterfall, constellation, picture, logging, themes, and
   modular panel lifecycle.

Continue incrementally with:

5. HackRF/GNU Radio connection and simulation/real-backend selection (complete
   for the initial receive-only hardware milestone).
6. Live sample transport and receiver status (complete for the initial
   receive-only hardware milestone).
7. LRPT demodulation and decoding.
8. Live real-image reconstruction.
9. Pass prediction, integration testing, and robust error handling.

Keep each step runnable and testable without hardware whenever practical.
