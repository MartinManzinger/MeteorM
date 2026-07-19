# HackRF receive integration

## Scope and safety boundary

The real receiver is a receive-only GNU Radio flowgraph:

```text
Soapy HackRF Source (complex float32) -> stream-to-vector
                                      -> compiled keep-one-in-N decimator
                                      -> bounded latest-IQ sink
                                      -> SpectrumProcessor / ConstellationProcessor
                                      -> immutable ReceiverSnapshot
```

There is deliberately no GNU Radio sink connected to radio hardware, no HackRF
transmit API call, and no transmit control in the event bus or GUI. The existing
custom Qt spectrum, waterfall, and constellation renderers remain the only GUI
sinks.

## Replaceable interfaces

`GNUradioBridge` owns the generated-flowgraph-style GNU Radio objects inside a
dedicated child process. It lazily imports `gnuradio.gr` and `gnuradio.soapy`,
constructs one `driver=hackrf` source, applies the receiver settings, and
connects it to compiled stream-to-vector and keep-one-in-N blocks before a small
custom GNU Radio sink. Only about 12
4,096-sample analysis vectors per second cross from GNU Radio into Python,
regardless of the radio sample rate. The sink retains only the latest vector;
GNU Radio never calls Qt and cannot grow application memory without bound.

`GNUradioProcessBridge` owns that process and the bounded inter-process sample
and control queues. If native GNU Radio shutdown hangs after USB removal, the
parent terminates only the receiver child. The operating system then releases
the USB handle, allowing a newly connected device to start without restarting
the desktop application.

`HackRFController` owns device discovery, bridge lifecycle, settings revisions,
and conversion of raw IQ blocks into application snapshots. Its worker thread is
the only caller of the bridge control methods. Failures become immutable
`ReceiverStatus` updates instead of escaping into GUI widgets.

`ReceiverManager` selects either `SimulationBackend` or `HackRFController` from
the persisted device name while presenting the same start/stop/settings/latest
snapshot interface to `AppOrchestrator`. Simulation therefore remains available
when GNU Radio, the Soapy driver, USB permissions, or hardware are absent.

Panels remain isolated from these services. They request device discovery and
receiver actions on `AppEventBus`, then consume `ReceiverStatus`, device-list,
settings, and snapshot signals through `PanelContext`.

## Hardware mapping and assumptions

The GNU Radio 3.10 Soapy HackRF source maps application controls as follows:

| Application setting | Soapy HackRF control | Accepted GUI range |
| --- | --- | --- |
| center frequency | `set_frequency(0, hz)` | 1 MHz to 6 GHz |
| sample rate | `set_sample_rate(0, hz)` | 2 to 20 MS/s, full 1 MHz steps |
| filter bandwidth | `set_bandwidth(0, hz)` | 1 to 28 MHz |
| RF amplifier | `set_gain(0, "AMP", enabled)` | off/on |
| LNA/IF gain | `set_gain(0, "LNA", db)` | 0 to 40 dB, 8 dB steps |
| VGA/baseband gain | `set_gain(0, "VGA", db)` | 0 to 62 dB, 2 dB steps |

HackRF documentation describes RX AMP as a binary roughly 11 dB stage, LNA/IF
as 0--40 dB in 8 dB steps, and VGA/baseband as 0--62 dB in 2 dB steps. The AMP
defaults off because excess gain can overload the receiver. The application
does not infer calibration: displayed power is relative dBFS derived from the
normalized complex samples, not calibrated dBm.

The application exposes the HackRF's full 20 MS/s maximum in whole-MHz steps.
The compiled decimator increases its keep-one-in-N ratio with sample rate, so
the Python analysis and GUI update load remains nearly constant even though USB
and GNU Radio must still handle the full selected rate. Changing sample rate
while running rebuilds the flowgraph so vector sizes and decimation remain
consistent; center frequency, bandwidth, and gain changes are applied directly
to the live source.

If a running flowgraph delivers no IQ for three seconds, the controller reports
an error and ends that receiver run. After reconnecting the HackRF, pressing
**Start receiver** creates a fresh worker and flowgraph instead of attempting to
reuse the failed native objects.

References:

- <https://wiki.gnuradio.org/index.php/Soapy>
- <https://wiki.gnuradio.org/index.php/Soapy_HackRF_Source>
- <https://hackrf.readthedocs.io/en/latest/setting_gain.html>

## Runtime dependencies and diagnostics

On Ubuntu the real backend requires GNU Radio 3.10 or newer with `gr-soapy`, the
Soapy HackRF module, libhackrf, and the `hackrf_info` utility. `hackrf_info` is
run only in a short-lived subprocess on a discovery worker thread so a driver
failure cannot terminate the GUI process. A connected device must also be
accessible through the system's HackRF udev permissions.

Useful diagnostics are:

```bash
gnuradio-config-info --enabled-components
hackrf_info
SoapySDRUtil --probe="driver=hackrf"
```

The application does not install these packages or alter udev/system settings.

## Current hand-off point

This bridge provides raw, bounded live IQ for spectrum/waterfall display and a
raw-IQ constellation. It does not claim symbol timing, carrier synchronization,
LRPT packet decoding, or image reconstruction. Those later stages must consume
the same bounded sample boundary or a documented successor without adding GUI
thread processing.

Hardware verification used a connected HackRF One at 2, 3, and 10 MS/s and
137.9 MHz. Device discovery, GNU Radio source startup, 4,096-sample complex64
analysis delivery, a 2,048-bin spectrum, live sample-rate flowgraph rebuilding,
controller status/snapshot delivery, and clean flowgraph shutdown all succeeded.
The stalled-stream error and subsequent fresh restart are covered with test
doubles; a physical unplug/replug cycle remains hardware-in-the-loop work.
