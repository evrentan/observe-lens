# MongoDB / DocumentDB notes

`db.py mongo '<expression>'` evaluates a guarded read-only mongosh
expression. Allowed: `db.getCollectionNames()`, `db.getCollectionInfos()`,
`db.stats()`, `db.<coll>.find|findOne|countDocuments|estimatedDocumentCount|
distinct|aggregate|getIndexes|stats(...)`, and
`db.getCollection("<coll>").<m>(...)`, chained only with
limit/sort/skip/project/toArray/count/hint/maxTimeMS/batchSize/explain.
`find` gets `.limit(100)` appended automatically unless present.

## Examples

```
db.orders.find({orderId: "A-123"}).limit(5)
db.orders.find({status: "FAILED", createdAt: {$gte: ISODate("2026-01-15T09:00:00Z")}})
db.orders.countDocuments({status: "PENDING"})
db.orders.distinct("status")
db.orders.aggregate([{$match: {status: "FAILED"}}, {$group: {_id: "$errorCode", n: {$sum: 1}}}])
db.orders.getIndexes()
```

`$out`, `$merge`, `$function`, `$accumulator`, `$where`, eval, runCommand,
getSiblingDB and every write method are refused.

## AWS DocumentDB notes

DocumentDB speaks the Mongo wire protocol but with differences:

- **TLS is required** on most clusters: set `"tls": true` and pass the
  region CA bundle via mongosh's normal TLS flags; when verification
  conflicts, `"options": {"tlsAllowInvalidCertificates": "true"}` works but
  prefer the real CA.
- **Auth**: `authMechanism` SCRAM-SHA-1 is common: add
  `"options": {"authMechanism": "SCRAM-SHA-1"}`.
- **retryWrites**: DocumentDB does not support them —
  `"options": {"retryWrites": "false"}`.
- Some APIs are missing (`getCollectionInfos` variants, `$$` features,
  `$lookup` limits); fall back to `find`/`aggregate` basics.
