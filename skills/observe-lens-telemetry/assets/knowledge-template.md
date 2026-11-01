# Telemetry knowledge notes

Write down what an agent cannot guess about your observability setup.

## Datasources

- Datasource names/uids for logs, metrics and traces per environment.

## Labels

- Label names used for namespace, app/service, pod and level.
- Which labels carry request/order/correlation ids.

## Services

- Service names as they appear in Loki/Tempo/Prometheus.

## Useful queries

- Known-good LogQL / PromQL / TraceQL for common questions.

## Retention and sampling

- How long logs/traces are kept; any tail sampling that hides traces.
