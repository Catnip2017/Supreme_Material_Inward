import win32com.client
def type_save_filename(filepath, wait_before=1.0, wait_after=1.5):
    """
    Types a filename into the native Windows "Save Print Output As" dialog
    and presses Enter to save. The File name field already has focus when
    this dialog opens, so this sends keystrokes directly via
    WScript.Shell.SendKeys without needing to activate the window by title.
    """
    import time
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    time.sleep(wait_before)
    shell.SendKeys("^a")
    shell.SendKeys(filepath)
    time.sleep(0.3)
    shell.SendKeys("{ENTER}")
    time.sleep(wait_after)
