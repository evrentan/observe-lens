# Database knowledge notes

Write down what an agent cannot guess about your schemas.

## Instances and schemas

- Which instance holds which data; which schema(s) matter per database.
- Which `users` entry reads which schema.

## Key tables / collections

- The tables that answer most questions (orders, customers, jobs, logs).
- Column names for ids and statuses; enum/status value meanings.

## Id formats

- What an order/customer/request id looks like and which column it maps to.

## Known quirks

- Big or slow tables to filter early, JSON columns, time-zone columns,
  tables partitioned by date.
