# Meteor-M LRPT Station

Desktop Python application for tracking Meteor-M N2-3/N2-4 weather satellites
and, incrementally, receiving and displaying live LRPT data with a HackRF One.

The current build provides a hardware-free simulated receiver, live CelesTrak
TLE retrieval, local SGP4 propagation, an offline Natural Earth world map,
spectrum and constellation plots, a simulated image reconstruction, persistent
settings, and a modular fixed-slot panel interface.

## Quick start on a fresh Ubuntu system

1. Install Git and the application dependencies:

   ```bash
   sudo apt update
   sudo apt install git python3 python3-numpy python3-yaml \
     python3-pyside6.qtwidgets python3-skyfield python3-pytest
   ```

2. Clone and enter the repository:

   ```bash
   git clone <repository-url> MeteorM
   cd MeteorM
   ```

3. Ensure the launcher is executable, then start the application:

   ```bash
   chmod +x launcher
   ./launcher
   ```

After the executable bit is set, you can also double-click `launcher` in a file
manager and choose **Run** if the desktop asks how to open executable text.

No virtual environment, package installation inside the repository, paid map
API, or connected HackRF is required for the mock receiver.

## Using the modular interface

The application uses the fixed layout from `panel_layout.ods`. The map and log
are mandatory and cannot be closed. An unloaded optional panel remains as a
small inactive slot at its assigned position; click that slot to import and
load it. Use the × in a loaded optional panel's header to unload it and restore
the clickable inactive slot. There is no separate panel-loader area. The
selected optional panels are restored on the next launch.

Satellite tracking and Receiver location share the upper-left panel space.
Use the **Satellite** and **Receiver** selectors in that slot's header to switch
between them. Switching unloads the previous panel, so the two panels are never
loaded simultaneously.

Choose **Settings → Appearance → Dark mode/Normal mode** to switch the GUI
theme. The choice is applied immediately to standard controls and all custom
map, spectrum, waterfall, constellation, and picture renderers, then saved in
`settings.yaml`.

The optional panels are:

- **Receiver location** — manual coordinates, pasted coordinates/Google Maps
  links, and synchronization with the map's right-click receiver action;
- **Satellite tracking** — independent M2-3/M2-4 activation, cached TLE loading,
  local actual/projected orbit calculations, 12-second ground-track resolution,
  fading historical direction cues, half-hour local-time map markers, colored
  overlays, per-satellite information and SDR tuning from either the panel or a
  satellite's map context menu, and manual online refresh;
- **SDR control** — current HackRF One-style device and RF settings;
- **Live spectrum** — spectral power plot and bounded waterfall history;
- **IQ constellation** — prepared complex symbol points;
- **Decoded picture** — incrementally reconstructed LRPT image data.

The application log is always loaded and cannot be closed. A panel that cannot
be imported—because its file, expected `Panel` class, or a dependency is
missing—remains unloaded and reports the cause in this log. Its verbosity can
be selected in the log panel, and the visible session log can be saved as text.

Panels exchange typed requests and immutable snapshots through a shared event
bus. They do not own or call one another. `main.py` owns settings, processing,
tracking, backend lifecycle, and orchestration. `gui.py` owns shared panel
contracts, latest state, the panel registry, and fixed panel slots. Satellite
tracking runs only while the Satellite tracking panel is loaded; the mandatory
map passively receives its snapshots. The simulated receiver remains
independent of those panels.

## Test

From the repository root:

```bash
python3 -m pytest -q src/test
```

## Settings and cache

All persistent data lives in the root `settings.yaml`:

- receiver location and HackRF settings;
- enabled satellites and tracking intervals;
- interface size, loaded panels, appearance mode, and log verbosity;
- validated cached TLE records.

The file is human-readable and can be edited while the application is closed.
The program does not create an application-specific settings or cache directory
elsewhere on the system.

Loading Satellite tracking starts one worker for each checked satellite. Each
worker reads validated cached TLE data without network I/O, then calculates
positions locally once per second. Only that satellite's manual fetch button
contacts CelesTrak. Valid responses are stored separately in `settings.yaml`,
and the provider enforces a minimum two-hour request interval.

## Project layout

```text
MeteorM/
├── launcher          Double-click/run entry point
├── settings.yaml     All settings and cached runtime data
├── README.md         Setup and usage guide
├── info/             Dependencies, licenses, and technical notes
└── src/              All application Python code and required map data
    ├── main.py       Services and application orchestration
    ├── gui.py        Shared contracts and dynamic GUI shell
    ├── panel_*.py    Exactly one file per GUI panel
    └── test/         Automated tests, kept outside production modules
```

Implementation details and orbital-data assumptions are documented in
`info/satellite-tracking.md`; the panel contract and runtime ownership are in
`info/architecture.md`. Dependency details are in
`info/dependencies.txt`, with licensing information in `info/licenses.md`.

## Current limitations

GNU Radio/HackRF acquisition, actual LRPT demodulation and decoding, pass
prediction relative to the receiver, automatic discovery of frequency changes,
and a Leaflet/OpenStreetMap view remain future milestones. The application is
receive-only and contains no transmit path.
