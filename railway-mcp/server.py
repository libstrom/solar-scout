"""
Railway MCP Server
Provides tools for managing Railway projects, deployments and variables.
Requires RAILWAY_API_TOKEN environment variable.
"""

import os
import sys
import json
import asyncio
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

RAILWAY_API = "https://backboard.railway.app/graphql/v2"
TOKEN = os.environ.get("RAILWAY_API_TOKEN", "")

app = Server("railway-mcp")


def gql(query: str, variables: dict = None) -> dict:
    resp = httpx.post(
        RAILWAY_API,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise ValueError(data["errors"][0]["message"])
    return data["data"]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_projects",
            description="List all Railway projects and their services",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_deployments",
            description="Get recent deployments for a service",
            inputSchema={
                "type": "object",
                "properties": {
                    "service_id": {"type": "string", "description": "Railway service ID"},
                    "limit": {"type": "integer", "description": "Number of deployments (default 5)", "default": 5},
                },
                "required": ["service_id"],
            },
        ),
        Tool(
            name="get_logs",
            description="Get build or deploy logs for a deployment",
            inputSchema={
                "type": "object",
                "properties": {
                    "deployment_id": {"type": "string", "description": "Railway deployment ID"},
                },
                "required": ["deployment_id"],
            },
        ),
        Tool(
            name="get_variables",
            description="Get environment variables for a service in a project",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "service_id": {"type": "string"},
                    "environment_id": {"type": "string"},
                },
                "required": ["project_id", "service_id", "environment_id"],
            },
        ),
        Tool(
            name="set_variable",
            description="Set (upsert) an environment variable for a service",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "service_id": {"type": "string"},
                    "environment_id": {"type": "string"},
                    "name": {"type": "string", "description": "Variable name"},
                    "value": {"type": "string", "description": "Variable value"},
                },
                "required": ["project_id", "service_id", "environment_id", "name", "value"],
            },
        ),
        Tool(
            name="set_variables_bulk",
            description="Set multiple environment variables at once",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "service_id": {"type": "string"},
                    "environment_id": {"type": "string"},
                    "variables": {
                        "type": "object",
                        "description": "Key-value pairs of variables to set",
                    },
                },
                "required": ["project_id", "service_id", "environment_id", "variables"],
            },
        ),
        Tool(
            name="redeploy",
            description="Trigger a redeployment of a service",
            inputSchema={
                "type": "object",
                "properties": {
                    "deployment_id": {"type": "string", "description": "ID of deployment to redeploy"},
                },
                "required": ["deployment_id"],
            },
        ),
        Tool(
            name="get_service_url",
            description="Get the public URL(s) for a Railway service",
            inputSchema={
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "environment_id": {"type": "string"},
                },
                "required": ["service_id", "environment_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict) -> dict:
    if name == "list_projects":
        return _list_projects()
    elif name == "get_deployments":
        return _get_deployments(args["service_id"], args.get("limit", 5))
    elif name == "get_logs":
        return _get_logs(args["deployment_id"])
    elif name == "get_variables":
        return _get_variables(args["project_id"], args["service_id"], args["environment_id"])
    elif name == "set_variable":
        return _set_variable(
            args["project_id"], args["service_id"],
            args["environment_id"], args["name"], args["value"]
        )
    elif name == "set_variables_bulk":
        return _set_variables_bulk(
            args["project_id"], args["service_id"],
            args["environment_id"], args["variables"]
        )
    elif name == "redeploy":
        return _redeploy(args["deployment_id"])
    elif name == "get_service_url":
        return _get_service_url(args["service_id"], args["environment_id"])
    else:
        raise ValueError(f"Unknown tool: {name}")


def _list_projects() -> dict:
    data = gql("""
    query {
      me {
        projects {
          edges {
            node {
              id
              name
              environments {
                edges { node { id name } }
              }
              services {
                edges {
                  node { id name }
                }
              }
            }
          }
        }
      }
    }
    """)
    projects = []
    for edge in data["me"]["projects"]["edges"]:
        p = edge["node"]
        projects.append({
            "id": p["id"],
            "name": p["name"],
            "environments": [e["node"] for e in p["environments"]["edges"]],
            "services": [s["node"] for s in p["services"]["edges"]],
        })
    return {"projects": projects}


def _get_deployments(service_id: str, limit: int) -> dict:
    data = gql("""
    query($serviceId: String!) {
      deployments(input: { serviceId: $serviceId }, first: 10) {
        edges {
          node {
            id
            status
            createdAt
            url
            meta
          }
        }
      }
    }
    """, {"serviceId": service_id})
    deployments = [e["node"] for e in data["deployments"]["edges"]][:limit]
    return {"deployments": deployments}


def _get_logs(deployment_id: str) -> dict:
    data = gql("""
    query($deploymentId: String!) {
      deploymentLogs(deploymentId: $deploymentId) {
        message
        severity
        timestamp
      }
    }
    """, {"deploymentId": deployment_id})
    return {"logs": data.get("deploymentLogs", [])}


def _get_variables(project_id: str, service_id: str, environment_id: str) -> dict:
    data = gql("""
    query($projectId: String!, $serviceId: String!, $environmentId: String!) {
      variables(projectId: $projectId, serviceId: $serviceId, environmentId: $environmentId)
    }
    """, {"projectId": project_id, "serviceId": service_id, "environmentId": environment_id})
    return {"variables": data.get("variables", {})}


def _set_variable(project_id: str, service_id: str, environment_id: str, name: str, value: str) -> dict:
    gql("""
    mutation($input: VariableUpsertInput!) {
      variableUpsert(input: $input)
    }
    """, {"input": {
        "projectId": project_id,
        "serviceId": service_id,
        "environmentId": environment_id,
        "name": name,
        "value": value,
    }})
    return {"ok": True, "name": name}


def _set_variables_bulk(project_id: str, service_id: str, environment_id: str, variables: dict) -> dict:
    results = []
    for name, value in variables.items():
        _set_variable(project_id, service_id, environment_id, name, str(value))
        results.append(name)
    return {"ok": True, "set": results}


def _redeploy(deployment_id: str) -> dict:
    data = gql("""
    mutation($id: String!) {
      deploymentRedeploy(id: $id) { id status }
    }
    """, {"id": deployment_id})
    return data["deploymentRedeploy"]


def _get_service_url(service_id: str, environment_id: str) -> dict:
    data = gql("""
    query($serviceId: String!, $environmentId: String!) {
      serviceInstance(serviceId: $serviceId, environmentId: $environmentId) {
        domains {
          serviceDomains { domain }
          customDomains { domain }
        }
      }
    }
    """, {"serviceId": service_id, "environmentId": environment_id})
    instance = data.get("serviceInstance", {})
    domains = instance.get("domains", {})
    return {
        "service_domains": [d["domain"] for d in domains.get("serviceDomains", [])],
        "custom_domains": [d["domain"] for d in domains.get("customDomains", [])],
    }


if __name__ == "__main__":
    if not TOKEN:
        print("Error: RAILWAY_API_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)
    asyncio.run(stdio_server(app))
