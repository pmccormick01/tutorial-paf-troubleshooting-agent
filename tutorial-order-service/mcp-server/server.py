"""OCI DevOps MCP server (demo) - MCP SDK 2.x, HTTPS, tool-call logging."""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import oci
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from oci.exceptions import ServiceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ai-devops-mcp")

# Values are supplied by Podman using --env-file paf-devops-tutorial.env.
# Fail immediately if a required OCI resource identifier is missing.
REGION = os.environ.get("OCI_REGION", "us-chicago-1")
COMPARTMENT_ID = os.environ["OCI_COMPARTMENT_ID"]
PIPELINE_ID = os.environ["OCI_BUILD_PIPELINE_ID"]
PIPELINE_NAME = os.environ.get("OCI_PIPELINE_NAME", "tutorial-order-service-pipeline")
LOG_GROUP_ID = os.environ["OCI_LOG_GROUP_ID"]
LOG_ID = os.environ["OCI_LOG_ID"]

MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))
TLS_CERT = os.environ.get("MCP_TLS_CERT", "/app/certs/server.crt")
TLS_KEY = os.environ.get("MCP_TLS_KEY", "/app/certs/server.key")


mcp = MCPServer(
    "oci-devops-troubleshooting",
    description="Read-only OCI DevOps build investigation",
    version="1.4.0",
)

BUILD_ID_PATTERN = re.compile(
    rf"^ocid1\.devopsbuildrun\.oc1\.{re.escape(REGION)}\.[a-z0-9]+$"
)


def devops_client():
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    return oci.devops.DevopsClient({"region": REGION}, signer=signer)


def log_client():
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    return oci.loggingsearch.LogSearchClient({"region": REGION}, signer=signer)


def validate_build(client, build_run_id):
    if not BUILD_ID_PATTERN.fullmatch(build_run_id):
        raise ValueError("Invalid build-run OCID")
    build = client.get_build_run(build_run_id).data
    if build.build_pipeline_id != PIPELINE_ID:
        raise ValueError("Build does not belong to the configured pipeline")
    return build


def redact(message):
    text = str(message)
    text = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:password|api[_-]?key|secret|token)\s*[=:]\s*)\S+",
        r"\1[REDACTED]",
        text,
    )
    return text[:4000]


def error_result(error):
    if isinstance(error, ServiceError):
        return {"error": "OCI_API_ERROR", "status": error.status, "code": error.code}
    if isinstance(error, ValueError):
        return {"error": "INVALID_REQUEST", "message": str(error)}
    return {"error": "TOOL_ERROR", "message": "Operation failed"}


@mcp.tool()
def list_recent_builds(limit: int = 10) -> dict:
    """List recent executions of the Order Service pipeline."""
    logger.info("MCP TOOL CALLED: list_recent_builds")
    try:
        limit = max(1, min(int(limit), 25))
        client = devops_client()
        builds = []
        page = None
        for _ in range(10):
            kwargs = {"compartment_id": COMPARTMENT_ID, "limit": 100}
            if page:
                kwargs["page"] = page
            response = client.list_build_runs(**kwargs)
            builds.extend(
                build for build in response.data.items
                if build.build_pipeline_id == PIPELINE_ID
            )
            page = response.headers.get("opc-next-page")
            if not page:
                break
        builds.sort(
            key=lambda b: b.time_created or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        result = {
            "pipeline": PIPELINE_NAME,
            "builds": [
                {
                    "id": b.id,
                    "name": b.display_name,
                    "status": b.lifecycle_state,
                    "created": b.time_created.isoformat() if b.time_created else None,
                }
                for b in builds[:limit]
            ],
        }
        logger.info("MCP TOOL COMPLETED: list_recent_builds count=%d", len(result["builds"]))
        return result
    except Exception as error:
        logger.warning("MCP TOOL FAILED: list_recent_builds type=%s", type(error).__name__)
        return error_result(error)


@mcp.tool()
def get_build_status(build_run_id: str) -> dict:
    """Get the status of an Order Service build run."""
    logger.info("MCP TOOL CALLED: get_build_status")
    try:
        build = validate_build(devops_client(), build_run_id)
        result = {
            "id": build.id,
            "name": build.display_name,
            "status": build.lifecycle_state,
            "pipeline_id": build.build_pipeline_id,
            "created": build.time_created.isoformat() if build.time_created else None,
        }
        logger.info("MCP TOOL COMPLETED: get_build_status status=%s", build.lifecycle_state)
        return result
    except Exception as error:
        logger.warning("MCP TOOL FAILED: get_build_status type=%s", type(error).__name__)
        return error_result(error)


@mcp.tool()
def get_build_logs(
    build_run_id: str,
    hours_back: int = 168,
    max_records: int = 200,
) -> dict:
    """Retrieve chronological logs for an Order Service build run."""
    logger.info("MCP TOOL CALLED: get_build_logs")
    try:
        build = validate_build(devops_client(), build_run_id)
        hours_back = max(1, min(int(hours_back), 336))
        max_records = max(1, min(int(max_records), 300))
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=hours_back)
        query = (
            f'search "{COMPARTMENT_ID}/{LOG_GROUP_ID}/{LOG_ID}" '
            "| sort by datetime asc"
        )
        details = oci.loggingsearch.models.SearchLogsDetails(
            search_query=query,
            time_start=start_time,
            time_end=end_time,
            is_return_field_info=False,
        )
        client = log_client()
        records = []
        page = None
        exhausted = False
        for _ in range(20):
            kwargs = {"search_logs_details": details, "limit": 1000}
            if page:
                kwargs["page"] = page
            response = client.search_logs(**kwargs)
            for item in response.data.results or []:
                entry = oci.util.to_dict(item.data)
                content = (entry.get("logContent") or {}).get("data") or {}
                if content.get("buildRunId") != build_run_id:
                    continue
                records.append({
                    "time": entry.get("time"),
                    "message": redact(content.get("message", "")),
                })
                if len(records) >= max_records:
                    break
            if len(records) >= max_records:
                break
            page = response.headers.get("opc-next-page")
            if not page:
                exhausted = True
                break
        records.sort(key=lambda record: record["time"] or "")
        logger.info("MCP TOOL COMPLETED: get_build_logs count=%d", len(records))
        return {
            "build_run_id": build_run_id,
            "build_status": build.lifecycle_state,
            "records": records,
            "record_count": len(records),
            "possibly_truncated": len(records) >= max_records or not exhausted,
            "search_window_hours": hours_back,
        }
    except Exception as error:
        logger.warning("MCP TOOL FAILED: get_build_logs type=%s", type(error).__name__)
        return error_result(error)


if __name__ == "__main__":
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            f"ai-devops-mcp:{MCP_PORT}",
            f"localhost:{MCP_PORT}",
            f"127.0.0.1:{MCP_PORT}",
        ],
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        transport_security=security,
        host=MCP_HOST,
    )
    uvicorn.run(
        app,
        host=MCP_HOST,
        port=MCP_PORT,
        ssl_certfile=TLS_CERT,
        ssl_keyfile=TLS_KEY,
    )
