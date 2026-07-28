# Monitoring

This document defines observability for ProSeqGO: what is monitored, where to look, and how alerts map to operational response.

For dashboard export workflows and detailed PromQL examples, see also [monitoring/README.md](../monitoring/README.md).

## Observability stack

| Component | Role | Access |
|-----------|------|--------|
| Prometheus | Metrics collection, alert evaluation | `http://localhost:9090` |
| Grafana | Dashboards, visualization | `http://localhost:3000` (default `admin`/`admin`) |
| redis-exporter | Redis health and queue signals | Scraped by Prometheus (monitoring profile) |

Monitoring is **profile-based** and isolated from the public NGINX ingress. Start with:

```bash
make monitoring-up
```

## Service health model

Health is determined by Prometheus scrape targets (`up` metric), not JSON `/health` endpoints.

| Job name | Target | Port |
|----------|--------|------|
| `prometheus` | `localhost:9090` | 9090 |
| `embedding_api_metrics` | `embedding-api` | 8000 |
| `embedding_worker_metrics` | `embedding-worker` | 8001 |
| `go_prediction_api_metrics` | `go-prediction-api` | 8000 |
| `trainer_api_metrics` | `trainer-api` | 8000 (training profile) |
| `trainer_worker_metrics` | `trainer-worker` | 8001 (training profile) |
| `redis_exporter` | `redis-exporter` | 9121 |

**Healthy:** `up == 1` for all expected jobs given active profiles.

**Note:** `trainer_*` targets are only expected when the training profile is running. Absence is normal otherwise.

## Metrics catalog

### HTTP metrics (all APIs)

| Metric family | Labels | Purpose |
|---------------|--------|---------|
| `http_requests_total` | `service`, `method`, `route`, `status_code` | Request volume and errors |
| `http_request_duration_seconds` | `service`, `method`, `route` | Latency histogram |
| `http_requests_in_flight` | `service` | Concurrency |

Route labels are normalized to static templates (no raw UUIDs) to avoid cardinality explosion.

### Embedding pipeline

| Metric | Purpose |
|--------|---------|
| `embedding_queue_jobs` | Postgres queue depth by status |
| `rq_queue_length{queue="embedding-jobs"}` | Redis RQ queue length |
| Embedding job duration / outcome counters | Pipeline throughput and failures |

### Training pipeline (training profile)

| Metric | Purpose |
|--------|---------|
| Training queue depth | Backlog |
| Training job duration by mode | Retrain latency |
| Failure reason counters | Debug failed jobs |

### Inference

| Metric | Purpose |
|--------|---------|
| `inference_duration_seconds` | Latency by `model_version` |
| Validation failure counters | Embedding dimension / schema drift |
| `top_k` distribution | Usage patterns |

### Redis

| Metric | Purpose |
|--------|---------|
| `redis_up` | Redis availability |

## Dashboards

Provisioned from `monitoring/grafana/dashboards/`:

| Dashboard | File | Focus |
|-----------|------|-------|
| CAFA5 Service Health | `service-health.json` | Target up/down, request rate, 5xx ratio, p95 latency, in-flight |
| CAFA5 Domain Pipelines | `domain-pipelines.json` | Embedding/training queues, inference by model version, validation failures |

Grafana datasource UID: `prometheus` → `http://prometheus:9090`.

**Start here during incidents:** Service Health dashboard, then Domain Pipelines if embedding or inference is involved.

## Alerts

Rules: [`monitoring/alerts.yml`](../monitoring/alerts.yml)

| Alert | Condition | Severity | Meaning |
|-------|-----------|----------|---------|
| `ProSeqGOServiceMetricsTargetDown` | `up == 0` for >2m | critical | Scrape target unreachable |
| `ProSeqGOHighHttp5xxRatio` | 5xx ratio >5% for 10m with traffic floor | warning | User-visible API errors |
| `ProSeqGOEmbeddingQueueBacklogHigh` | queued jobs >20 for 10m | warning | Embedding pipeline saturated |
| `ProSeqGOEmbeddingWorkerDownWithBacklog` | worker down + RQ queue non-empty for 5m | critical | Jobs stuck with no worker |
| `ProSeqGORedisDown` | `redis_up == 0` for 2m | critical | Job dispatch broken |

Verify rules and firing alerts:

```bash
curl -s http://localhost:9090/-/ready
curl -s http://localhost:9090/api/v1/rules
curl -s http://localhost:9090/api/v1/alerts
```

**Production gap:** Alertmanager and external notifications (PagerDuty, Slack) are not wired in the default stack. Add Alertmanager for production paging.

## SLO guidance (recommended)

Define explicitly for your deployment; suggested starting points:

| SLI | Target |
|-----|--------|
| API availability | `up{job=~"embedding_api_metrics|go_prediction_api_metrics"} == 1` for 99.5% / 30d |
| Prediction p95 latency | < 5s for single embedding inference (embedding precomputed) |
| 5xx ratio | < 1% over 1h under normal load |
| Embedding queue | < 20 queued jobs 95% of time |

## Model observability

When rolling a new model version:

1. Confirm `model_version` label appears in inference metrics
2. Compare p95 latency by `model_version`
3. Watch validation failure reasons (embedding dimension mismatch is the most common)
4. Check 5xx ratio on `go-prediction-api`
5. Verify champion alias in MLflow matches expected version

## Runbook: first actions per alert

### `ProSeqGOServiceMetricsTargetDown`

1. `docker compose ps` — is the container running?
2. `docker compose logs --tail=200 <service>`
3. Confirm `/metrics` responds inside the Docker network (not via NGINX)
4. Restart affected service if crash-looping

### `ProSeqGOHighHttp5xxRatio`

1. Identify service from alert label
2. Check recent deploys or model promotions
3. Inspect logs for stack traces
4. Query 5xx by route in Prometheus/Grafana

### `ProSeqGOEmbeddingQueueBacklogHigh`

1. Check `embedding-worker` logs and GPU/CPU utilization
2. Scale workers or reduce inbound job rate
3. Inspect failed jobs in Postgres `proseqgo_jobs`

### `ProSeqGOEmbeddingWorkerDownWithBacklog`

1. Restart `embedding-worker`
2. Confirm crash recovery requeues orphaned jobs (see smoke test)
3. Investigate OOM or GPU errors in worker logs

### `ProSeqGORedisDown`

1. `docker compose ps redis`
2. `docker compose logs redis`
3. Restart Redis; verify RQ workers reconnect

## Reload and maintenance

**Prometheus config/rules change:**

```bash
curl -X POST http://localhost:9090/-/reload
# or
docker compose restart prometheus
```

**Grafana dashboard JSON change:** auto-refresh via provisioning; restart Grafana if panels do not update.

**Retention:** Prometheus TSDB retention 15 days (`--storage.tsdb.retention.time=15d` in compose).

## Useful PromQL queries

Service availability:

```promql
up{job=~"prometheus|embedding_api_metrics|go_prediction_api_metrics|trainer_api_metrics"}
```

HTTP 5xx ratio by service:

```promql
sum by (service) (rate(http_requests_total{status_code=~"5.."}[5m]))
/
clamp_min(sum by (service) (rate(http_requests_total[5m])), 0.001)
```

Embedding queue depth:

```promql
cafa5_embedding_queue_jobs{status="queued"}
```

Inference p95 by model version:

```promql
histogram_quantile(
  0.95,
  sum by (le, model_version) (rate(inference_duration_seconds_bucket[5m]))
)
```

## Stop monitoring

```bash
make monitoring-down
```

## Related documentation

- [troubleshooting.md](troubleshooting.md) — extended diagnostic steps
- [architecture.md](architecture.md) — which services expose metrics
- [deployment.md](deployment.md) — starting the monitoring profile
