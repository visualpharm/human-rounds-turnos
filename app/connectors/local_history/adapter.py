"""ABC adapter for the private local clinical-history archive."""

from app.connectors import ClinicalHistoryLookupConnector

from . import local_history as _impl


class LocalHistoryConnector(ClinicalHistoryLookupConnector):
    def lookup_by_dni(self, dni):
        return _impl.lookup_by_dni(dni)
