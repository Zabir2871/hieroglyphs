"""
KOReader loads StarDict dictionaries by scanning its dict/ folder for
subfolders each containing an .ifo/.idx/.dict(.dz)/.syn set - so "export
for KOReader" is really just: produce a normal StarDict folder (see
stardict_exporter.py) and hand it back as a single zip the user can
extract straight into koreader/data/dict/ on their device.
"""
from __future__ import annotations
import os
import shutil


def zip_stardict_folder(stardict_dir: str, out_zip_path_no_ext: str) -> str:
    """Zips stardict_dir's *contents* (not the dir itself nested twice) and
    returns the resulting .zip path."""
    archive = shutil.make_archive(out_zip_path_no_ext, "zip", root_dir=os.path.dirname(stardict_dir),
                                   base_dir=os.path.basename(stardict_dir))
    return archive
