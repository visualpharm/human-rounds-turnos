"""ABC adapter for the patient's own uploaded lab results."""

from app.connectors import ClinicalHistoryLookupConnector

from . import patient_uploads as _impl


class PatientUploadsConnector(ClinicalHistoryLookupConnector):
    def lookup_by_dni(self, dni):
        return _impl.lookup_by_dni(dni)
