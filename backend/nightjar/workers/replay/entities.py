"""Halden Logistics sample entities used by the synthetic replays (API contract Part 6). Fictional: `.example`
domains, documentation address ranges for the outside world, private 10.x ranges inside."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Host:
    name: str
    ip: str
    site: str  # antwerp | memphis | melbourne | cloud
    kind: str
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class Person:
    username: str
    name: str
    site: str
    country: str
    workstation: str | None
    kind: str = "employee"  # employee | contractor | service | admin
    remote: bool = False


SITE_TZ = {"antwerp": "Europe/Brussels", "memphis": "America/Chicago", "melbourne": "Australia/Melbourne"}
SITE_ISP = {"antwerp": "Telenet BV", "memphis": "Comcast Cable", "melbourne": "Telstra Corporation"}
SITE_COUNTRY = {"antwerp": "BE", "memphis": "US", "melbourne": "AU"}
SITE_VPN = {"antwerp": "ant-vpn-01", "memphis": "mem-citrix-01", "melbourne": "mel-vpn-01"}
EMAIL_DOMAIN = "halden.example"
AWS_ACCOUNT = "111122223333"
CI_KEY = "AKIAEXAMPLEHALDEN01"

HOSTS: tuple[Host, ...] = (
    Host("mem-dispatch-01", "10.10.1.10", "memphis", "server"),
    Host("mem-sql-01", "10.10.1.20", "memphis", "server"),
    Host("mem-citrix-01", "10.10.0.5", "memphis", "network"),
    Host("mem-ws-031", "10.10.20.31", "memphis", "workstation", "m.bell"),
    Host("mem-ws-040", "10.10.20.40", "memphis", "workstation", "t.reyes"),
    Host("ant-wms-01", "10.20.1.10", "antwerp", "server"),
    Host("ant-dc-01", "10.20.1.2", "antwerp", "server"),
    Host("ant-fs-01", "10.20.1.30", "antwerp", "server"),
    Host("ant-backup-01", "10.20.1.40", "antwerp", "server"),
    Host("ant-vpn-01", "10.20.0.5", "antwerp", "network"),
    Host("ant-ws-014", "10.20.20.14", "antwerp", "workstation", "j.okafor"),
    Host("ant-ws-022", "10.20.20.22", "antwerp", "workstation", "l.peeters"),
    Host("mel-vpn-01", "10.30.0.5", "melbourne", "network"),
    Host("mel-depot-gw-01", "10.30.1.10", "melbourne", "ot_gateway"),
    Host("mel-ws-007", "10.30.20.7", "melbourne", "workstation", "a.tanaka"),
)
HOST = {h.name: h for h in HOSTS}
WINDOWS_HOSTS = tuple(h for h in HOSTS if h.kind in ("server", "workstation", "ot_gateway"))

PEOPLE: tuple[Person, ...] = (
    Person("j.okafor", "Joy Okafor", "antwerp", "BE", "ant-ws-014"),
    Person("p.claes", "Pieter Claes", "antwerp", "BE", None),
    Person("i.vos", "Ingrid Vos", "antwerp", "BE", None),
    Person("m.bell", "Marcus Bell", "memphis", "US", "mem-ws-031"),
    Person("a.tanaka", "Aiko Tanaka", "melbourne", "AU", "mel-ws-007"),
    Person("l.peeters", "Lukas Peeters", "antwerp", "BE", "ant-ws-022"),
    Person("s.haddad", "Samira Haddad", "antwerp", "BE", None),
    Person("r.dubois", "Remi Dubois", "antwerp", "BE", None),
    Person("e.janssens", "Eva Janssens", "antwerp", "BE", None, "admin"),
    Person("k.wouters", "Koen Wouters", "antwerp", "BE", None),
    Person("g.lambert", "Griet Lambert", "antwerp", "BE", None),
    Person("d.harper", "Dana Harper", "memphis", "US", None, remote=True),
    Person("j.whitlock", "Jordan Whitlock", "memphis", "US", None, remote=True),
    Person("c.nguyen", "Chris Nguyen", "memphis", "US", None, "admin", remote=True),
    Person("o.fernandez", "Olivia Fernandez", "memphis", "US", None),
    Person("t.reyes", "Tomas Reyes", "memphis", "US", "mem-ws-040", "contractor"),
    Person("n.kelly", "Niamh Kelly", "melbourne", "AU", None, remote=True),
    Person("b.singh", "Baljit Singh", "melbourne", "AU", None, remote=True),
    Person("h.morrison", "Hamish Morrison", "melbourne", "AU", None, "admin"),
    Person("w.chen", "Wei Chen", "melbourne", "AU", None, remote=True),
)
PERSON = {p.username: p for p in PEOPLE}
SERVICE_ACCOUNTS = ("svc-dispatch", "svc-backup", "ci-deploy")
ADMIN_ACCOUNT = "adm-ejanssens"

# Spray targets: real usernames mixed with guesses (63 accounts in the halden-night spray).
SPRAY_GUESSES = (
    "admin",
    "administrator",
    "test",
    "guest",
    "support",
    "helpdesk",
    "scanner",
    "backup",
    "vpnuser",
    "training",
    "reception",
    "warehouse",
    "depot",
    "fleet",
    "dispatch",
    "finance",
    "hr",
    "it",
    "sales",
    "ops",
    "info",
    "user1",
    "user2",
    "temp",
    "sysadmin",
    "operator",
    "service",
    "printer",
    "kiosk",
    "manager",
    "accounts",
    "payroll",
    "logistics",
    "driver",
    "night",
    "security",
    "audit",
    "remote",
    "citrix",
    "linehaul",
    "telematics",
    "m.smith",
    "j.brown",
    "s.wilson",
    "a.taylor",
)


def email_of(username: str) -> str:
    return f"{username}@{EMAIL_DOMAIN}"
