# Third-party notices

ECSim is distributed under its own proprietary EULA (see LICENSE). It uses the following
third-party components, each under its own license. Those licenses govern the components.

| Component | License | Notes |
|---|---|---|
| Qt for Python (PySide6) | LGPL-3.0 | Used as a dynamically linked library. You may replace it with a compatible version of your choice (LGPL §4); the source is available at https://code.qt.io/ |
| Qt framework | LGPL-3.0 | Bundled with PySide6 |
| PyYAML | MIT | |
| Python | PSF License | Bundled in packaged builds |
| GCC / Clang (user-installed) | GPL-3.0 with runtime exception / Apache-2.0 | Not distributed with ECSim; detected on the user's system |
| MATLAB Engine for Python (user-installed, optional) | MathWorks license | Not distributed; requires the user's own MATLAB license |

Full license texts are included in packaged builds under `licenses/`.
