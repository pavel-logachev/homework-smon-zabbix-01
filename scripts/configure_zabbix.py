#!/usr/bin/env python
"""Idempotently configure and verify the Netology Zabbix Part 2 lab."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "http://127.0.0.1:18084/api_jsonrpc.php"
EVIDENCE = Path(__file__).resolve().parents[1] / "evidence" / "api-verification.json"
ADMIN_USER = "Admin"
ADMIN_PASSWORD = "zabbix"  # Default credential of this disposable loopback-only lab.
CUSTOM_TEMPLATE = "Netology CPU and RAM"
HOSTS = {
    "logachevpa-1": "agent-1",
    "logachevpa-2": "agent-2",
}


def rpc(method: str, params: object, auth: str | None = None) -> object:
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }
    if auth is not None:
        payload["auth"] = auth
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json-rpc"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode())
    if "error" in result:
        raise RuntimeError(f"{method}: {result['error']}")
    return result["result"]


def wait_for_api(timeout: int = 240) -> str:
    deadline = time.time() + timeout
    last_error = "not attempted"
    while time.time() < deadline:
        try:
            version = str(rpc("apiinfo.version", {}))
            auth = str(
                rpc(
                    "user.login",
                    {"user": ADMIN_USER, "password": ADMIN_PASSWORD},
                )
            )
            print(f"Zabbix API {version} is ready")
            return auth
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            last_error = str(exc)
            time.sleep(3)
    raise TimeoutError(f"Zabbix API did not become ready: {last_error}")


def first(method: str, params: dict, description: str, auth: str) -> dict:
    rows = rpc(method, params, auth)
    if not rows:
        raise RuntimeError(f"Not found: {description}")
    return rows[0]


def ensure_template(auth: str, groupid: str) -> str:
    rows = rpc(
        "template.get",
        {"output": ["templateid", "host", "name"], "filter": {"host": [CUSTOM_TEMPLATE]}},
        auth,
    )
    if rows:
        templateid = rows[0]["templateid"]
    else:
        templateid = rpc(
            "template.create",
            {"host": CUSTOM_TEMPLATE, "name": CUSTOM_TEMPLATE, "groups": [{"groupid": groupid}]},
            auth,
        )["templateids"][0]
    return str(templateid)


def ensure_item(auth: str, templateid: str, spec: dict) -> str:
    rows = rpc(
        "item.get",
        {
            "output": ["itemid", "name", "key_", "units", "delay", "status"],
            "templateids": templateid,
            "filter": {"key_": [spec["key_"]]},
        },
        auth,
    )
    if rows:
        itemid = rows[0]["itemid"]
        rpc("item.update", {"itemid": itemid, **spec}, auth)
    else:
        itemid = rpc("item.create", {"hostid": templateid, **spec}, auth)["itemids"][0]
    return str(itemid)


def ensure_host(
    auth: str,
    host: str,
    dns: str,
    host_groupid: str,
    linux_templateid: str,
    custom_templateid: str,
) -> str:
    rows = rpc("host.get", {"output": ["hostid"], "filter": {"host": [host]}}, auth)
    templates = [{"templateid": linux_templateid}, {"templateid": custom_templateid}]
    interface = {
        "type": 1,
        "main": 1,
        "useip": 0,
        "ip": "",
        "dns": dns,
        "port": "10050",
    }
    if rows:
        hostid = str(rows[0]["hostid"])
        rpc(
            "host.update",
            {
                "hostid": hostid,
                "groups": [{"groupid": host_groupid}],
                "templates": templates,
            },
            auth,
        )
        interfaces = rpc(
            "hostinterface.get",
            {"output": "extend", "hostids": hostid, "filter": {"type": 1, "main": 1}},
            auth,
        )
        if interfaces:
            rpc("hostinterface.update", {"interfaceid": interfaces[0]["interfaceid"], **interface}, auth)
        else:
            rpc("hostinterface.create", {"hostid": hostid, **interface}, auth)
    else:
        hostid = str(
            rpc(
                "host.create",
                {
                    "host": host,
                    "name": host,
                    "groups": [{"groupid": host_groupid}],
                    "templates": templates,
                    "interfaces": [interface],
                },
                auth,
            )["hostids"][0]
        )
    return hostid


def wait_for_values(auth: str, hostids: list[str], timeout: int = 180) -> list[dict]:
    deadline = time.time() + timeout
    latest: list[dict] = []
    while time.time() < deadline:
        latest = rpc(
            "item.get",
            {
                "output": ["itemid", "hostid", "name", "key_", "lastvalue", "lastclock", "units", "state"],
                "hostids": hostids,
                "filter": {"key_": ["netology.cpu.util", "netology.ram.util"]},
            },
            auth,
        )
        seen = {row["hostid"] for row in latest if int(row.get("lastclock") or 0) > 0}
        if seen == set(hostids) and len(latest) >= 4:
            return latest
        time.sleep(5)
    raise TimeoutError(f"Latest values missing after {timeout}s: {latest}")


def ensure_graph(auth: str, hostid: str, host: str, itemids_by_key: dict[str, str]) -> str:
    name = f"{host}: CPU and RAM"
    rows = rpc(
        "graph.get",
        {"output": ["graphid", "name"], "hostids": hostid, "filter": {"name": [name]}},
        auth,
    )
    if rows:
        return str(rows[0]["graphid"])
    result = rpc(
        "graph.create",
        {
            "name": name,
            "width": 900,
            "height": 200,
            "gitems": [
                {
                    "itemid": itemids_by_key["netology.cpu.util"],
                    "color": "E53935",
                    "sortorder": 0,
                },
                {
                    "itemid": itemids_by_key["netology.ram.util"],
                    "color": "2E7D32",
                    "sortorder": 1,
                },
            ],
        },
        auth,
    )
    return str(result["graphids"][0])


def ensure_dashboard(auth: str, graphids: dict[str, str]) -> str:
    name = "Netology Zabbix Part 2"
    rows = rpc("dashboard.get", {"output": ["dashboardid", "name"], "filter": {"name": [name]}}, auth)
    widgets = []
    for index, host in enumerate(HOSTS):
        widgets.append(
            {
                "type": "svggraph",
                "name": f"{host}: CPU and RAM",
                "x": index * 12,
                "y": 0,
                "width": 12,
                "height": 5,
                "view_mode": 0,
                "fields": [
                    {"type": 1, "name": "ds.hosts.0.0", "value": host},
                    {"type": 1, "name": "ds.items.0.0", "value": "CPU utilization"},
                    {"type": 1, "name": "ds.color.0", "value": "E53935"},
                    {"type": 1, "name": "ds.hosts.1.0", "value": host},
                    {"type": 1, "name": "ds.items.1.0", "value": "RAM utilization"},
                    {"type": 1, "name": "ds.color.1", "value": "2E7D32"},
                    {"type": 0, "name": "graph_time", "value": 1},
                    {"type": 1, "name": "time_from", "value": "now-1h"},
                    {"type": 0, "name": "legend_lines", "value": 2},
                ],
            }
        )
    pages = [{"name": "CPU and RAM", "display_period": 30, "widgets": widgets}]
    if rows:
        dashboardid = str(rows[0]["dashboardid"])
        rpc("dashboard.update", {"dashboardid": dashboardid, "pages": pages}, auth)
    else:
        dashboardid = str(
            rpc(
                "dashboard.create",
                {"name": name, "display_period": 30, "auto_start": 0, "pages": pages},
                auth,
            )["dashboardids"][0]
        )
    return dashboardid


def main() -> None:
    auth = wait_for_api()
    version = str(rpc("apiinfo.version", {}))

    template_group = first(
        "hostgroup.get",
        {"output": ["groupid", "name"], "filter": {"name": ["Templates/Operating systems"]}},
        "template group Templates/Operating systems",
        auth,
    )
    linux_group = first(
        "hostgroup.get",
        {"output": ["groupid", "name"], "filter": {"name": ["Linux servers"]}},
        "host group Linux servers",
        auth,
    )
    linux_template = first(
        "template.get",
        {"output": ["templateid", "host", "name"], "filter": {"host": ["Linux by Zabbix agent"]}},
        "template Linux by Zabbix agent",
        auth,
    )

    custom_templateid = ensure_template(auth, template_group["groupid"])
    template_items = {
        "netology.cpu.util": ensure_item(
            auth,
            custom_templateid,
            {
                "name": "CPU utilization",
                "key_": "netology.cpu.util",
                "type": 15,
                "value_type": 0,
                "units": "%",
                "delay": "30s",
                "history": "7d",
                "trends": "365d",
                "params": "100-last(//system.cpu.util[,idle])",
            },
        ),
        "netology.ram.util": ensure_item(
            auth,
            custom_templateid,
            {
                "name": "RAM utilization",
                "key_": "netology.ram.util",
                "type": 15,
                "value_type": 0,
                "units": "%",
                "delay": "30s",
                "history": "7d",
                "trends": "365d",
                "params": "100-last(//vm.memory.size[pavailable])",
            },
        ),
    }

    hostids = {}
    for host, dns in HOSTS.items():
        hostids[host] = ensure_host(
            auth,
            host,
            dns,
            linux_group["groupid"],
            linux_template["templateid"],
            custom_templateid,
        )

    latest = wait_for_values(auth, list(hostids.values()))
    itemids = {host: {} for host in HOSTS}
    for row in latest:
        host = next(name for name, hostid in hostids.items() if hostid == row["hostid"])
        itemids[host][row["key_"]] = row["itemid"]

    graphids = {
        host: ensure_graph(auth, hostids[host], host, itemids[host])
        for host in HOSTS
    }
    dashboardid = ensure_dashboard(auth, graphids)

    hosts_evidence = rpc(
        "host.get",
        {
            "output": ["hostid", "host", "name", "status", "available", "error"],
            "hostids": list(hostids.values()),
            "selectInterfaces": ["interfaceid", "dns", "ip", "port", "available", "error"],
            "selectParentTemplates": ["templateid", "host", "name"],
        },
        auth,
    )
    template_evidence = rpc(
        "template.get",
        {
            "output": ["templateid", "host", "name"],
            "templateids": custom_templateid,
            "selectItems": ["itemid", "name", "key_", "units", "delay", "preprocessing"],
        },
        auth,
    )
    dashboard_evidence = rpc(
        "dashboard.get",
        {"output": "extend", "dashboardids": dashboardid, "selectPages": "extend"},
        auth,
    )

    evidence = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "zabbix_version": version,
        "template": template_evidence,
        "hosts": hosts_evidence,
        "latest_data": latest,
        "graphs": graphids,
        "dashboard": dashboard_evidence,
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"templateid": custom_templateid, "hostids": hostids, "dashboardid": dashboardid, "latest_values": len(latest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
