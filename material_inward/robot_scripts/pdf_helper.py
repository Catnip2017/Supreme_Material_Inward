import time
import win32gui, win32con

# REPLACED (2026-09-03): the old version of this file drove the native
# "Save Print Output As" dialog that used to appear after every SAP print
# (type_save_filename() -- searched for the dialog by title via pywinauto,
# then hunted for its filename field). zgrn.robot no longer prints to PDF
# at all -- it ticks SAP's own "Download for MIP" checkbox, which writes
# the PDF straight to disk with no Save dialog involved. The only native
# dialog left is the same-day-reprint overwrite confirmation below.


def confirm_overwrite_dialog(timeout=5):
    """Finds the native 'Confirm Save As' box and clicks Yes explicitly.
    The default-focused button is 'No', so Enter is not safe here."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = win32gui.FindWindow(None, "Confirm Save As")
        if hwnd:
            btn = []
            def _cb(h, _):
                if win32gui.GetClassName(h) == "Button":
                    caption = win32gui.GetWindowText(h).replace("&", "")
                    if caption.strip().lower() == "yes":
                        btn.append(h)
            win32gui.EnumChildWindows(hwnd, _cb, None)
            if btn:
                win32gui.SendMessage(btn[0], win32con.BM_CLICK, 0, 0)
                return "OVERWRITE_CONFIRMED"
            return "YES_BUTTON_NOT_FOUND"
        time.sleep(0.5)
    return "NO_POPUP"
