"""

PyCozmo setup script.

"""

import os
import re
import setuptools


def get_package_variable(key):
    fspec = os.path.join("pycozmo", "__init__.py")
    with open(fspec) as f:
        for line in f:
            m = re.match(r"(\S+)\s*=\s*[\"']?(.+?)[\"']?\s*$", line)
            if m and key == m.group(1):
                return m.group(2)
    return None


def get_readme():
    with open("README.md") as f:
        readme = f.read()
    return readme


setuptools.setup(
    name="pycozmo",
    packages=setuptools.find_packages(),
    # PEP 561: the package is fully annotated, so let type checkers use it.
    package_data={"pycozmo": ["py.typed"]},
    zip_safe=False,
    version=get_package_variable("__version__"),
    license="MIT",
    license_files=["LICENSE.md"],
    description="A pure-Python communication library, alternative SDK, and application for the Cozmo robot.",
    long_description=get_readme(),
    long_description_content_type="text/markdown",
    author="Kaloyan Tenchov",
    author_email="zayfod@gmail.com",
    url="https://github.com/zayfod/pycozmo/",
    python_requires=">=3.11",
    install_requires=[
        "dpkt>=1.9.8,<2",
        "numpy>=1.26,<3",
        "Pillow>=10.0,<13",
        "flatbuffers>=24.3.25,<26",
    ],
    keywords=["ddl", "anki", "cozmo", "robot", "robotics"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Intended Audience :: Developers",
        "Intended Audience :: Education",
    ],
    scripts=[
        "tools/pycozmo_dump.py",
        "tools/pycozmo_replay.py",
        "tools/pycozmo_update.py",
        "tools/pycozmo_resources.py",
        "tools/pycozmo_app.py",
    ],
)
