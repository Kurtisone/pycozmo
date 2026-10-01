# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.

import os
import glob
import re
import sys
sys.path.insert(0, os.path.abspath('../..'))    # noqa

# noinspection PyPep8
import pycozmo  # noqa


# -- Project information -----------------------------------------------------

project = 'PyCozmo'
# noinspection PyShadowingBuiltins
copyright = '2019-2020, Kaloyan Tenchov'
author = 'Kaloyan Tenchov'

# The full version, including alpha/beta/rc tags
release = pycozmo.__version__


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.coverage',
    'sphinx.ext.intersphinx',
    'recommonmark',
]

# Add any paths that contain templates here, relative to this directory.
templates_path: list = []

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ['**tests**']

master_doc = 'index'


# -- Options for HTML output -------------------------------------------------

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path: list = []


autosummary_generate = True
autodoc_default_flags = [
    'members',
    'undoc-members',
    'show-inheritance',
    'inherited-members',
]
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'PIL': ('https://pillow.readthedocs.io/en/latest/', None),
}


# Create external/ subdirectory and copy markdown files from docs/, and the README as the overview, so that it cannot
# drift from the one on the repository's front page. A link to the README, or to a file the documentation does not
# carry, points to where it is instead.
REPOSITORY = 'https://github.com/Kurtisone/pycozmo/blob/master/'
cur_dir = os.path.dirname(__file__)
external_dir = os.path.join(cur_dir, 'external')
if not os.path.exists(external_dir):
    os.mkdir(external_dir)


def write_external(text, name):
    with open(os.path.join(external_dir, name), 'w') as f:
        f.write(text)


doc_spec = os.path.join(cur_dir, '..', '*.md')
for src in glob.glob(doc_spec):
    with open(src) as f:
        text = f.read()
    text = text.replace('](../README.md', '](overview.md')
    text = re.sub(r'\]\(\.\./((?:tools|examples)/[^)\s]+|CHANGES\.md|CONTRIBUTING\.md)\)',
                  r'](' + REPOSITORY + r'\1)', text)
    write_external(text, os.path.basename(src))
with open(os.path.join(cur_dir, '..', '..', 'README.md')) as f:
    readme = f.read()
readme = readme.replace('[docs/](docs/)', '`docs/`').replace('](docs/', '](')
readme = re.sub(r'\]\(((?:tools|examples)/[^)\s]+|CHANGES\.md|CONTRIBUTING\.md|requirements\.txt)\)',
                r'](' + REPOSITORY + r'\1)', readme)
write_external(readme, 'overview.md')
