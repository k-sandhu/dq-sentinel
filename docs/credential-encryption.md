# Connection credential encryption

Supply a unique Fernet key in `DQ_ENCRYPTION_KEY` to both API and worker from a secret manager. Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Preserve it separately from metadata backups: without the matching key the credentials cannot be recovered. Production requires a valid key. Development without a key requires the explicit `DQ_ALLOW_PLAINTEXT_DSNS=true` opt-in and stores credentials unencrypted.

On startup, legacy plaintext connection DSNs are encrypted in one transaction. Existing ciphertext is authenticated first; incorrect keys and unsupported formats abort without modifying rows. Database backups and old database pages may still contain plaintext; protect and retire them according to your retention policy.

## Rotation and recovery

Stop **all** API and worker processes and prevent writes to the metadata DB. Take a restorable metadata backup and preserve its matching old key securely. Generate a new key in your secret manager; provide the old key as `DQ_ENCRYPTION_KEY` and the new key as `DQ_NEW_ENCRYPTION_KEY` to an offline process, without putting either in shell history or arguments. From `backend`, run `python -m app.rotate_credentials`. The operation authenticates every row and commits all replacements together; on failure the transaction rolls back. Set `DQ_ENCRYPTION_KEY` to the new key for every API and worker, remove `DQ_NEW_ENCRYPTION_KEY`, and restart them. Verify connectivity before retiring the old key. Keep old keys as long as backups encrypted with them are retained. To restore a backup, stop all processes, restore the database and its matching key, then restart; never overwrite a key until the corresponding backup has been verified.

Fernet provides authenticated encryption; see [pyca documentation](https://cryptography.io/en/stable/fernet/).

For PostgreSQL, set `DQ_DATABASE_URL` to the metadata database URL and run the same command once in a single backend process with the two key environment variables supplied securely. It opens only the metadata database, takes a transaction-level advisory lock and an exclusive lock on its `connections` table, and never connects to source databases. Keep all API and worker processes stopped throughout rotation.
