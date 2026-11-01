# Query cookbook

## LogQL (logs)

```
{namespace="shop", app="api"} |= "A-123"                      # id search
{namespace="shop", app="api"} |= "error" |~ "timeout|refused" # regex
{namespace="shop"} | json | level="error"                   # parse then filter
{app="api"} |~ "orderId[=:]\"?A-123"                        # loose id match
{app="api"} |= "exception" != "HealthCheck"                 # exclude noise
```

Rules: always a stream selector first, then `|=`/`|~` line filters, then
parsers. Keep `--limit` and a tight `--from/--to`.

## TraceQL (traces)

```
{trace:id = "abc123..."}
{resource.service.name = "api" && status = error}
{resource.service.name = "api" && duration > 2s}
{span.http.status_code = 500}
{resource.service.name = "api" && span.orderId = "A-123"}   # custom attrs
```

Find the trace with `traces '<TraceQL>'`, then read the tree with
`trace <id>` (`--errors-only` narrows to failing spans).

## PromQL (metric / metric-range)

```
sum by (pod) (kube_pod_container_status_restarts_total{namespace="shop"})
rate(http_requests_total{app="api",status=~"5.."}[5m])
sum(rate(http_requests_total{app="api"}[5m]))
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{app="api"}[5m])))
kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}
increase(errors_total{app="api"}[15m])
up{job="api"}
```

`metric` is an instant query (`--at` optional); `metric-range` prints
min/max/first/last per series, not every point.

## Comparing around a release

Query `metric-range 'rate(errors_total{app="api"}[5m])'` for the hour
before and after the deploy time from observe-lens-cd; a step change right
at the sync finish is the signal.
