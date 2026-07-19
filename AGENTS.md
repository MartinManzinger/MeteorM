# AGENTS.md

## Project goal

Build a desktop Python application for receiving and displaying live Meteor-M N2-3/N2-4 LRPT weather-satellite data using a HackRF One.

The application must combine SDR control, satellite tracking, signal visualization, demodulation, decoding, and live image display in one GUI.

## Main requirements

The GUI must provide:

- Receiver-location selection by map or manual latitude/longitude.
- Persistent settings stored in a human-readable YAML file.
- World map showing:
  - receiver location;
  - predicted satellite ground track;
  - current satellite position;
  - visible-pass information where practical.
- HackRF control panel:
  - center frequency;
  - sample rate;
  - filter bandwidth;
  - LNA/VGA gains;
  - amplifier state;
  - device selection.
- Live spectrum display.
- Live IQ constellation display.
- Receiver and demodulator status.
- Live reconstruction of decoded LRPT image data.

Prefer OpenStreetMap/Leaflet or another freely usable map backend. Do not require a paid Google Maps API unless explicitly approved.

Satellite positions should be calculated from TLE orbital data through a replaceable provider interface. Keep downloading TLE data separate from orbit propagation and GUI rendering.

## Architecture

Use separate classes with clear responsibilities. Suggested components:

- `AppConfig` — YAML loading, validation and saving.
- `ReceiverLocation` — receiver coordinates and location selection.
- `TLEProvider` — retrieves and caches orbital elements.
- `SatelliteTracker` — orbit propagation, position and ground track.
- `HackRFController` — SDR configuration and lifecycle.
- `GNUradioBridge` — starts and interfaces with the GNU Radio receiver.
- `SpectrumProcessor` — FFT preparation and spectrum data.
- `ConstellationProcessor` — IQ point-cloud preparation.
- `LRPTDemodulator` — signal demodulation and decoder state.
- `LRPTDecoder` — packet and image decoding.
- `ImageAssembler` — incremental image reconstruction.
- `MainWindow` — GUI composition only.

Do not put signal processing, hardware control or orbital calculations directly into GUI widgets.

## GNU Radio integration

GNU Radio should handle HackRF sample acquisition and the required real-time receiver pipeline.

The generated GNU Radio Python file is treated as a backend component. Do not manually mix GUI code into generated GNU Radio code.

Expose data to the application through a defined bridge, such as callbacks, queues, ZeroMQ or another documented local interface.

The custom application must implement its own spectrum, constellation, map and image displays. Do not depend on GNU Radio GUI sinks for the final interface.

## Implementation rules

- Use Python and PySide6 unless a different technology is explicitly approved.
- Use NumPy for sample and plotting data.
- Keep high-rate processing outside the GUI thread.
- Use bounded queues to prevent uncontrolled memory growth.
- Avoid unnecessary copies of IQ data.
- Keep hardware, decoder and map providers replaceable and mockable.
- Support development and GUI testing without connected hardware.
- Never enable transmission; this application is receive-only.
- Do not silently install packages or modify system configuration.
- Do not commit changes unless explicitly requested.
- Prefer small, understandable modules over excessive abstraction.
- Document external protocols, data formats and important assumptions.

## Development approach

Work incrementally:

1. GUI layout with simulated data.
2. Configuration and receiver-location handling.
3. Satellite tracking and map display.
4. HackRF/GNU Radio connection.
5. Spectrum and constellation data.
6. LRPT demodulation and decoding.
7. Live image reconstruction.
8. Integration tests and error handling.

Before implementing a major subsystem, describe its interface and dependencies. Report missing specifications or uncertain protocol details instead of guessing.