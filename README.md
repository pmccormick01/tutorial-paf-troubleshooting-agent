# Build an AI DevOps Agent with Oracle Private Agent Factory and OCI DevOps

**Hands-on tutorial | Oracle Private Agent Factory (PAF) 26.7 | OCI DevOps | OCI Logging | MCP SDK 2.x | Python**

Build a simple CI pipeline for a Python Order Service, then use an AI agent in Oracle Private Agent Factory to investigate builds through the Model Context Protocol (MCP). The agent retrieves real build history, status, and execution logs, diagnoses test failures, and recommends fixes for a developer to review and validate.

> **Scope:** This is a learning/demo project, not a production reference architecture. The agent is **read-only**: it does not modify source code, trigger remediation, or deploy changes automatically. The video focuses on OCI DevOps and PAF Agent Builder; MCP deployment, IAM, certificates, and networking are documented here as supporting setup.

## Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Files and repository hygiene](#files-and-repository-hygiene)
4. [Create the DevOps project and repository](#1-create-the-devops-project-and-repository)
5. [Prepare and push the Order Service](#2-prepare-and-push-the-order-service)
6. [Create the build pipeline](#3-create-the-build-pipeline)
7. [Enable OCI Logging](#4-enable-oci-logging)
8. [Configure MCP access and deployment](#5-configure-mcp-access-and-deployment)
9. [Connect MCP to PAF](#6-connect-mcp-to-paf)
10. [Build the PAF agent workflow](#7-build-the-paf-agent-workflow)
11. [Run the demonstration](#8-run-the-demonstration)
12. [Troubleshooting and operations](#troubleshooting-and-operations)
13. [Security and publication checklist](#security-and-publication-checklist)

## Architecture

```text
Developer / Git push
       |
       v
OCI DevOps Code Repository (tutorial-order-service)
       |
       v
OCI DevOps Managed Build (tutorial-order-service-pipeline)
  - install dependencies
  - run 7 pytest tests
       |
       +----> OCI Logging (build execution records)

Developer asks PAF Agent Builder workflow to investigate
       |
       v
PAF Agent (Gemini 2.5 Flash)
       |
       | HTTPS / Streamable HTTP MCP
       v
Python MCP server on OCI Compute (rootless Podman)
  - list_recent_builds
  - get_build_status
  - get_build_logs
       |
       | OCI Python SDK / instance principal
       v
OCI DevOps APIs + OCI Logging Search
       |
       v
Agent explains evidence, probable cause, fix and verification steps
       |
       v
Developer reviews fix, commits it, reruns pipeline
```

The MCP server does **not** inspect repository source code. It infers likely defects from build metadata and log evidence; verify the proposed code change against the actual application.

## Prerequisites

- OCI tenancy with permissions for DevOps, Code Repositories, Managed Build, Logging, IAM, and Compute; choose a compartment and region (this tutorial used **Chicago / `us-chicago-1`**).
- Oracle Private Agent Factory **26.7** already running, with administrator access and an available model (the demo used **Gemini 2.5 Flash**). PAF's backing database must be running.
- OCI Compute instance hosting the existing MCP server; rootless **Podman**, outbound connectivity as needed, and permission to use **instance principals**.
- An MCP server exposing the three tools above, accessible from PAF over **HTTPS**. Instructions follow if recreating it.
- Local machine with Git, Python, a code editor, and OCI DevOps HTTPS Git credentials (usually an **OCI auth token**, not an API signing key).
- Working Order Service files: `app.py`, `tests/test_orders.py`, `build_spec.yaml`, `requirements.txt`, and `requirements-dev.txt`.

### Resource names used in the recording

| Resource | Tutorial example |
|---|---|
| DevOps project | `paf-devops-tutorial` |
| OCI-hosted code repository | `tutorial-order-service` |
| Build pipeline | `tutorial-order-service-pipeline` |
| Managed build stage | `build-and-test` |
| Log group | `paf-devops-tutorial-logs` |
| Build execution log | `tutorial-build-logs` |
| MCP container | `ai-devops-mcp` |
| PAF workflow | `AI DevOps Tutorial Workflow` |

Replace all placeholders in this README with **your own** compartment, resource OCIDs, hostname, and file paths. Never copy resource IDs from someone else's tenancy.

## Files and repository hygiene

The **OCI DevOps application repository** needs only:

```text
tutorial-order-service/
├── app.py
├── build_spec.yaml
├── requirements.txt
├── requirements-dev.txt
├── tests/
│   └── test_orders.py
├── README.md
└── .gitignore
```

Supporting MCP files may live in a separate directory or an ignored local folder:

```text
mcp-server/
├── server.py
├── Containerfile
├── requirements.txt
├── .containerignore
└── certs/               # local only; never commit private keys
```

If you have `config-tests/`, keep those diagnostics outside the application test discovery path or run `pytest tests/` explicitly. In the recorded project, some diagnostics import `oci` and `mcp` and are **not** Order Service unit tests.

Suggested application `.gitignore`:

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.DS_Store
.env
*.env
certs/
config-tests/
mcp-server/
*.key
*.pem
```

**Never commit** `ca.key`, `server.key`, SSH private keys, tokens, PAF session cookies, or real environment files. Public `.crt` files are not secrets, but excluding the whole `certs/` folder is simpler. `.gitignore` does not untrack previously committed files.

## 1. Create the DevOps project and repository

1. In OCI Console, choose the intended region and compartment.
2. Navigate to **Developer Services → DevOps → Projects → Create DevOps project**.
3. Name the project `paf-devops-tutorial`, enter a description, and select the compartment. **Notifications are optional** and were not needed in this demo.
4. Open the project → **Code Repositories → Create repository**. Name it `tutorial-order-service`. Do **not** initialize with a README if you'll push an existing local README.
5. Open the repository details and copy the **HTTPS clone URL**.
6. On your laptop, run:

   ```bash
   cd ~/Desktop/repos
   git clone '<YOUR_OCI_DEVOPS_HTTPS_CLONE_URL>'
   cd tutorial-order-service
   ```

If prompted, use the OCI DevOps Git username format shown by your repository and an OCI **auth token**. macOS Keychain or another credential helper may remember credentials after the first clone. An empty repository may report no commits yet; that is expected.

## 2. Prepare and push the Order Service

Copy your prepared application files into the cloned repository. The application is a lightweight FastAPI order API with a product catalog, stock validation, order creation, and percentage discounts. It is not backed by a separate application database.

The correct discount function is:

```python
def calculate_total(subtotal: float, discount: float) -> float:
    return round(subtotal * (1 - discount), 2)
```

The corresponding test includes:

```python
def test_discount():
    assert calculate_total(100.0, 0.10) == 90.0
```

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -v
```

**Expected baseline:** `7 passed`. If pytest tries to collect `config-tests/test_*.py`, specify `tests/` as above (or configure `pytest.ini` with `testpaths = tests`).

Stage only intended application files:

```bash
git add app.py build_spec.yaml requirements.txt requirements-dev.txt tests/test_orders.py README.md .gitignore
git diff --cached --name-only
git commit -m "Add Order Service application and tests"
git push -u origin main
```

Return to OCI DevOps → Code Repositories and confirm the files appear on `main`.

## 3. Create the build pipeline

1. Open **DevOps → Projects → `paf-devops-tutorial` → Build Pipelines**.
2. Select **Create build pipeline**, name it `tutorial-order-service-pipeline`, and create it.
3. Add a **Managed Build** stage named `build-and-test`.
4. Select a compatible managed build image/runner. The original successful demo used **Python 3.11** in its managed build environment; verify the runtime supported by your image and `build_spec.yaml`.
5. Configure the **primary code repository source** (in the pipeline source configuration if not on the stage form): repository `tutorial-order-service`, branch `main`.
6. Set the build spec path to `build_spec.yaml` or use the default root-location lookup, according to the console form. Save the stage and pipeline.
7. Check the **actual committed** `build_spec.yaml`: it should install both dependency sets and execute `python -m pytest tests/ -v` (or equivalent). A nonzero pytest exit code must fail the stage.

> This demo uses **manual build runs**. Automatic repository triggers and deployment stages are optional enhancements, not requirements.

## 4. Enable OCI Logging

Build execution output is generated by OCI DevOps; for this MCP implementation, make it searchable through **OCI Logging**.

1. In **Observability & Management → Logging → Log Groups**, create `paf-devops-tutorial-logs` in the tutorial compartment.
2. Enable the relevant **DevOps build execution resource log** for your pipeline/build resource. Select the new log group and set the log name to `tutorial-build-logs`.
3. **Check the selected resource and log category**. Merely naming a log `tutorial-build-logs` does not make it a build-execution log; project event logs are not necessarily equivalent.
4. Run a build and confirm that the log contains build-step messages and pytest output. Record the **log group OCID** and **log OCID** from OCI Logging.
5. Confirm the build run includes a `buildRunId` in its searchable log records, because the MCP `get_build_logs` implementation filters on that field.

You do **not** create a separate log for every build run. One enabled build log can hold records for multiple runs.

## 5. Configure MCP access and deployment

**If the existing MCP server is already running, skip directly to [5.4](#54-point-the-server-at-your-tutorial-resources).** The video deliberately does not demonstrate Podman, TLS generation, or IAM setup.

### 5.1 OCI IAM and instance principals

The MCP server uses the OCI Compute instance identity, not a downloaded OCI API signing key:

```python
signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
```

Create or reuse a dynamic group whose matching rule selects your MCP Compute instance, for example:

```text
instance.id = '<YOUR_COMPUTE_INSTANCE_OCID>'
```

Grant the dynamic group read access to the required DevOps and Logging resources in the appropriate compartment. The following are **illustrative policy patterns**; adapt domain-qualified group names and permissions to your tenancy, and validate against OCI IAM documentation:

```text
Allow dynamic-group <DOMAIN>/<GROUP_NAME> to read devops-family in compartment <COMPARTMENT_NAME>
Allow dynamic-group <DOMAIN>/<GROUP_NAME> to read log-groups in compartment <COMPARTMENT_NAME>
Allow dynamic-group <DOMAIN>/<GROUP_NAME> to read log-content in compartment <COMPARTMENT_NAME>
```

Confirm actual API calls work; successful signer creation alone does not establish that the instance is authorized to list builds or search logs.

### 5.2 MCP project files and container image

On the OCI Compute instance, use **rootless Podman** as the same Linux user that owns the PAF container. Do not switch between `podman` and `sudo podman`; they use different container stores.

Example `requirements.txt` for the MCP server (use versions compatible with the tested source):

```text
mcp>=2,<3
oci>=2,<3
uvicorn
```

Example `Containerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY server.py .
CMD ["python", "server.py"]
```

Example `.containerignore`:

```text
certs/
.venv/
.env
*.env
__pycache__/
*.pyc
```

The working MCP SDK 2.x server uses `MCPServer` from `mcp.server.mcpserver` (not the removed SDK 1.x `FastMCP` import), with `streamable_http_app()` and Uvicorn. It exposes:

| Tool | Purpose |
|---|---|
| `list_recent_builds(limit=10)` | List recent executions belonging to the configured pipeline |
| `get_build_status(build_run_id)` | Validate the run belongs to the configured pipeline; return lifecycle state |
| `get_build_logs(build_run_id, ...)` | Query OCI Logging Search for matching build-run records |

Use the **tested** `server.py` supplied with the MCP project; the table above is not a replacement implementation.

### 5.3 HTTPS and private networking

The demo uses an MCP server listening on `8765` **inside a Podman network**, not published on the Compute instance's public interface. The PAF container and MCP container share a DNS-enabled Podman network (`ai-devops-network`), and PAF accesses:

```text
https://ai-devops-mcp:8765/mcp
```

The TLS server certificate must have `DNS:ai-devops-mcp` in its Subject Alternative Name and be signed by a CA that PAF trusts. The working server mounts its certificate and private key at:

```text
/app/certs/server.crt
/app/certs/server.key
```

If creating a fresh demo CA, keep **`ca.key` off Git and preferably off the VM** after issuance. Keep `server.key` readable only by the container's required user. The CA public certificate `ca.crt` is what you use to establish trust; **never upload `ca.key` to PAF**.

Verify TLS from inside the PAF container (replace names as needed):

```bash
podman exec oracle-applied-ai-label \
  curl --cacert /tmp/ai-devops-ca.crt -i --max-time 10 \
  https://ai-devops-mcp:8765/mcp
```

This assumes you've copied `ca.crt` into that container at `/tmp/ai-devops-ca.crt`. A response like **HTTP 400 / Missing session ID** is acceptable for this bare `curl` request: it confirms TLS and routing but is **not** a complete MCP session test. An **HTTP 421 / Invalid Host header** indicates MCP host-header validation needs to allow `ai-devops-mcp:8765`.

The demo imported the CA public certificate into PAF's trust store using its administrator certificate-import API. The exact route, authorization requirements, and administrator UI can vary by PAF release; consult the matching PAF 26.7 certificate-management documentation before automating this step. Never publish administrator session cookies.

**PAF policy caveat:** The working demo required permitting private outbound destinations in PAF. Turning off **Block Private Outbound Urls** affects **all PAF workflows**, not just this MCP connection. Use this only in a dedicated, trusted demo environment after assessing the risk; keep **Allow Insecure Http Urls = No**. For shared or production environments, use a more narrowly controlled architecture instead.

### 5.4 Point the server at your tutorial resources

The current `server.py` reads OCI resource identifiers from environment variables. On the Compute instance, create a file named `paf-devops-tutorial.env` **outside Git**:

```dotenv
OCI_REGION=us-chicago-1
OCI_COMPARTMENT_ID=<YOUR_COMPARTMENT_OCID>
OCI_PIPELINE_NAME=tutorial-order-service-pipeline
OCI_BUILD_PIPELINE_ID=<YOUR_BUILD_PIPELINE_OCID>
OCI_LOG_GROUP_ID=<YOUR_LOG_GROUP_OCID>
OCI_LOG_ID=<YOUR_LOG_OCID>
MCP_HOST=0.0.0.0
MCP_PORT=8765
MCP_TLS_CERT=/app/certs/server.crt
MCP_TLS_KEY=/app/certs/server.key
```

Use `chmod 600 paf-devops-tutorial.env`. **Podman does not automatically read this file**; pass it with `--env-file` when creating the container. Rebuilding is necessary only if the Python source or image contents changed; changing the env file requires **recreating** the container.

On the Compute instance, after confirming the working image and source files are present:

```bash
cd ~/ai-devops-mcp
podman build -t localhost/ai-devops-mcp:1.4 .
podman stop ai-devops-mcp
podman rm ai-devops-mcp
podman run -d \
  --name ai-devops-mcp \
  --network ai-devops-network \
  --restart unless-stopped \
  --env-file ./paf-devops-tutorial.env \
  -v "$HOME/ai-devops-mcp/certs:/app/certs:ro,Z" \
  localhost/ai-devops-mcp:1.4
```

The network must already exist, and the PAF container must also be connected to it. The certificate mount assumes the host files exist at `~/ai-devops-mcp/certs/`. Reusing this container redirects the **original** PAF workflow to the tutorial pipeline too; keep that in mind if preserving the old demo matters.

Verify:

```bash
podman ps
podman logs --tail 30 ai-devops-mcp
podman exec ai-devops-mcp python -c \
  'import os; print(os.environ["OCI_PIPELINE_NAME"], os.environ["OCI_REGION"])'
```

Expect the tutorial pipeline name and `us-chicago-1`, plus successful HTTPS startup. Then test the MCP tools through PAF or a compatible MCP client.

## 6. Connect MCP to PAF

1. Open **PAF → MCP Servers**. If the existing `OCI DevOps MCP Server` is registered, reuse it.
2. Otherwise register an MCP server named `OCI DevOps MCP Server` with URL `https://ai-devops-mcp:8765/mcp` and **Direct** authentication (demo only).
3. Test the connection. It should succeed and discover the three tools. Direct authentication here means PAF does not send explicit MCP credentials; access still depends on your network and trust controls.
4. If PAF reports **private/non-routable destination blocked**, inspect its outbound URL policy. If it reports TLS errors, verify the CA trust and SAN. If it reports `Invalid Host header`, inspect the MCP SDK 2.x `TransportSecuritySettings.allowed_hosts` configuration.

## 7. Build the PAF agent workflow

1. Open **Agent Builder** and create **`AI DevOps Tutorial Workflow`**.
2. Add **Chat Input**, **Agent**, **Chat Output**, and **MCP Server** nodes.
3. Connect **Chat Input → Agent → Chat Output**, and connect the **MCP Server** node to the Agent's **Tools** input.
4. Configure the Agent with **Gemini 2.5 Flash** and a low temperature (the recorded demo used `0.01`).
5. Paste the project's **tested v1.2 troubleshooting instructions**. They should direct the agent to retrieve build evidence before diagnosing, identify the failed stage and tests, distinguish facts from inference, and provide root cause, recommended fix, verification steps, and confidence. The exact v1.2 text must be supplied separately if it is not included in your repository.
6. In the MCP Server node, select **`OCI DevOps MCP Server`** and allow `list_recent_builds`, `get_build_status`, and `get_build_logs`. Save the workflow.
7. Open the workflow chat and test with:

   > Retrieve the three most recent build runs from my tutorial Order Service pipeline using the available MCP tools. Include the build name, lifecycle status, and build run OCID. Do not invent results.

Check that the returned run IDs and names belong to the **tutorial** pipeline. If necessary, verify tool invocation in the MCP container logs.

## 8. Run the demonstration

### A. Baseline: successful build

1. Confirm `calculate_total()` uses `(1 - discount)`.
2. In OCI DevOps → `tutorial-order-service-pipeline`, choose **Start manual run** with the latest `main` commit.
3. Wait for `SUCCEEDED`; inspect the build logs for **7 passed**.
4. Ask the PAF agent:

   > Use the available MCP tools to retrieve the most recent build run from my Order Service pipeline. Report its name, status, and OCID, then retrieve the execution logs and summarize the test results. Base your response only on retrieved information.

### B. Intentionally introduce a test failure

In `app.py`, change **only** the discount function from:

```python
return round(subtotal * (1 - discount), 2)
```

to:

```python
return round(subtotal * (1 + discount), 2)
```

Commit the change to the **demo branch/repository only**:

```bash
git add app.py
git commit -m "Demo: introduce discount calculation bug"
git push origin main
```

Start another manual build. In the verified demo, pytest reported:

```text
FAILED tests/test_orders.py::test_discount      # expected 90.0, got 110.0
FAILED tests/test_orders.py::test_create_order  # expected 900.0, got 1100.0
2 failed, 5 passed
```

### C. Let the agent investigate

Start a **new PAF chat** and ask (without pasting logs or revealing the bug):

> Investigate the most recent failed build in my Order Service CI/CD pipeline. Use the available MCP tools to retrieve the actual build status and execution logs. Identify the failed stage, summarize the test failures, determine whether they share a common root cause, and recommend the exact code correction. Include verification steps and a confidence level. Base your diagnosis only on retrieved evidence.

The agent should identify the unit-test failure, correlate the two discount-related assertions, and infer that discount handling is increasing rather than decreasing the total. Treat the precise code change as a **recommendation** until a developer inspects `app.py`.

Verify MCP tool execution on the Compute instance:

```bash
podman logs --since 10m ai-devops-mcp 2>&1 | grep 'MCP TOOL'
```

The original end-to-end test confirmed all three tool invocations and retrieved 123 log records for the failed build. The number of records can vary.

### D. Apply and verify the fix

Restore `(1 - discount)`, then:

```bash
python -m pytest tests/ -v
git add app.py
git commit -m "Fix discount calculation identified by AI DevOps agent"
git push origin main
```

Run the pipeline again. The expected result is **7 passed** and **SUCCEEDED**. This completes the demonstration: **successful baseline → intentional failure → MCP log retrieval → AI diagnosis → developer fix → successful rebuild**.

## Troubleshooting and operations

| Symptom | Likely cause / action |
|---|---|
| `No module named mcp` or MCP SDK 2.x import error | Wrong Python environment or SDK version; use the Python 3.12 container and `MCPServer` SDK 2.x API. |
| `KeyError: OCI_PIPELINE_NAME` | Missing environment variable; inspect `paf-devops-tutorial.env`, then recreate container with `--env-file`. |
| `FileNotFoundError` from `load_cert_chain` | Uvicorn TLS paths do not match the `/app/certs` volume mount. |
| `HTTP 421 Invalid Host header` | Allow `ai-devops-mcp:8765` in MCP transport security settings; don't disable host checks globally. |
| `curl: (77)` | CA file missing or unreadable at the specified path inside the calling container. |
| `HTTP 400 Missing session ID` on bare curl | Endpoint is reachable, but a raw GET is not a complete MCP protocol exchange. |
| PAF blocks private/non-routable URL | PAF private-outbound policy; changing it is application-wide and should be assessed carefully. |
| MCP tools return no tutorial builds | Check pipeline OCID, compartment OCID, region, IAM and whether a run exists. |
| MCP logs empty after `grep` | No tool calls within the selected `--since` window, or wrong container/image. Run a fresh agent query. |
| Build `pip` fails with `ProxyError` | Network/proxy/DNS access to package index; `No matching distribution` may be a secondary symptom. |
| PAF page hangs after VM restart | Confirm database is up, then inspect/restart the **rootless** PAF Podman container as needed. |

Useful commands (run on the Compute instance as the Podman-owning user):

```bash
podman ps -a
podman start oracle-applied-ai-label
podman start ai-devops-mcp
podman logs --tail 40 ai-devops-mcp
podman logs --since 10m ai-devops-mcp 2>&1 | grep 'MCP TOOL'
podman logs -f ai-devops-mcp 2>&1 | grep --line-buffered 'MCP TOOL'
```

`podman logs -f` only follows output; **Ctrl+C** stops following, not the container. Use `podman ps`, **not** `sudo podman ps`, for the rootless containers.

## Security and publication checklist

- [ ] No private keys, certificates directory, credentials, auth tokens, session cookies, or real `.env` files are tracked by Git.
- [ ] The README and source use placeholder OCIDs and addresses rather than your tenancy's resource identifiers.
- [ ] `.containerignore` excludes certificate and environment files from the image build context.
- [ ] MCP tools remain read-only; build IDs are validated against the configured pipeline.
- [ ] The TLS certificate hostname matches the internal MCP DNS name; PAF trusts the issuing CA.
- [ ] Any application-wide private-outbound policy relaxation is clearly identified as **demo-only**.
- [ ] The recording does not reveal SSH keys, PAF cookies, sensitive logs, or private configuration.
- [ ] The final demo application is restored to its working discount calculation.
- [ ] Documentation reflects the actual files and OCI Console settings used in your environment.

## Further reading

- [OCI DevOps documentation](https://docs.oracle.com/en-us/iaas/Content/devops/using/home.htm)
- [OCI Logging documentation](https://docs.oracle.com/en-us/iaas/Content/Logging/home.htm)
- [OCI Python SDK documentation](https://docs.oracle.com/en-us/iaas/tools/python/latest/)
- [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk)

---

**Takeaway:** OCI DevOps detects build failures through automated testing. Oracle Private Agent Factory, using MCP tools, helps developers retrieve the evidence and diagnose what went wrong—while the developer remains in control of applying and validating the fix.
