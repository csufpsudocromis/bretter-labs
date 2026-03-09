import logging
import ssl
from dataclasses import dataclass
from urllib.parse import urlparse

from ldap3 import (
    ALL,
    AUTO_BIND_NO_TLS,
    AUTO_BIND_TLS_BEFORE_BIND,
    Connection,
    Server,
    SUBTREE,
    Tls,
)
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

logger = logging.getLogger(__name__)


@dataclass
class LDAPRuntimeConfig:
    enabled: bool = False
    server_uri: str = ""
    bind_dn: str = ""
    bind_password: str = ""
    user_base_dn: str = ""
    user_filter: str = "(uid={username})"
    start_tls: bool = False
    insecure_skip_verify: bool = False
    timeout_seconds: int = 10


def missing_required_fields(cfg: LDAPRuntimeConfig) -> list[str]:
    if not cfg.enabled:
        return []
    missing: list[str] = []
    if not str(cfg.server_uri or "").strip():
        missing.append("ldap_server_uri")
    if not str(cfg.user_base_dn or "").strip():
        missing.append("ldap_user_base_dn")
    if not str(cfg.user_filter or "").strip():
        missing.append("ldap_user_filter")
    return missing


def _server_from_uri(cfg: LDAPRuntimeConfig) -> tuple[Server, bool]:
    raw = str(cfg.server_uri or "").strip()
    if not raw:
        raise ValueError("LDAP server URI is required")
    if "://" not in raw:
        raw = f"ldap://{raw}"
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "ldap").strip().lower()
    if scheme not in {"ldap", "ldaps"}:
        raise ValueError("LDAP server URI scheme must be ldap:// or ldaps://")
    host = str(parsed.hostname or "").strip()
    if not host:
        raise ValueError("LDAP server host is missing")
    use_ssl = scheme == "ldaps"
    port = parsed.port or (636 if use_ssl else 389)
    tls = Tls(validate=ssl.CERT_NONE if cfg.insecure_skip_verify else ssl.CERT_REQUIRED)
    server = Server(
        host=host,
        port=int(port),
        use_ssl=use_ssl,
        tls=tls,
        connect_timeout=max(3, min(60, int(cfg.timeout_seconds or 10))),
        get_info=ALL,
    )
    return server, use_ssl


def _open_connection(
    *,
    server: Server,
    bind_dn: str,
    bind_password: str,
    start_tls: bool,
) -> Connection:
    auto_bind = AUTO_BIND_TLS_BEFORE_BIND if start_tls else AUTO_BIND_NO_TLS
    return Connection(
        server=server,
        user=bind_dn or None,
        password=bind_password or None,
        auto_bind=auto_bind,
        raise_exceptions=True,
        receive_timeout=max(3, min(60, int(server.connect_timeout or 10))),
    )


def authenticate(username: str, password: str, cfg: LDAPRuntimeConfig) -> tuple[bool, str]:
    if not cfg.enabled:
        return False, "disabled"
    if not str(password or ""):
        return False, "empty_password"

    missing = missing_required_fields(cfg)
    if missing:
        raise ValueError(f"LDAP config missing required fields: {', '.join(missing)}")

    try:
        server, using_ldaps = _server_from_uri(cfg)
    except ValueError:
        raise

    escaped_username = escape_filter_chars(str(username or "").strip())
    template = str(cfg.user_filter or "(uid={username})").strip() or "(uid={username})"
    try:
        search_filter = template.format(username=escaped_username)
    except Exception as exc:
        raise ValueError("LDAP user filter must include a valid {username} placeholder") from exc

    if not search_filter:
        raise ValueError("LDAP user filter cannot be empty")

    start_tls = bool(cfg.start_tls and not using_ldaps)
    bind_dn = str(cfg.bind_dn or "").strip()
    bind_password = str(cfg.bind_password or "")

    user_dn = ""
    search_conn: Connection | None = None
    try:
        search_conn = _open_connection(
            server=server,
            bind_dn=bind_dn,
            bind_password=bind_password,
            start_tls=start_tls,
        )
        search_conn.search(
            search_base=str(cfg.user_base_dn or "").strip(),
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["dn"],
            size_limit=2,
        )
        if not search_conn.entries:
            return False, "user_not_found"
        user_dn = str(search_conn.entries[0].entry_dn or "").strip()
        if not user_dn:
            return False, "user_dn_not_found"
    except LDAPException as exc:
        logger.warning("LDAP search phase failed: %s", exc)
        raise RuntimeError("LDAP directory search failed") from exc
    finally:
        if search_conn is not None:
            try:
                search_conn.unbind()
            except Exception:
                pass

    user_conn: Connection | None = None
    try:
        user_conn = _open_connection(
            server=server,
            bind_dn=user_dn,
            bind_password=str(password),
            start_tls=start_tls,
        )
        if not user_conn.bound:
            return False, "invalid_credentials"
        return True, user_dn
    except LDAPException:
        return False, "invalid_credentials"
    finally:
        if user_conn is not None:
            try:
                user_conn.unbind()
            except Exception:
                pass
