from __future__ import annotations

import base64
import contextlib
import os
import subprocess
from xml.sax.saxutils import escape


def show_windows_notification(title: str, message: str) -> None:
    """Show a non-blocking Windows toast when the application is not in focus.

    The app has no extra notification dependency.  Windows PowerShell can use
    the built-in toast API on supported Windows versions; failures are safely
    ignored because the same information remains visible in the application.
    """
    if os.name != "nt":
        return
    title_xml = escape(title)
    message_xml = escape(message)
    script = f'''[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,
ContentType=WindowsRuntime] | Out-Null
$template = @'
<toast><visual><binding template="ToastGeneric"><text>{title_xml}</text>
<text>{message_xml}</text></binding></visual></toast>
'@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("eStamp Automation").Show($toast)
'''
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    with contextlib.suppress(OSError):
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
