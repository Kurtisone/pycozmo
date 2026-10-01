Contributing to PyCozmo
=======================

Any contribution to PyCozmo is welcome and appreciated.


Bug Reports and Feature Requests
--------------------------------

- Bug reports and feature requests should be made using the
  [GitHub issues](https://github.com/Kurtisone/pycozmo/issues) of this fork. Upstream's tracker,
  [zayfod/pycozmo](https://github.com/zayfod/pycozmo/issues), is for upstream.


Pull Requests
-------------

- Pull requests can be made on [GitHub](https://github.com/Kurtisone/pycozmo). Development happens on a private
  Forgejo instance that mirrors to GitHub, so a pull request cannot be merged there: it is read, and applied on the
  Forgejo side. See Support in the [README](README.md).
- The checks - `flake8 .`, `mypy .` and `pytest pycozmo/` - should all be passing: see Checks in the README. None of
  them needs a robot.
- Code should adhere to the [PEP 8 style guide](https://peps.python.org/pep-0008/), with lines of up to 120 characters.
- Using docstrings is encouraged.
- Adding tests is encouraged.
