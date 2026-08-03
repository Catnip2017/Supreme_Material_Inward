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
import time
import pywinauto

DEFAULT_FOLDER = r"C:\material_inward\dms_staging"

folder_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FOLDER

time.sleep(1)
app = pywinauto.Application().connect(title="Open")
dlg = app["Open"]

# Type the folder path and navigate into it
dlg["File name:Edit"].set_text(folder_path)
time.sleep(0.5)
dlg["File name:Edit"].type_keys("{ENTER}")
time.sleep(2)

# Click in the file list area to focus it
file_list = dlg["ShellView"]
file_list.click()
time.sleep(0.5)

# Select all files using Ctrl+A on the file list
file_list.type_keys("^a")
time.sleep(0.5)

# Click Open button
dlg["Open"].click()
