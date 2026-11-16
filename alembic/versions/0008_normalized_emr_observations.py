"""Normalize persisted legacy EMR mirrors before Core reads only v1 records.

Revision ID: 0008_normalized_emr_observations
Revises: 0007_existing_install_shape
Create Date: 2026-09-01

The temporary HSI adapter used to persist its wire JSON verbatim.  Core now
stores and reads only the documented EMR observation contract.  This is a
one-time in-place projection so existing imported appointments, no-show
history, and account rows retain their meaning without a runtime dual parser.
"""
from alembic import op


revision = "0008_normalized_emr_observations"
down_revision = "0007_existing_install_shape"
branch_labels = None
depends_on = None


def upgrade():
    # Historical HSI field spellings belong only in this one-way data upgrade.
    # All Core runtime readers use the normalized fields written below.
    op.execute("""
        UPDATE hsi_patients
        SET payload = jsonb_strip_nulls(jsonb_build_object(
            'id', hsi_patient_id,
            'document_number', COALESCE(payload #>> '{person,identificationNumber}', payload->>'identificationNumber'),
            'given_name', COALESCE(payload #>> '{person,firstName}', payload->>'firstName'),
            'family_name', COALESCE(payload #>> '{person,lastName}', payload->>'lastName')
        ))
        WHERE payload ? 'person' OR payload ? 'identificationNumber'
    """)
    op.execute("""
        UPDATE hsi_doctor_schedules
        SET payload = jsonb_strip_nulls(jsonb_build_object(
            'id', hsi_diary_id,
            'professional_id', hsi_professional_id,
            'specialty', payload #>> '{diary,specialty}',
            'professional_name', payload #>> '{diary,professional}'
        ))
        WHERE payload ? 'diary'
    """)
    for table, key_column in (("hsi_appointments", "hsi_appointment_id"),
                              ("hsi_checkpoints", "entity_id")):
        op.execute(f"""
            UPDATE {table}
            SET {"payload" if table == "hsi_appointments" else "remote"} = jsonb_strip_nulls(jsonb_build_object(
                'id', {key_column},
                'schedule_id', {"hsi_diary_id" if table == "hsi_appointments" else "remote->>'diaryId'"},
                'patient_id', {"hsi_patient_id" if table == "hsi_appointments" else "remote->>'patientId'"},
                'scheduled_at', CASE WHEN {"payload" if table == "hsi_appointments" else "remote"}->>'date' IS NOT NULL
                                      AND {"payload" if table == "hsi_appointments" else "remote"}->>'hour' IS NOT NULL
                    THEN ({"payload" if table == "hsi_appointments" else "remote"}->>'date') || 'T' ||
                         left({"payload" if table == "hsi_appointments" else "remote"}->>'hour', 8) || '-03:00' END,
                'state', CASE {"payload" if table == "hsi_appointments" else "remote"}->>'appointmentStateId'
                    WHEN '1' THEN 'scheduled' WHEN '2' THEN 'scheduled'
                    WHEN '3' THEN 'no_show' WHEN '4' THEN 'cancelled'
                    WHEN '5' THEN 'attended' WHEN '7' THEN 'blocked'
                    WHEN '9' THEN 'no_show' END,
                'version', {"payload" if table == "hsi_appointments" else "remote"}->>'updatedOn'
            ))
            WHERE {"payload" if table == "hsi_appointments" else "remote"} ? 'appointmentStateId'
        """)


def downgrade():
    raise NotImplementedError("EMR observation normalization is forward-only")
