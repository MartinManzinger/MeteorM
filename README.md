# Meteor-M LRPT Station

Desktop Python application for tracking Meteor-M N2-3/N2-4 weather satellites
and, incrementally, receiving and displaying live LRPT data with a HackRF One.

The current build provides selectable simulated or receive-only HackRF One/GNU
Radio acquisition, live CelesTrak TLE retrieval, local SGP4 propagation, an
offline Natural Earth world map, live spectrum/waterfall and raw-IQ
constellation plots, a simulated image reconstruction, persistent settings,
and a modular fixed-slot panel interface.

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

3. Ensure the launchers are executable, then start the application:

   ```bash
   chmod +x launcher MeteorM.desktop
   ./launcher
   ```

For an icon-bearing graphical launcher, double-click `MeteorM.desktop` and
choose **Allow Launching** if the desktop asks whether to trust it. The plain
`launcher` remains the portable terminal/file-manager fallback.

No virtual environment, package installation inside the repository, paid map
API, or connected HackRF is required for the mock receiver.

To enable a connected HackRF One on Ubuntu, also install the native receive
stack before launching:

```bash
sudo apt install gnuradio soapysdr-module-hackrf hackrf
```

The system's HackRF udev permissions must allow the current user to access the
device. The application never changes packages, permissions, or system
configuration itself.

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

Green mode is the standard theme. Choose **Settings → Appearance** to switch
between Green, Dark, and Normal modes. The choice is applied immediately to
standard controls and all custom map, spectrum, waterfall, constellation, and
picture renderers, then saved in `settings.yaml`.

The supplied application icon and Settings menu share the compact custom title
strip with the window controls. The program name stays centered across the full
window, avoiding a second menu row. The Qt desktop identity matches
`MeteorM.desktop`, allowing Linux taskbars to resolve the supplied icon. Menu
popup colors follow the selected appearance theme.

The optional panels are:

- **Receiver location** — manual coordinates, pasted coordinates/Google Maps
  links, and synchronization with the map's right-click receiver action;
- **Satellite tracking** — independent M2-3/M2-4 and NOAA-15/18/19 activation,
  cached TLE loading, local actual/projected orbit calculations, 12-second
  ground-track resolution, fading historical direction cues, half-hour
  local-time map markers, colored overlays, per-satellite information, manual
  online refresh, Meteor LRPT tuning, and NOAA legacy APT tuning. NOAA entries
  do not claim APT demodulation or decoding support. Satellite details use a
  one-at-a-time accordion to keep all five controls manageable;
- **SDR control** — simulation/HackRF selection, asynchronous HackRF discovery,
  synchronized numeric fields/sliders, immediate RF-setting updates,
  receive-only lifecycle, sample counters, and backend errors;
- **Live spectrum** — spectral power plot, bounded waterfall history, and the
  active sample-source status; spectrum and waterfall share one aligned MHz
  axis, and left-clicking either plot retunes the receiver to that frequency;
- **IQ constellation** — prepared raw/simulated complex points (not yet
  symbol-synchronized LRPT data);
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
map passively receives its snapshots. The selected receiver remains independent
of those panels. GNU Radio and the Soapy HackRF driver are imported only when
the real device is selected, so the application still runs when those optional
native dependencies or the hardware are absent.

## Using a HackRF One

1. Connect the HackRF One and load **SDR control**.
2. Press **Discover**. Select a serial-specific entry when more than one device
   is connected, or use **HackRF One (auto)** for the first available unit.
3. Set center frequency, sample rate, bandwidth, and RX gains with either the
   numeric fields or sliders. Sample rate is limited to full 1 MHz steps from
   2 to 20 MS/s. Changes take effect and persist immediately; a running
   receiver rebuilds its flowgraph when sample rate changes.
4. Press **Start receiver**. Receivers are intentionally stopped when the
   program opens. The SDR and spectrum panels show discovery,
   startup, live sample counts, bounded-buffer drops, or the driver error.

The real flowgraph runs in a disposable receiver process and uses compiled
vector/decimation blocks before its bounded latest-IQ sink; it contains no radio
sink and no transmit API/control. Settings can be retuned while running.
Spectrum power and signal level are relative dBFS, not calibrated dBm. If the
HackRF is unplugged, the run changes to an error state and any stuck native
receiver process is ended; reconnect it and press **Start receiver** to create a
fresh flowgraph.
The receiver has been verified on connected hardware at 2, 3, and 10 MS/s. See
`info/hackrf-integration.md` for the bridge contract, test scope, and diagnostics.

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
├── MeteorM.desktop   Icon-bearing Linux desktop launcher
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

Actual LRPT demodulation/decoding and real image reconstruction,
receiver-relative pass prediction, automatic discovery of frequency changes,
hardware-in-the-loop reconnect testing, and a Leaflet/OpenStreetMap view remain
future milestones. The application is receive-only and contains no transmit
path.
