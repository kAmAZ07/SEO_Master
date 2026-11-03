<?php
declare(strict_types=1);
if (!defined('ABSPATH')) { exit; }

final class SM_V2_WPDB_Store implements SM_V2_Store {
    private array $tables;
    private bool $in_transaction = false;
    private array $locked_entities = [];
    private array $changed_entities = [];
    private const META = [
        'title' => '_seo_master_meta_title', 'description' => '_seo_master_meta_description', 'jsonld' => '_seo_master_schema_jsonld',
    ];

    public function __construct(private SM_V2_SQL $database, private Closure $invalidate_cache) {
        $this->tables = $database->tables();
        sm_v2_keys($this->tables, ['posts', 'meta', 'operations', 'nonces']);
        foreach ($this->tables as $table) {
            if (!is_string($table) || preg_match('/\A[A-Za-z0-9_]{1,64}\z/D', $table) !== 1) { throw new SM_V2_Error('unsupported_storage', 503); }
        }
        if (count(array_unique($this->tables)) !== 4) { throw new SM_V2_Error('unsupported_storage', 503); }
    }

    private function query(string $sql, array $identifiers = [], array $parameters = []): array|int {
        foreach ($identifiers as $identifier) {
            if (!in_array($identifier, $this->tables, true)) { throw new SM_V2_Error('unsupported_storage', 503); }
        }
        return $this->database->query($sql, $identifiers, $parameters);
    }

    public function check_storage(): void {
        $session = $this->query('SELECT @@SESSION.autocommit AS autocommit, @@SESSION.sql_mode AS sql_mode, @@GLOBAL.innodb_flush_log_at_trx_commit AS durable_commit');
        if (($session[0]['autocommit'] ?? null) != 1 || $this->database->character_set() !== 'utf8mb4' ||
            (int) ($session[0]['durable_commit'] ?? 0) !== 1 ||
            preg_match('/(?:^|,)(?:STRICT_TRANS_TABLES|STRICT_ALL_TABLES)(?:,|$)/', $session[0]['sql_mode'] ?? '') !== 1) {
            throw new SM_V2_Error('unsupported_storage', 503);
        }
        // MySQL/MariaDB reject this if a transaction is already active. No nested commit of another plugin's work.
        $this->query('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE');
        $this->check_tables();
    }

    private function check_tables(): void {
        $accounts = $this->query('SELECT CURRENT_USER() AS account');
        // Only directly granted, unambiguous accounts are supported; role-only/complex identities fail closed.
        if (count($accounts) !== 1 || preg_match('/\A([A-Za-z0-9_.-]{1,64})@([A-Za-z0-9_.:%-]{1,255})\z/D', $accounts[0]['account'] ?? '', $account) !== 1) {
            throw new SM_V2_Error('unsupported_storage', 503);
        }
        $grantee = "'" . $account[1] . "'@'" . $account[2] . "'";
        $global_grants = $this->query("SELECT PRIVILEGE_TYPE FROM information_schema.USER_PRIVILEGES WHERE GRANTEE = ? AND PRIVILEGE_TYPE = 'TRIGGER'", [], [$grantee]);
        $schema_grants = $this->query("SELECT PRIVILEGE_TYPE FROM information_schema.SCHEMA_PRIVILEGES WHERE GRANTEE = ? AND TABLE_SCHEMA = DATABASE() AND PRIVILEGE_TYPE = 'TRIGGER'", [], [$grantee]);
        foreach ($this->tables as $name => $table) {
            if ($global_grants === [] && $schema_grants === []) {
                $table_grants = $this->query("SELECT PRIVILEGE_TYPE FROM information_schema.TABLE_PRIVILEGES WHERE GRANTEE = ? AND TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND PRIVILEGE_TYPE = 'TRIGGER'", [], [$grantee, $table]);
                if ($table_grants === []) { throw new SM_V2_Error('unsupported_storage', 503); }
            }
            $rows = $this->query(
                'SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?',
                [], [$table]
            );
            if (count($rows) !== 1 || strtoupper($rows[0]['ENGINE'] ?? '') !== 'INNODB' ||
                !str_starts_with($rows[0]['TABLE_COLLATION'] ?? '', 'utf8mb4_')) { throw new SM_V2_Error('unsupported_storage', 503); }
            $triggers = $this->query(
                'SELECT COUNT(*) AS trigger_count FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE() AND EVENT_OBJECT_TABLE = ?',
                [], [$table]
            );
            if ((int) ($triggers[0]['trigger_count'] ?? -1) !== 0) { throw new SM_V2_Error('unsupported_storage', 503); }
            $indexes = $this->query('SHOW INDEX FROM %i', [$table]);
            $primary = array_values(array_filter($indexes, static fn(array $index): bool => $index['Key_name'] === 'PRIMARY'));
            $primary_column = ['posts' => 'ID', 'meta' => 'meta_id', 'operations' => 'operation_id', 'nonces' => 'nonce_hash'][$name];
            if (count($primary) !== 1 || $primary[0]['Column_name'] !== $primary_column ||
                (int) $primary[0]['Non_unique'] !== 0 || $primary[0]['Sub_part'] !== null) { throw new SM_V2_Error('unsupported_storage', 503); }
            if ($name === 'meta' && array_filter($indexes, static fn(array $index): bool =>
                $index['Column_name'] === 'post_id' && (int) $index['Seq_in_index'] === 1 && $index['Sub_part'] === null) === []) {
                throw new SM_V2_Error('unsupported_storage', 503);
            }
            if ($name === 'operations' || $name === 'nonces') {
                $columns = $this->query('SHOW COLUMNS FROM %i', [$table]);
                $types = [];
                foreach ($columns as $column) { $types[$column['Field']] = strtolower($column['Type']); }
                $expected = $name === 'operations' ?
                    ['operation_id' => 'char(36)', 'request_hash' => 'char(64)', 'receipt' => 'longtext'] :
                    ['nonce_hash' => 'char(64)', 'expires_at' => 'bigint unsigned'];
                foreach ($expected as $column => $type) {
                    if (($types[$column] ?? null) !== $type) { throw new SM_V2_Error('unsupported_storage', 503); }
                }
            }
        }
    }

    public function claim_nonce(string $key_id, string $nonce, int $expires): void {
        if ($this->in_transaction) { throw new SM_V2_Error('needs_reconciliation', 503); }
        try {
            $this->query('INSERT INTO %i (nonce_hash, expires_at) VALUES (?, ?)', [$this->tables['nonces']], [
                hash('sha256', $key_id . "\n" . $nonce), $expires,
            ]);
        } catch (SM_V2_Error $error) {
            if ($error->error_code === 'duplicate_key') { throw new SM_V2_Error('replay', 409); }
            throw $error;
        }
    }

    public function begin(): void {
        if ($this->in_transaction) { throw new SM_V2_Error('needs_reconciliation', 503); }
        $this->query('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE');
        $this->query('START TRANSACTION');
        $this->in_transaction = true;
        try {
            // Transaction-duration metadata locks prevent ALTER switching engines mid-apply.
            foreach ($this->tables as $table) {
                $this->query('SELECT 1 FROM %i LIMIT 0', [$table]);
            }
            $this->check_tables();
        } catch (Throwable $error) {
            try { $this->rollback(); }
            catch (Throwable $rollback_error) { throw new SM_V2_Error('needs_reconciliation', 503); }
            throw $error;
        }
    }
    private function require_transaction(): void {
        if (!$this->in_transaction) { throw new SM_V2_Error('needs_reconciliation', 503); }
    }

    public function lock_operation(string $operation_id, string $request_hash): ?array {
        $this->require_transaction();
        $created = false;
        try {
            $this->query('INSERT INTO %i (operation_id, request_hash, receipt) VALUES (?, ?, NULL)',
                [$this->tables['operations']], [$operation_id, $request_hash]);
            $created = true;
        } catch (SM_V2_Error $error) {
            if ($error->error_code !== 'duplicate_key') { throw $error; }
        }
        $rows = $this->query('SELECT request_hash, receipt FROM %i WHERE operation_id = ? FOR UPDATE', [$this->tables['operations']], [$operation_id]);
        if (count($rows) !== 1) { throw new SM_V2_Error('needs_reconciliation', 503); }
        if (!hash_equals($rows[0]['request_hash'], $request_hash)) { throw new SM_V2_Error('idempotency_conflict', 409); }
        if ($rows[0]['receipt'] === null) {
            if (!$created) { throw new SM_V2_Error('needs_reconciliation', 503); }
            return null;
        }
        return $this->decode_receipt($rows[0]['receipt']);
    }

    public function snapshot(int $entity_id): array {
        $this->require_transaction();
        $posts = $this->query('SELECT ID, post_type, post_status, post_password FROM %i WHERE ID = ? FOR UPDATE', [$this->tables['posts']], [$entity_id]);
        if (count($posts) !== 1 || !in_array($posts[0]['post_type'], ['post', 'page'], true) ||
            $posts[0]['post_status'] !== 'publish' || $posts[0]['post_password'] !== '') { throw new SM_V2_Error('target_not_found', 404); }
        // SERIALIZABLE next-key locks cover absent meta rows too; a concurrent insertion cannot evade CAS.
        $rows = $this->query(
            'SELECT meta_id, meta_key, CASE WHEN BINARY meta_key IN (?, ?, ?) THEN meta_value ELSE NULL END AS meta_value FROM %i WHERE post_id = ? LIMIT 1025 FOR UPDATE',
            [$this->tables['meta']], [self::META['title'], self::META['description'], self::META['jsonld'], $entity_id]
        );
        if (count($rows) > 1024) { throw new SM_V2_Error('unsupported_storage', 503); }
        $fields = ['title' => ['state' => 'missing'], 'description' => ['state' => 'missing'], 'jsonld' => ['state' => 'missing']];
        $seen = [];
        foreach ($rows as $row) {
            $field = array_search(strtolower($row['meta_key']), self::META, true);
            if ($field === false) { continue; }
            if (isset($seen[$field]) || $row['meta_key'] !== self::META[$field] || !is_string($row['meta_value']) ||
                strlen($row['meta_value']) > 8192 || str_contains($row['meta_value'], "\0") || preg_match('//u', $row['meta_value']) !== 1) {
                throw new SM_V2_Error('needs_reconciliation', 503);
            }
            $seen[$field] = true;
            $fields[$field] = ['state' => 'value', 'value' => $row['meta_value']];
        }
        $this->locked_entities[$entity_id] = true;
        return ['entity_id' => $entity_id, 'post_type' => $posts[0]['post_type'], 'post_status' => 'publish', 'fields' => $fields];
    }

    public function write_fields(int $entity_id, array $fields): void {
        $this->require_transaction();
        if (!isset($this->locked_entities[$entity_id])) { throw new SM_V2_Error('needs_reconciliation', 503); }
        if (count($fields) < 1 || count($fields) > 3) { throw new SM_V2_Error('invalid_schema', 400); }
        foreach ($fields as $field => $value) {
            if (!is_string($field) || !isset(self::META[$field]) || !is_array($value) || !isset($value['state'])) { throw new SM_V2_Error('invalid_schema', 400); }
            if ($value['state'] === 'missing') {
                sm_v2_keys($value, ['state']);
            } elseif ($value['state'] === 'value') {
                sm_v2_keys($value, ['state', 'value']);
                if ($field === 'jsonld') { sm_v2_jsonld($value['value']); }
                else { sm_v2_text($value['value'], $field === 'title' ? 512 : 2000); }
            } else { throw new SM_V2_Error('invalid_schema', 400); }
        }
        $this->persist_fields($entity_id, $fields);
    }
    public function restore_fields(int $entity_id, array $fields): void {
        $this->require_transaction();
        if (!isset($this->locked_entities[$entity_id])) { throw new SM_V2_Error('needs_reconciliation', 503); }
        if (count($fields) < 1 || count($fields) > 3) { throw new SM_V2_Error('unsupported_snapshot', 422); }
        foreach ($fields as $field => $state) {
            if (!is_string($field) || !isset(self::META[$field])) { throw new SM_V2_Error('unsupported_snapshot', 422); }
            sm_v2_restore_state($state);
        }
        $this->persist_fields($entity_id, $fields);
    }
    private function persist_fields(int $entity_id, array $fields): void {
        foreach ($fields as $field => $value) {
            $this->query('DELETE FROM %i WHERE post_id = ? AND BINARY meta_key = ?', [$this->tables['meta']], [$entity_id, self::META[$field]]);
            if ($value['state'] === 'value') {
                // Native prepared values are already exact. WP update_post_meta()/wp_unslash() must NOT strip JSON/backslashes here.
                $this->query('INSERT INTO %i (post_id, meta_key, meta_value) VALUES (?, ?, ?)', [$this->tables['meta']], [$entity_id, self::META[$field], $value['value']]);
            }
        }
        $this->changed_entities[$entity_id] = true;
    }
    public function save_operation(string $operation_id, string $request_hash, array $receipt): void {
        $this->require_transaction();
        $affected = $this->query('UPDATE %i SET receipt = ? WHERE operation_id = ? AND request_hash = ? AND receipt IS NULL',
            [$this->tables['operations']], [sm_v2_canonical($receipt), $operation_id, $request_hash]);
        if ($affected !== 1) { throw new SM_V2_Error('needs_reconciliation', 503); }
    }
    private function decode_receipt(string $json): array {
        try { return sm_v2_decode($json, 131072); }
        catch (SM_V2_Error $error) { throw new SM_V2_Error('needs_reconciliation', 503); }
    }
    public function read_operation(string $operation_id): ?array {
        $this->require_transaction();
        $rows = $this->query('SELECT receipt FROM %i WHERE operation_id = ? FOR UPDATE', [$this->tables['operations']], [$operation_id]);
        if ($rows === []) { return null; }
        if (count($rows) !== 1 || $rows[0]['receipt'] === null) { throw new SM_V2_Error('needs_reconciliation', 503); }
        return $this->decode_receipt($rows[0]['receipt']);
    }
    public function commit(): void {
        $this->require_transaction();
        try { $this->query('COMMIT'); }
        catch (Throwable $error) { throw new SM_V2_Error('needs_reconciliation', 503); }
        $this->in_transaction = false;
        $this->locked_entities = [];
        // Invalidate only after commit; a crash here is a visibility/verification gate, not permission to replay with a new operation.
        try {
            foreach (array_keys($this->changed_entities) as $entity_id) { ($this->invalidate_cache)($entity_id); }
        } catch (Throwable $error) { throw new SM_V2_Error('needs_reconciliation', 503); }
        $this->changed_entities = [];
    }
    public function rollback(): void {
        if ($this->in_transaction) {
            $this->query('ROLLBACK');
            $this->in_transaction = false;
        }
        $this->locked_entities = [];
        $this->changed_entities = [];
    }

    // Called only by separately authorized activation with an explicit install flag, never by REST/readiness.
    public function install_schema(): void {
        $this->query("CREATE TABLE IF NOT EXISTS %i (
            operation_id char(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            request_hash char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            receipt longtext NULL,
            PRIMARY KEY (operation_id)
        ) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin", [$this->tables['operations']]);
        $this->query("CREATE TABLE IF NOT EXISTS %i (
            nonce_hash char(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            expires_at bigint unsigned NOT NULL,
            PRIMARY KEY (nonce_hash)
        ) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin", [$this->tables['nonces']]);
        $this->check_storage();
    }
}
