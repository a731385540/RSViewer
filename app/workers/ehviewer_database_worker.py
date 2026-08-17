from PySide6.QtCore import QObject, QRunnable, Signal

from app.services.ehviewer_database_transfer import export_ehviewer_database


class EhViewerDatabaseExportSignals(QObject):
    completed = Signal(str, int)
    failed = Signal(str)


class EhViewerDatabaseExportWorker(QRunnable):
    def __init__(self, repository, destination_path):
        super().__init__()
        self.repository = repository
        self.destination_path = destination_path
        self.signals = EhViewerDatabaseExportSignals()

    def run(self):
        try:
            result = export_ehviewer_database(
                self.repository,
                self.destination_path,
            )
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.completed.emit(str(result.path), result.gallery_count)
