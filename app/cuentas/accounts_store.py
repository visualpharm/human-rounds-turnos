"""Persistence boundary for ``cuentas.accounts``.

``accounts`` reaches account, session, dependent, password-reset, and OTP
persistence only through this module. Implementations remain in ``core.db``:
every wrapper below is a signature-preserving delegation with no SQL or
behavior of its own.
"""

from app.core import db
from app.core import db_accounts
from app.core import db_slots

# Re-export the canonical exceptions; the domain never inspects SQL errors.
DbUnavailable = db.DbUnavailable
UserKeyTaken = db_accounts.UserKeyTaken
UserEmailTaken = db_accounts.UserEmailTaken


def get_session(token):
    return db_accounts.get_session(token=token)


def consume_verified_otp(contacto, proposito):
    return db_accounts.consume_verified_otp(contacto=contacto, proposito=proposito)


def get_user(dni):
    return db_accounts.get_user(dni=dni)


def user_by_portal_id(portal_user_id):
    return db_accounts.user_by_portal_id(portal_user_id=portal_user_id)


def user_claim_portal(portal_user_id, dni, email=None, nombre=None, apellido=None):
    return db_accounts.user_claim_portal(portal_user_id=portal_user_id, dni=dni, email=email,
                                         nombre=nombre, apellido=apellido)


def mcp_token_revoke(dni, only_if_set=False):
    return db_accounts.mcp_token_revoke(dni=dni, only_if_set=only_if_set)


def user_fill_name(dni, nombre=None, apellido=None):
    return db_accounts.user_fill_name(dni=dni, nombre=nombre, apellido=apellido)


def get_user_by_email(email):
    return db_accounts.get_user_by_email(email=email)


def create_user(dni, nombre, apellido, nacimiento, sexo, contacto, password, email=None,
                cobertura=None, domicilio=None, lang=None, identity_kind=None):
    return db_accounts.create_user(dni=dni, nombre=nombre, apellido=apellido, nacimiento=nacimiento,
                          sexo=sexo, contacto=contacto, password=password, email=email,
                          cobertura=cobertura, domicilio=domicilio, lang=lang,
                          identity_kind=identity_kind)


def create_user_and_session(dni, nombre, apellido, nacimiento, sexo, contacto,
                            password, email=None, cobertura=None, domicilio=None,
                            lang=None, identity_kind=None, claim=None):
    return db_accounts.create_user_and_session(
        dni=dni, nombre=nombre, apellido=apellido, nacimiento=nacimiento,
        sexo=sexo, contacto=contacto, password=password, email=email,
        cobertura=cobertura, domicilio=domicilio, lang=lang, identity_kind=identity_kind,
        claim=claim)


def open_claim_of(key):
    return db_accounts.open_claim_of(key=key)


def pending_claimants(dni):
    return db_accounts.pending_claimants(dni=dni)


def create_session(dni):
    return db_accounts.create_session(dni=dni)


def verify_password(dni, password):
    return db_accounts.verify_password(dni=dni, password=password)


def update_user(dni, nombre=None, apellido=None, nacimiento=None, sexo=None,
                contacto=None, email=None, cobertura=None, domicilio=None,
                avisos=None, lang=None, coverage_write=True,
                coverage_guard=False, coverage_expected=None):
    return db_accounts.update_user(dni=dni, nombre=nombre, apellido=apellido, nacimiento=nacimiento,
                          sexo=sexo, contacto=contacto, email=email, cobertura=cobertura,
                          domicilio=domicilio, avisos=avisos, lang=lang,
                          coverage_write=coverage_write, coverage_guard=coverage_guard,
                          coverage_expected=coverage_expected)


def delete_session(token):
    return db_accounts.delete_session(token=token)


def list_dependents(adulto_dni):
    return db_slots.list_dependents(adulto_dni=adulto_dni)


def get_dependent(adulto_dni, dni):
    return db_slots.get_dependent(adulto_dni=adulto_dni, dni=dni)


def save_dependent(adulto_dni, dni, nombre, apellido, nacimiento, sexo, vinculo):
    return db_slots.save_dependent(adulto_dni=adulto_dni, dni=dni, nombre=nombre,
                             apellido=apellido, nacimiento=nacimiento, sexo=sexo,
                             vinculo=vinculo)


def delete_dependent(adulto_dni, dni):
    return db_slots.delete_dependent(adulto_dni=adulto_dni, dni=dni)


def change_dni(viejo, nuevo):
    return db_accounts.change_dni(viejo=viejo, nuevo=nuevo)


def create_reset_token(dni, ttl_minutos=60):
    return db_accounts.create_reset_token(dni=dni, ttl_minutos=ttl_minutos)


def otp_recent(contacto, proposito, segundos=45):
    return db_accounts.otp_recent(contacto=contacto, proposito=proposito, segundos=segundos)


def create_otp(contacto, proposito, ttl_minutos=10):
    return db_accounts.create_otp(contacto=contacto, proposito=proposito, ttl_minutos=ttl_minutos)


def verify_otp(contacto, proposito, code):
    return db_accounts.verify_otp(contacto=contacto, proposito=proposito, code=code)


def set_password(dni, password):
    return db_accounts.set_password(dni=dni, password=password)


def consume_reset_token(token):
    return db_accounts.consume_reset_token(token=token)
