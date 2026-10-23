"""
robot_scripts/file_dialog.py — Native Windows "Open" file-picker helper for
dms_upload.robot.

Selenium cannot interact with OS-level file dialogs, so the batch-upload step
in dms_upload.robot shells out to this script (via `Run Process`) to drive
the dialog with pywinauto: type the folder path, select all files in it,
click Open.

The folder path is passed in as a command-line argument by the robot
(DMS_PENDING_UPLOAD_FOLDER, i.e. config.DMS_STAGING_FOLDER) so it never
drifts out of sync with the app's actual staging folder. Falls back to the
app's default staging path only if called without an argument.
"""

import sys
import os
import time
import pywinauto

# Make the project root importable regardless of cwd (this script is run as
# a standalone subprocess by dms_upload.robot via `Run Process`), so the
# fallback below can reuse config.py's own IS_PRODUCTION-aware path instead
# of a second hardcoded literal that could drift out of sync with it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config.config import config
    DEFAULT_FOLDER = config.DMS_STAGING_FOLDER
except Exception:
    DEFAULT_FOLDER = r"C:\material_inward\dms_staging"

folder_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FOLDER

time.sleep(1)
app = pywinauto.Application().connect(title="Open")
dlg = app["Open"]

# FIX: typing just the bare folder path here made the dialog list (and the
# Ctrl+A below select) EVERY file in DMS_STAGING_FOLDER -- not just the
# *.pdf invoices. That folder also holds each PDF's _meta.json sidecar and
# document_links.xlsx itself (DMS_LINKS_EXCEL_PATH lives in the same
# folder), so both were getting uploaded into Contentverse's batch queue
# right alongside the real invoices. dms_upload.robot's own file count and
# Index Each File loop only ever count/name the *.pdf files (via
# List Files In Directory ... *.pdf), so Contentverse's actual queue had
# MORE items in it than the robot had names ready for -- desyncing which
# typed name landed on which physical document the moment a non-PDF file
# was interleaved into Contentverse's own ordering. Confirmed in production:
# a "File Already Exists" conflict for a _meta.json sidecar, and Contentverse
# opening its embedded Excel editor for document_links.xlsx, both mid-batch.
# Appending \*.pdf uses the Open dialog's standard wildcard-filter behavior
# (same as typing a pattern into an Explorer address bar) so only PDFs are
# ever listed or selected here, regardless of what else is in the folder.
pdf_pattern = folder_path.rstrip("\\") + r"\*.pdf"

# Type the folder+filter pattern and navigate into it
dlg["File name:Edit"].set_text(pdf_pattern)
time.sleep(0.5)
dlg["File name:Edit"].type_keys("{ENTER}")
time.sleep(2)

# Click in the file list area to focus it
file_list = dlg["ShellView"]
file_list.click()
time.sleep(0.5)

# Select all files using Ctrl+A on the file list (PDF-only now, per the filter above)
file_list.type_keys("^a")
time.sleep(0.5)

# Click Open button
dlg["Open"].click()
