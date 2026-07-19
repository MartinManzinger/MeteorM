# Modular application architecture

## Runtime ownership

`main.py` is the composition root. It creates the settings store, mutable latest
state, event bus, GUI shell, selectable simulation/HackRF receiver, satellite
tracking services, and timers. `AppOrchestrator` is the only object that
coordinates those services. It does not render widgets.

All non-visual service classes—configuration, simulation, signal preparation,
TLE retrieval, SGP4 propagation, worker queues, and orchestration—are grouped
as classes in `main.py` to keep the source directory intentionally small.

`gui.py` defines the immutable data contracts, latest-state container, event
bus, panel metadata, and main window. It creates a fixed six-column/eight-row
grid matching `panel_layout.ods`. Its `PANEL_SPECS` registry maps a stable key
to a module, display metadata, grid coordinates, and service demand. Optional
modules are imported with `importlib` only after the user clicks their inactive
slot. Import and construction failures are logged and leave that same slot
available for another load attempt.

Satellite tracking and Receiver location intentionally map to one shared
upper-left slot. Header selectors lazily switch between them; `MainGUI` unloads
the current module before loading the other, so their service demand and saved
loaded-panel state are mutually exclusive.

The production source directory contains only `main.py`, `gui.py`, and one
`panel_*.py` file per visible panel. Renderer helpers and panel-specific logic
are classes inside the owning panel file rather than separate modules.

## Panel contract

Every optional `panel_*.py` module exports:

```python
class Panel(QWidget):
    def __init__(self, context: PanelContext) -> None:
        ...
```

`PanelContext` contains only:

- `AppEventBus`, whose Qt signals carry user requests and state updates;
- `ApplicationState`, the latest values used to initialize a newly loaded
  panel before the next update arrives.

A panel must not directly access another panel, `SettingsStore`, a receiver
backend, or `SatelliteTrackingService`. This keeps location, satellite
tracking, SDR controls, spectrum/waterfall, constellation, and picture display
independently loadable. Qt automatically disconnects a panel's event-bus
receivers when it is deleted.

The mandatory map and log follow the same widget contract but are constructed
directly by `gui.py`. Neither slot has a close action. The log therefore always
provides a visible destination for errors encountered while loading optional
modules.

## Service demand

The selected receiver runs independently and publishes bounded snapshots.
`ReceiverManager` keeps simulation available while `HackRFController` and its
receive-only `GNUradioProcessBridge` provide the optional real path. The
controller remains on an application worker thread while all native GNU
Radio/Soapy objects live in a disposable child process, communicating through
bounded control and latest-IQ queues. Panels may be added or removed without
changing either pipeline. Satellite
tracking is demand-driven: loading the Satellite tracking panel starts one
worker per checked satellite, unchecking one stops only its worker, and
unloading the panel stops both. Each worker reads cached TLE data without
network access at startup. Only a satellite's explicit fetch request can
request CelesTrak; successful validated data is stored in `settings.yaml`.
TLE retrieval, SGP4 propagation, device discovery, GNU Radio control, and
receiver processing remain outside the GUI thread. The mandatory map only
consumes orbit and location events.

The real receiver boundary, native dependencies, gain mapping, failure model,
and receive-only flowgraph are specified in `info/hackrf-integration.md`.

## Adding a panel

1. Add one `src/panel_<name>.py` file that implements the contract above.
2. Add its `PanelSpec` to `PANEL_SPECS` in `src/gui.py`.
3. Use event-bus signals for requests or updates. Extend `AppEventBus` and
   `ApplicationState` when the panel introduces new shared state.
4. Add a construction/lifecycle test under `src/test`.

Set `starts_tracking=True` only when the panel consumes orbital snapshots. A
future hardware or decoding service should use an equivalent explicit demand
flag instead of being accessed from panel code.
