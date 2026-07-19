# Dependency and data licenses

The application does not vendor its Python dependencies. They are installed by
the operating system, so their complete license texts are supplied by those
packages. This inventory records the relevant upstream licenses.

| Component | Purpose | Upstream license |
| --- | --- | --- |
| Python | Runtime | Python Software Foundation License |
| PySide6 / Qt for Python | GUI bindings | LGPL-3.0, GPL-3.0, or Qt commercial terms |
| Qt | GUI framework | LGPL-3.0, GPL-3.0, or Qt commercial terms depending on distribution/module |
| NumPy | Numerical arrays and FFT | BSD-3-Clause |
| PyYAML | YAML settings | MIT |
| Skyfield | Orbit calculations | MIT |
| sgp4 | SGP4 propagation | MIT |
| jplephem | Skyfield dependency | MIT |
| GNU Radio / gr-soapy | Optional real receiver runtime | GPL-3.0-or-later (with separately licensed bundled portions) |
| SoapyHackRF | Optional SoapySDR HackRF plugin | MIT |
| libhackrf / HackRF tools | Optional hardware library and discovery utility | GPL-2.0-or-later |
| pytest | Tests | MIT |
| Natural Earth 1:110m land | Offline coastline data | Public domain |

Upstream license references:

- [Python license](https://docs.python.org/3/license.html)
- [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html)
- [NumPy license](https://numpy.org/doc/stable/license.html)
- [PyYAML repository](https://github.com/yaml/pyyaml)
- [Skyfield repository](https://github.com/skyfielders/python-skyfield)
- [python-sgp4 repository](https://github.com/brandon-rhodes/python-sgp4)
- [jplephem repository](https://github.com/brandon-rhodes/python-jplephem)
- [GNU Radio repository](https://github.com/gnuradio/gnuradio)
- [SoapyHackRF repository](https://github.com/pothosware/SoapyHackRF)
- [HackRF repository](https://github.com/greatscottgadgets/hackrf)
- [pytest license](https://docs.pytest.org/en/stable/license.html)
- [Natural Earth terms](https://www.naturalearthdata.com/about/terms-of-use/)

See `natural-earth-license.txt` beside this file for the bundled map data
notice.
