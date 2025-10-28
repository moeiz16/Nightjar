"""Build the small offline bundles committed under backend/nightjar from the downloaded sources in data/.

    python scripts/build_bundles.py            # all
    python scripts/build_bundles.py attack kev sigma

* attack: backend/nightjar/intel/data/attack_v18_1.json (techniques, sub-techniques, tactics)
* kev:    backend/nightjar/intel/data/kev_subset.json   (dateAdded <= 2025-11-27: inventory vendors + latest 200)
* sigma:  backend/nightjar/detect/rules/sigmahq/*.yml    (the curated active set, unmodified, DRL 1.1)

Needs `make data` first. Deterministic output (sorted keys, no timestamps).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INTEL = ROOT / "backend" / "nightjar" / "intel" / "data"
RULES = ROOT / "backend" / "nightjar" / "detect" / "rules" / "sigmahq"
KEV_CUTOFF = "2025-11-27"  # end of the project's demo window; lookups also hide entries after clock.today()

# Vendors/products present in the sample inventory (gateways, VPN, Windows estate, backup, identity, cloud).
KEV_VENDORS = {
    ("Citrix", None),
    ("Fortinet", None),
    ("Microsoft", "Windows"),
    ("Microsoft", "Exchange Server"),
    ("Microsoft", "SharePoint"),
    ("Microsoft", "Office"),
    ("Microsoft", "Outlook"),
    ("Veeam", None),
    ("Okta", None),
    ("VMware", None),
    ("Ivanti", None),
    ("Palo Alto Networks", None),
}

# Curated active SigmaHQ set (ADR 0003): rules for Halden's log sources and the techniques its scenarios and
# OTRF windows exercise, chosen for fidelity and evaluation cost. Paths relative to data/sigma/rules.
CURATED = """
windows/process_creation/proc_creation_win_rundll32_process_dump_via_comsvcs.yml
windows/process_creation/proc_creation_win_sysinternals_procdump_lsass.yml
windows/process_creation/proc_creation_win_susp_lsass_dmp_cli_keywords.yml
windows/process_creation/proc_creation_win_hktl_mimikatz_command_line.yml
windows/process_creation/proc_creation_win_createdump_lolbin_execution.yml
windows/process_creation/proc_creation_win_rdrleakdiag_process_dumping.yml
windows/process_creation/proc_creation_win_sysinternals_psexec_remote_execution.yml
windows/process_creation/proc_creation_win_sysinternals_psexesvc_as_system.yml
windows/process_creation/proc_creation_win_sysinternals_susp_psexec_paexec_flags.yml
windows/process_creation/proc_creation_win_sysinternals_psexec_paexec_escalate_system.yml
windows/process_creation/proc_creation_win_hktl_impacket_lateral_movement.yml
windows/process_creation/proc_creation_win_wmiprvse_susp_child_processes.yml
windows/process_creation/proc_creation_win_schtasks_creation_temp_folder.yml
windows/process_creation/proc_creation_win_schtasks_susp_pattern.yml
windows/process_creation/proc_creation_win_schtasks_system.yml
windows/process_creation/proc_creation_win_schtasks_powershell_persistence.yml
windows/process_creation/proc_creation_win_schtasks_folder_combos.yml
windows/process_creation/proc_creation_win_schtasks_schedule_type.yml
windows/process_creation/proc_creation_win_hktl_powersploit_empire_default_schtasks.yml
windows/process_creation/proc_creation_win_susp_shadow_copies_deletion.yml
windows/process_creation/proc_creation_win_wbadmin_delete_all_backups.yml
windows/process_creation/proc_creation_win_bcdedit_boot_conf_tamper.yml
windows/process_creation/proc_creation_win_powershell_shadowcopy_deletion.yml
windows/process_creation/proc_creation_win_susp_ntds.yml
windows/process_creation/proc_creation_win_susp_sensitive_file_access_shadowcopy.yml
windows/process_creation/proc_creation_win_cmd_shadowcopy_access.yml
windows/process_creation/proc_creation_win_esentutl_sensitive_file_copy.yml
windows/process_creation/proc_creation_win_reg_dumping_sensitive_hives.yml
windows/process_creation/proc_creation_win_powershell_base64_encoded_cmd.yml
windows/process_creation/proc_creation_win_powershell_base64_iex.yml
windows/process_creation/proc_creation_win_powershell_base64_frombase64string.yml
windows/process_creation/proc_creation_win_powershell_download_iex.yml
windows/process_creation/proc_creation_win_powershell_iex_patterns.yml
windows/process_creation/proc_creation_win_powershell_invoke_webrequest_download.yml
windows/process_creation/proc_creation_win_powershell_susp_download_patterns.yml
windows/process_creation/proc_creation_win_powershell_base64_hidden_flag.yml
windows/process_creation/proc_creation_win_powershell_frombase64string.yml
windows/process_creation/proc_creation_win_powershell_susp_parent_process.yml
windows/process_creation/proc_creation_win_powershell_webclient_casing.yml
windows/process_creation/proc_creation_win_hktl_empire_powershell_launch.yml
windows/process_creation/proc_creation_win_pua_adfind_susp_usage.yml
windows/process_creation/proc_creation_win_pua_adfind_enumeration.yml
windows/process_creation/proc_creation_win_renamed_adfind.yml
windows/process_creation/proc_creation_win_hktl_bloodhound_sharphound.yml
windows/process_creation/proc_creation_win_susp_whoami_as_param.yml
windows/process_creation/proc_creation_win_whoami_priv_discovery.yml
windows/process_creation/proc_creation_win_whoami_execution_from_high_priv_process.yml
windows/process_creation/proc_creation_win_nslookup_domain_discovery.yml
windows/process_creation/proc_creation_win_office_susp_child_processes.yml
windows/process_creation/proc_creation_win_office_outlook_susp_child_processes.yml
windows/process_creation/proc_creation_win_mshta_susp_execution.yml
windows/process_creation/proc_creation_win_mshta_http.yml
windows/process_creation/proc_creation_win_regsvr32_http_ip_pattern.yml
windows/process_creation/proc_creation_win_rundll32_no_params.yml
windows/process_creation/proc_creation_win_rundll32_unc_path.yml
windows/process_creation/proc_creation_win_certutil_download_direct_ip.yml
windows/process_creation/proc_creation_win_certutil_decode.yml
windows/process_creation/proc_creation_win_bitsadmin_download_direct_ip.yml
windows/process_creation/proc_creation_win_curl_download_direct_ip_susp_extensions.yml
windows/process_creation/proc_creation_win_wget_download_direct_ip.yml
windows/process_creation/proc_creation_win_pua_rclone_execution.yml
windows/process_creation/proc_creation_win_susp_data_exfiltration_via_cli.yml
windows/process_creation/proc_creation_win_rar_compression_with_password.yml
windows/process_creation/proc_creation_win_pua_ngrok.yml
windows/process_creation/proc_creation_win_pua_netcat.yml
windows/process_creation/proc_creation_win_pua_chisel.yml
windows/process_creation/proc_creation_win_plink_susp_tunneling.yml
windows/process_creation/proc_creation_win_net_user_add_never_expire.yml
windows/process_creation/proc_creation_win_susp_add_user_privileged_group.yml
windows/process_creation/proc_creation_win_susp_add_user_remote_desktop_group.yml
windows/process_creation/proc_creation_win_susp_eventlog_clear.yml
windows/process_creation/proc_creation_win_logman_disable_eventlog.yml
windows/process_creation/proc_creation_win_auditpol_susp_execution.yml
windows/process_creation/proc_creation_win_reg_disable_sec_services.yml
windows/process_creation/proc_creation_win_powershell_disable_defender_av_security_monitoring.yml
windows/process_creation/proc_creation_win_powershell_remove_mppreference.yml
windows/process_creation/proc_creation_win_wmic_uninstall_security_products.yml
windows/process_creation/proc_creation_win_susp_service_tamper.yml
windows/process_creation/proc_creation_win_taskkill_sep.yml
windows/process_creation/proc_creation_win_netsh_fw_allow_rdp.yml
windows/process_creation/proc_creation_win_netsh_port_forwarding_3389.yml
windows/process_creation/proc_creation_win_mstsc_rdp_hijack_shadowing.yml
windows/process_creation/proc_creation_win_tscon_rdp_redirect.yml
windows/process_creation/proc_creation_win_remote_access_tools_anydesk_silent_install.yml
windows/process_creation/proc_creation_win_webshell_recon_commands_and_processes.yml
windows/process_creation/proc_creation_win_webshell_susp_process_spawned_from_webserver.yml
windows/process_creation/proc_creation_win_susp_right_to_left_override.yml
windows/process_creation/proc_creation_win_susp_double_extension.yml
windows/process_creation/proc_creation_win_susp_script_exec_from_temp.yml
windows/process_access/proc_access_win_lsass_dump_comsvcs_dll.yml
windows/process_access/proc_access_win_lsass_dump_keyword_image.yml
windows/process_access/proc_access_win_hktl_generic_access.yml
windows/process_access/proc_access_win_lsass_werfault.yml
windows/builtin/security/win_security_susp_lsass_dump.yml
windows/builtin/security/win_security_impacket_psexec.yml
windows/builtin/security/win_security_susp_psexec.yml
windows/builtin/security/win_security_susp_scheduled_task_creation.yml
windows/builtin/security/win_security_audit_log_cleared.yml
windows/builtin/security/win_security_dcsync.yml
windows/builtin/security/win_security_account_discovery.yml
windows/builtin/security/win_security_susp_net_recon_activity.yml
windows/builtin/security/win_security_hidden_user_creation.yml
windows/builtin/security/win_security_metasploit_or_impacket_smb_psexec_service_install.yml
windows/builtin/security/win_security_smb_file_creation_admin_shares.yml
windows/builtin/security/win_security_remote_powershell_session.yml
windows/builtin/security/win_security_impacket_secretdump.yml
windows/builtin/security/win_security_sam_registry_hive_handle_request.yml
windows/builtin/security/account_management/win_security_successful_external_remote_smb_login.yml
windows/builtin/security/account_management/win_security_overpass_the_hash.yml
windows/powershell/powershell_script/posh_ps_potential_invoke_mimikatz.yml
windows/powershell/powershell_script/posh_ps_malicious_commandlets.yml
windows/powershell/powershell_script/posh_ps_susp_win32_shadowcopy_deletion.yml
windows/powershell/powershell_script/posh_ps_powerview_malicious_commandlets.yml
windows/powershell/powershell_script/posh_ps_susp_getprocess_lsass.yml
windows/powershell/powershell_script/posh_ps_tamper_windows_defender_set_mp.yml
windows/powershell/powershell_script/posh_ps_mailboxexport_share.yml
windows/network_connection/net_connection_win_domain_ngrok.yml
windows/network_connection/net_connection_win_rdp_reverse_tunnel.yml
windows/network_connection/net_connection_win_susp_malware_callback_port.yml
windows/network_connection/net_connection_win_wscript_cscript_outbound_connection.yml
windows/network_connection/net_connection_win_certutil_initiated_connection.yml
cloud/m365/audit/microsoft365_disabling_mfa.yml
cloud/aws/cloudtrail/aws_iam_s3browser_user_or_accesskey_creation.yml
cloud/aws/cloudtrail/aws_guardduty_disruption.yml
cloud/aws/cloudtrail/aws_config_disable_recording.yml
"""


def build_attack() -> None:
    stix = json.loads((DATA / "attack" / "enterprise-attack-18.1.json").read_text())
    objs = stix["objects"]
    tactics = {
        o["x_mitre_shortname"]: o["name"]
        for o in objs
        if o.get("type") == "x-mitre-tactic" and not o.get("revoked") and not o.get("x_mitre_deprecated")
    }
    names: dict[str, str] = {}
    techniques: dict[str, dict] = {}
    for o in objs:
        if o.get("type") != "attack-pattern" or o.get("revoked") or o.get("x_mitre_deprecated"):
            continue
        ref = next((r for r in o.get("external_references", []) if r.get("source_name") == "mitre-attack"), None)
        if not ref:
            continue
        tid = ref["external_id"]
        names[tid] = o["name"]
        desc = " ".join((o.get("description") or "").split())
        first = desc.split(". ")[0].rstrip(".") + "." if desc else ""
        first = _strip_citations(first)
        techniques[tid] = {
            "name": o["name"],
            "tactics": sorted({p["phase_name"] for p in o.get("kill_chain_phases", [])}),
            "description": first[:400],
            "url": ref.get("url", f"https://attack.mitre.org/techniques/{tid.replace('.', '/')}/"),
        }
    for tid, t in techniques.items():  # sub-techniques read "Parent: Sub"
        if "." in tid and tid.split(".")[0] in names:
            t["name"] = f"{names[tid.split('.')[0]]}: {t['name']}"
    out = {
        "version": "18.1",
        "source": "MITRE ATT&CK Enterprise v18.1 (STIX 2.1), subset of fields",
        "tactics": dict(sorted(tactics.items())),
        "techniques": dict(sorted(techniques.items())),
    }
    INTEL.mkdir(parents=True, exist_ok=True)
    (INTEL / "attack_v18_1.json").write_text(json.dumps(out, indent=0, sort_keys=False, ensure_ascii=False) + "\n")
    print(f"attack: {len(techniques)} techniques, {len(tactics)} tactics")


def _strip_citations(text: str) -> str:
    import re

    text = re.sub(r"\(Citation:[^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    return " ".join(text.split())


def build_kev() -> None:
    feed = json.loads((DATA / "kev" / "known_exploited_vulnerabilities.json").read_text())
    vulns = [v for v in feed["vulnerabilities"] if v["dateAdded"] <= KEV_CUTOFF]

    def wanted(v: dict) -> bool:
        for vendor, product in KEV_VENDORS:
            if v["vendorProject"] == vendor and (product is None or product.lower() in v["product"].lower()):
                return True
        return False

    keep = {v["cveID"]: v for v in vulns if wanted(v)}
    for v in sorted(vulns, key=lambda v: (v["dateAdded"], v["cveID"]), reverse=True)[:200]:
        keep[v["cveID"]] = v
    rows = [
        {
            "cveID": v["cveID"],
            "vendorProject": v["vendorProject"],
            "product": v["product"],
            "vulnerabilityName": v["vulnerabilityName"],
            "dateAdded": v["dateAdded"],
            "knownRansomwareCampaignUse": v["knownRansomwareCampaignUse"],
            "shortDescription": v["shortDescription"][:300],
        }
        for v in sorted(keep.values(), key=lambda v: v["cveID"])
    ]
    out = {"source": "CISA Known Exploited Vulnerabilities (CC0 1.0), subset", "cutoff": KEV_CUTOFF, "items": rows}
    (INTEL / "kev_subset.json").write_text(json.dumps(out, indent=0, ensure_ascii=False) + "\n")
    print(f"kev: {len(rows)} entries (cutoff {KEV_CUTOFF})")


def build_sigma() -> None:
    src = DATA / "sigma" / "rules"
    files = [line.strip() for line in CURATED.splitlines() if line.strip()]
    if RULES.exists():
        for old in RULES.glob("*.yml"):
            old.unlink()
    RULES.mkdir(parents=True, exist_ok=True)
    missing = []
    for rel in files:
        path = src / rel
        if not path.exists():
            missing.append(rel)
            continue
        shutil.copyfile(path, RULES / path.name)
    if missing:
        print("missing:", *missing, sep="\n  ")
    print(f"sigma: {len(files) - len(missing)} curated rules copied")


def main(argv: list[str]) -> None:
    targets = argv or ["attack", "kev", "sigma"]
    for t in targets:
        {"attack": build_attack, "kev": build_kev, "sigma": build_sigma}[t]()


if __name__ == "__main__":
    main(sys.argv[1:])
