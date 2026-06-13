from __future__ import annotations

import sys


def _write_server(path):
    path.write_text(
        "import json,sys\n"
        "def send(obj):\n"
        "    sys.stdout.write(json.dumps(obj)+chr(10))\n"
        "    sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    line=line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    msg=json.loads(line)\n"
        "    mid=msg.get('id')\n"
        "    meth=msg.get('method')\n"
        "    if meth=='initialize':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'protocolVersion':'2025-06-18','capabilities':{'tools':{},'resources':{},'prompts':{}},'serverInfo':{'name':'local','version':'1'}}})\n"
        "    elif meth=='notifications/initialized':\n"
        "        continue\n"
        "    elif meth=='tools/list':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'tools':[{'name':'read','description':'Read files','inputSchema':{'type':'object','properties':{}}},{'name':'write','description':'Write files','inputSchema':{'type':'object','properties':{}}}]}})\n"
        "    elif meth=='resources/list':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'resources':[{'uri':'note://a','name':'Note A'}]}})\n"
        "    elif meth=='prompts/list':\n"
        "        send({'jsonrpc':'2.0','id':mid,'result':{'prompts':[{'name':'review','description':'Review prompt'}]}})\n",
        encoding="utf-8",
    )


def test_mcp_catalog_install_probe_and_tool_checklist(tmp_path):
    from aegis.config import Config
    from aegis.mcp.client import (
        install_from_catalog,
        probe_server,
        save_tool_checklist,
        tool_checklist,
    )

    server = tmp_path / "mcp_server.py"
    _write_server(server)
    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["catalog"] = [{
        "name": "local",
        "command": sys.executable,
        "args": [str(server)],
        "description": "Local test server",
        "tool_filter": {"include": ["read"]},
    }]

    spec = install_from_catalog(cfg, "local")
    assert spec["command"] == sys.executable
    assert cfg.get("mcp.servers")["local"]["tool_filter"]["include"] == ["read"]

    probe = probe_server(cfg, "local")
    assert probe["ok"] is True
    assert probe["transport"] == "stdio"
    assert [tool["name"] for tool in probe["all_tools"]] == ["read", "write"]
    assert [tool["name"] for tool in probe["tools"]] == ["read"]
    assert probe["resources"][0]["uri"] == "note://a"
    assert probe["prompts"][0]["name"] == "review"

    checklist = tool_checklist(cfg, "local")
    selected = {item["name"]: item["selected"] for item in checklist["items"]}
    assert selected == {"read": True, "write": False}

    saved = save_tool_checklist(cfg, "local", ["write", "write", " "])
    assert saved["tool_filter"]["include"] == ["write"]
    selected = {item["name"]: item["selected"] for item in tool_checklist(cfg, "local")["items"]}
    assert selected == {"read": False, "write": True}


def test_mcp_empty_include_filter_surfaces_no_tools():
    from aegis.config import Config
    from aegis.mcp.client import _filter_tools, save_tool_checklist

    tools = [{"name": "read"}, {"name": "write"}]
    assert _filter_tools(tools, {"include": []}) == []

    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["servers"] = {
        "local": {"command": sys.executable, "args": ["-m", "server"]}
    }

    saved = save_tool_checklist(cfg, "local", [])
    assert saved["tool_filter"]["include"] == []
    assert _filter_tools(tools, saved["tool_filter"]) == []
