# DB setup

## Clients

| OS | postgres | mysql | mongodb |
| --- | --- | --- | --- |
| macOS | `brew install libpq` (add `$(brew --prefix)/opt/libpq/bin` to PATH) | `brew install mysql-client` | `brew install mongosh` |
| Linux | `postgresql-client` (Debian/RHEL names similar) | `mysql-client` or `mariadb-client` | MongoDB mongosh install page |
| Windows | run `winget search postgresql` or the official installer | `winget search mysql` or the official installer | `winget search mongosh` or the official installer |

`"client": "/full/path/to/psql"` overrides the PATH lookup per instance.

## Config fields

`engine` (postgres|mysql|mongodb), `host`, `port`, `database`, `user`,
`users` (all read-only users), `tunnel`, `environment`, `sslmode`
(postgres), `auth_source`, `tls`, `srv`, `options` (mongodb),
`description`, `client`. Top-level `tunnels`:

```json
"dev-bastion": {"ssh_host": "bastion-dev", "identity_file": "~/.ssh/id_ed25519",
 "port": 22, "forwards": [{"local_port": 15432, "remote_host": "db.internal",
 "remote_port": 5432}], "ssh_args": []}
```

## Read-only users (for the DBA)

```sql
-- postgres
CREATE USER shop_ro WITH PASSWORD '...';
GRANT CONNECT ON DATABASE shop TO shop_ro;
GRANT USAGE ON SCHEMA public TO shop_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO shop_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO shop_ro;

-- mysql
CREATE USER 'orders_ro'@'%' IDENTIFIED BY '...';
GRANT SELECT ON orders.* TO 'orders_ro'@'%';

-- mongodb (mongosh, admin db)
db.createUser({user: "catalog_ro", pwd: "...", roles: [{role: "read", db: "catalog"}]})
```

## SSH tunnels

`tunnel.py start <name>` runs `ssh -N -L <port>:<host>:<port>` detached —
it needs **key-based auth** (BatchMode cannot answer a password prompt).
Generate a key (`ssh-keygen -t ed25519`), install the public key on the
bastion, and load it:

- macOS/Linux: `eval "$(ssh-agent -s)"; ssh-add ~/.ssh/id_ed25519`
- Windows: `Get-Service ssh-agent | Set-Service -StartupType Automatic`,
  `Start-Service ssh-agent`, `ssh-add $HOME\.ssh\id_ed25519`

`tunnel.py status` checks whether the local ports accept connections;
`stop` kills only the pid it recorded.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `psql/mysql/mongosh not found` | install per the table above or set `client` |
| `publickey` in tunnel log | add key to bastion + ssh-agent |
| `address already in use` | a tunnel is already up — `tunnel.py status`, or another program holds the port |
| `password authentication failed` | secret missing — `config add` or `.pgpass`/`.my.cnf` |
| guard refused a read | check for a banned word inside the query; rewrite as SELECT |
| `SSL SYSCALL` / TLS errors | postgres `sslmode`, mongo `tls`/`options` |
