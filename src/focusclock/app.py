import os
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

if __package__ is None or __package__ == "":
    # Running as a script (e.g., PyInstaller)
    sys.path.insert(
        0, os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
            )
        )
    from focusclock.util import app_data_dir, resource_path
    from focusclock.window import FocusClockWindow
else:
    # Running as a package
    from .util import app_data_dir, resource_path
    from .window import FocusClockWindow

_INSTANCE_KEY = "FocusClockSingleInstance"
_LOCAL_SERVER = "FocusClockLocalServer"


def _install_crash_logger() -> None:
    log_path = app_data_dir() / "crash.log"

    def _hook(exc_type, exc_value, exc_tb):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat()} ---\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def _load_app_icon() -> QIcon:
    icon_file = resource_path("icon.png")
    if icon_file.is_file():
        return QIcon(str(icon_file))
    return QIcon()


def _try_activate_existing_instance() -> bool:
    """Return True if another instance is running and was activated."""
    socket = QLocalSocket()
    socket.connectToServer(_LOCAL_SERVER)
    if socket.waitForConnected(500):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True
    return False


def _start_local_server(window: FocusClockWindow) -> QLocalServer:
    server = QLocalServer()
    QLocalServer.removeServer(_LOCAL_SERVER)
    server.listen(_LOCAL_SERVER)

    def on_connection():
        if server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.waitForReadyRead(200)
            window.restore_window()
            conn.disconnectFromServer()

    server.newConnection.connect(on_connection)
    return server


def main():
    _install_crash_logger()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    lock = QLockFile(str(app_data_dir() / "focusclock.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        if _try_activate_existing_instance():
            return 0
        QMessageBox.warning(
            None,
            "FocusClock",
            "FocusClock is already running.",
        )
        return 1

    app.setWindowIcon(_load_app_icon())

    w = FocusClockWindow()
    server = _start_local_server(w)
    app._focusclock_server = server  # keep reference alive

    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
