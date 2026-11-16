"""Persistence boundary for ``admin_panel.reports``.

Reports reach their audited reporting-query surface only through this module.
Every wrapper preserves the canonical ``core.db`` signature and delegates
without adding SQL, schema, connection handling, or report policy.
"""

from app.core import db
from app.core import db_booking
from app.core import db_ai
from app.core import db_analytics
from app.core import db_reports

# Re-export the exact exception object so existing handlers keep catching
# storage outages raised by the canonical implementation.
DbUnavailable = db.DbUnavailable


def reports_kpis(dias=30):
    return db_reports.reports_kpis(dias=dias)


def reports_volume(dias=30):
    return db_reports.reports_volume(dias=dias)


def reports_booking_requests_by_service(dias=30):
    return db_reports.reports_booking_requests_by_service(dias=dias)


def reports_approved_appointment(dias=30):
    return db_reports.reports_approved_appointment(dias=dias)


def reports_rejections(dias=30):
    return db_reports.reports_rejections(dias=dias)


def top_searches(dias=30):
    return db_analytics.top_searches(dias=dias)


def reports_median_wait_prior(dias=30):
    return db_reports.reports_median_wait_prior(dias=dias)


def reports_holds_day_hour(dias=30):
    return db_reports.reports_holds_day_hour(dias=dias)


def reports_time_preference(dias=30):
    return db_reports.reports_time_preference(dias=dias)


def reports_funnel(dias=30):
    return db_reports.reports_funnel(dias=dias)


def reports_appointment_offers(dias=30):
    return db_reports.reports_appointment_offers(dias=dias)


def ai_costs_has_rows():
    return db_ai.ai_costs_has_rows()


def costs_summary(dias=30):
    return db_ai.costs_summary(dias=dias)


def costs_by_operation(dias=30):
    return db_ai.costs_by_operation(dias=dias)


def reports_puco_found(dias=30):
    return db_reports.reports_puco_found(dias=dias)


def reports_export_rows(dias=90):
    return db_reports.reports_export_rows(dias=dias)


def reports_patients_ages():
    return db_reports.reports_patients_ages()


def reports_patients_coverage():
    return db_reports.reports_patients_coverage()


def reports_patients_booking_requests_by_patient(dias=30):
    return db_reports.reports_patients_booking_requests_by_patient(dias=dias)


def reports_patients_kpis(dias=30):
    return db_reports.reports_patients_kpis(dias=dias)


def reports_patients_volume(dias=30):
    return db_reports.reports_patients_volume(dias=dias)


def reports_patients_active_months(meses=12):
    return db_reports.reports_patients_active_months(meses=meses)


def reports_patients_dependents():
    return db_reports.reports_patients_dependents()


def stats_panel_login(dias=30):
    return db_analytics.stats_panel_login(dias=dias)


def reports_consultations_completed(dias=90):
    return db_reports.reports_consultations_completed(dias=dias)


def reports_statistics_rows(desde_iso, hasta_iso):
    return db_reports.reports_statistics_rows(desde_iso=desde_iso, hasta_iso=hasta_iso)


def reports_booking_requests_by_week(semanas=8):
    return db_reports.reports_booking_requests_by_week(semanas=semanas)


def reports_searches_by_week(semanas=8):
    return db_reports.reports_searches_by_week(semanas=semanas)


def list_approved_future(desde=None, hasta=None):
    return db_booking.list_approved_future(desde=desde, hasta=hasta)
