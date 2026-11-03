<?php
declare(strict_types=1);
define('ABSPATH', __DIR__);
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-protocol.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-service.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-sql.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-store.php';

// Exercises the actual SM_V2_WPDB_Store, not a replacement store. SQL execution/locks are scripted, NOT MySQL/WP evidence.
final class ScriptedSQL implements SM_V2_SQL {
    public array $calls = [];
    public array $meta_rows = [];
    public array $operations = [];
    public array $nonces = [];
    public array $engine_overrides = [];
    public string $charset = 'utf8mb4';
    public string $sql_mode = 'STRICT_TRANS_TABLES';
    public int $durable_commit = 1;
    public int $autocommit = 1;
    public bool $has_trigger_grant = true;
    public int $trigger_count = 0;
    public bool $has_primary_key = true;
    public bool $has_post_index = true;
    public bool $in_transaction = false;
    public bool $fail_commit = false;
    public bool $fail_meta_insert = false;
    public string $post_status = 'publish';
    private ?array $backup = null;
    public function tables(): array {
        return ['posts' => 'test_posts', 'meta' => 'test_postmeta', 'operations' => 'test_operations', 'nonces' => 'test_nonces'];
    }
    public function character_set(): string { return $this->charset; }
    public function query(string $sql, array $identifiers = [], array $parameters = []): array|int {
        $this->calls[] = [$sql, $identifiers, $parameters];
        if (count($this->calls) > 500) { throw new RuntimeException('Unexpected SQL recursion/unbounded calls'); }
        if ($sql === 'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE') {
            if ($this->in_transaction) { throw new SM_V2_Error('storage_failure', 503); }
            return 0;
        }
        if ($sql === 'START TRANSACTION') {
            if ($this->in_transaction) { throw new RuntimeException('Nested transaction'); }
            $this->backup = [$this->meta_rows, $this->operations];
            $this->in_transaction = true;
            return 0;
        }
        if ($sql === 'COMMIT') {
            if (!$this->in_transaction) { throw new RuntimeException('Commit outside transaction'); }
            if ($this->fail_commit) { throw new SM_V2_Error('storage_failure', 503); }
            $this->in_transaction = false;
            $this->backup = null;
            return 0;
        }
        if ($sql === 'ROLLBACK') {
            if ($this->backup !== null) { [$this->meta_rows, $this->operations] = $this->backup; }
            $this->in_transaction = false;
            $this->backup = null;
            return 0;
        }
        if (str_starts_with($sql, 'SELECT @@SESSION.autocommit')) {
            return [['autocommit' => $this->autocommit, 'sql_mode' => $this->sql_mode, 'durable_commit' => $this->durable_commit]];
        }
        if ($sql === 'SELECT CURRENT_USER() AS account') { return [['account' => 'wordpress@localhost']]; }
        if (str_contains($sql, 'information_schema.USER_PRIVILEGES') || str_contains($sql, 'information_schema.TABLE_PRIVILEGES')) { return []; }
        if (str_contains($sql, 'information_schema.SCHEMA_PRIVILEGES')) { return $this->has_trigger_grant ? [['PRIVILEGE_TYPE' => 'TRIGGER']] : []; }
        if (str_contains($sql, 'information_schema.TRIGGERS')) { return [['trigger_count' => $this->trigger_count]]; }
        if (str_contains($sql, 'information_schema.TABLES')) {
            return [['ENGINE' => $this->engine_overrides[$parameters[0]] ?? 'InnoDB', 'TABLE_COLLATION' => 'utf8mb4_bin']];
        }
        if ($sql === 'SHOW INDEX FROM %i') {
            $key = array_search($identifiers[0], $this->tables(), true);
            $primary = ['posts' => 'ID', 'meta' => 'meta_id', 'operations' => 'operation_id', 'nonces' => 'nonce_hash'][$key];
            $indexes = $this->has_primary_key ? [['Key_name' => 'PRIMARY', 'Column_name' => $primary, 'Non_unique' => 0, 'Seq_in_index' => 1, 'Sub_part' => null]] : [];
            if ($key === 'meta' && $this->has_post_index) { $indexes[] = ['Key_name' => 'post_id', 'Column_name' => 'post_id', 'Non_unique' => 1, 'Seq_in_index' => 1, 'Sub_part' => null]; }
            return $indexes;
        }
        if ($sql === 'SHOW COLUMNS FROM %i') {
            $types = $identifiers[0] === 'test_operations' ?
                ['operation_id' => 'char(36)', 'request_hash' => 'char(64)', 'receipt' => 'longtext'] :
                ['nonce_hash' => 'char(64)', 'expires_at' => 'bigint unsigned'];
            $columns = [];
            foreach ($types as $field => $type) { $columns[] = ['Field' => $field, 'Type' => $type]; }
            return $columns;
        }
        if ($sql === 'SELECT 1 FROM %i LIMIT 0') {
            if (!$this->in_transaction) { throw new RuntimeException('Metadata-lock query outside transaction'); }
            return [];
        }
        if (str_starts_with($sql, 'CREATE TABLE IF NOT EXISTS %i')) { return 0; }
        if (str_starts_with($sql, 'INSERT INTO %i (nonce_hash')) {
            if ($this->in_transaction) { throw new RuntimeException('Nonce must commit independently'); }
            if (isset($this->nonces[$parameters[0]])) { throw new SM_V2_Error('duplicate_key', 503); }
            $this->nonces[$parameters[0]] = $parameters[1];
            return 1;
        }
        if (!$this->in_transaction) { throw new RuntimeException('Business SQL outside transaction'); }
        if (str_starts_with($sql, 'INSERT INTO %i (operation_id')) {
            if (isset($this->operations[$parameters[0]])) { throw new SM_V2_Error('duplicate_key', 503); }
            $this->operations[$parameters[0]] = ['request_hash' => $parameters[1], 'receipt' => null];
            return 1;
        }
        if (str_starts_with($sql, 'SELECT request_hash, receipt')) {
            if (!str_ends_with($sql, 'FOR UPDATE')) { throw new RuntimeException('Ledger reservation read must lock'); }
            return isset($this->operations[$parameters[0]]) ? [$this->operations[$parameters[0]]] : [];
        }
        if (str_starts_with($sql, 'SELECT receipt FROM')) {
            if (!str_ends_with($sql, 'FOR UPDATE')) { throw new RuntimeException('Status receipt read must lock'); }
            return isset($this->operations[$parameters[0]]) ? [['receipt' => $this->operations[$parameters[0]]['receipt']]] : [];
        }
        if (str_starts_with($sql, 'SELECT ID, post_type')) {
            if (!str_ends_with($sql, 'FOR UPDATE')) { throw new RuntimeException('Target read must lock'); }
            return [['ID' => $parameters[0], 'post_type' => 'post', 'post_status' => $this->post_status, 'post_password' => '']];
        }
        if (str_starts_with($sql, 'SELECT meta_id, meta_key')) {
            if (!str_ends_with($sql, 'LIMIT 1025 FOR UPDATE')) { throw new RuntimeException('Bounded meta locking read required'); }
            return $this->meta_rows;
        }
        if (str_starts_with($sql, 'DELETE FROM %i WHERE post_id')) {
            $this->meta_rows = array_values(array_filter($this->meta_rows, static fn(array $row): bool => $row['meta_key'] !== $parameters[1]));
            return 1;
        }
        if (str_starts_with($sql, 'INSERT INTO %i (post_id')) {
            if ($this->fail_meta_insert) { throw new SM_V2_Error('storage_failure', 503); }
            $this->meta_rows[] = ['meta_id' => count($this->meta_rows) + 1, 'meta_key' => $parameters[1], 'meta_value' => $parameters[2]];
            return 1;
        }
        if (str_starts_with($sql, 'UPDATE %i SET receipt')) {
            $operation = $this->operations[$parameters[1]] ?? null;
            if ($operation === null || $operation['receipt'] !== null || $operation['request_hash'] !== $parameters[2]) { return 0; }
            $this->operations[$parameters[1]]['receipt'] = $parameters[0];
            return 1;
        }
        throw new RuntimeException('Unexpected SQL template: ' . $sql);
    }
}
final class StoreFixture {
    public ScriptedSQL $sql;
    public SM_V2_WPDB_Store $store;
    public array $invalidated = [];
    public bool $fail_cache = false;
    public function __construct() {
        $this->sql = new ScriptedSQL();
        $this->store = new SM_V2_WPDB_Store($this->sql, function (int $entity_id): void {
            if ($this->fail_cache) { throw new RuntimeException('Injected post-commit cache failure'); }
            $this->invalidated[] = $entity_id;
        });
    }
}
function require_condition(bool $condition, string $message): void {
    if (!$condition) { throw new RuntimeException($message); }
}
function require_store_error(callable $action, string $code): void {
    try { $action(); } catch (SM_V2_Error $error) {
        if ($error->error_code !== $code) { throw new RuntimeException("Expected $code, got {$error->error_code}"); }
        return;
    }
    throw new RuntimeException("Expected $code");
}
$operation_id = '33333333-3333-4333-8333-333333333333';
$request_hash = hash('sha256', 'non-secret-request');
$tests = [
    'actual store outside-transaction guards precede all SQL' => function () use ($operation_id, $request_hash): void {
        $fixture = new StoreFixture();
        foreach ([
            fn() => $fixture->store->read_operation($operation_id), fn() => $fixture->store->snapshot(7),
            fn() => $fixture->store->write_fields(7, ['title' => ['state' => 'missing']]),
            fn() => $fixture->store->lock_operation($operation_id, $request_hash),
            fn() => $fixture->store->save_operation($operation_id, $request_hash, ['kind' => 'receipt']),
        ] as $action) { require_store_error($action, 'needs_reconciliation'); }
        require_condition($fixture->sql->calls === [], 'guard reached SQL');
    },
    'preflight has no DDL or business mutations' => function (): void {
        $fixture = new StoreFixture();
        $fixture->store->check_storage();
        foreach ($fixture->sql->calls as [$sql]) { require_condition(!preg_match('/\A(?:CREATE|ALTER|DELETE|INSERT|UPDATE|COMMIT)/', $sql), 'preflight mutated'); }
        require_condition($fixture->sql->operations === [] && $fixture->sql->meta_rows === [], 'preflight wrote fields');
    },
    'storage incompatibilities fail closed before writes' => function (): void {
        foreach ([
            ['sql_mode', ''], ['charset', 'utf8'], ['durable_commit', 0], ['autocommit', 0],
            ['has_trigger_grant', false], ['trigger_count', 1], ['has_primary_key', false], ['has_post_index', false],
            ['engine_overrides', ['test_postmeta' => 'MyISAM']],
        ] as [$property, $value]) {
            $fixture = new StoreFixture();
            $fixture->sql->$property = $value;
            require_store_error(fn() => $fixture->store->check_storage(), 'unsupported_storage');
            require_condition($fixture->sql->meta_rows === [] && $fixture->sql->operations === [], 'bad storage wrote');
        }
    },
    'foreign transaction cannot be implicitly committed' => function (): void {
        $fixture = new StoreFixture();
        $fixture->sql->in_transaction = true;
        require_store_error(fn() => $fixture->store->begin(), 'storage_failure');
        require_condition(count($fixture->sql->calls) === 1 && $fixture->sql->in_transaction, 'foreign transaction touched');
    },
    'nonce unique claim survives business rollback' => function () use ($operation_id, $request_hash): void {
        $fixture = new StoreFixture();
        $fixture->store->claim_nonce('key', str_repeat('a', 32), 1793715901);
        require_store_error(fn() => $fixture->store->claim_nonce('key', str_repeat('a', 32), 1793715901), 'replay');
        $fixture->store->begin();
        $fixture->store->lock_operation($operation_id, $request_hash);
        $fixture->store->snapshot(7);
        $fixture->store->write_fields(7, ['title' => ['state' => 'value', 'value' => 'Title']]);
        $fixture->store->rollback();
        require_condition(count($fixture->sql->nonces) === 1 && $fixture->sql->operations === [] && $fixture->sql->meta_rows === [], 'rollback lost nonce or retained business writes');
        require_condition($fixture->invalidated === [], 'rollback invalidated cache before commit');
    },
    'exact prepared values commit with ledger then status reads lock' => function () use ($operation_id, $request_hash): void {
        $fixture = new StoreFixture();
        $fixture->store->begin();
        $fixture->store->lock_operation($operation_id, $request_hash);
        $before = $fixture->store->snapshot(7);
        require_condition($before['fields']['title'] === ['state' => 'missing'], 'missing state lost');
        $value = 'Точный "quote" \\ 😀; SELECT --';
        $fixture->store->write_fields(7, ['title' => ['state' => 'value', 'value' => $value]]);
        require_condition($fixture->invalidated === [], 'cache invalidated before commit');
        $receipt = ['kind' => 'receipt', 'operation_id' => $operation_id, 'value' => $value];
        $fixture->store->save_operation($operation_id, $request_hash, $receipt);
        $fixture->store->commit();
        require_condition($fixture->invalidated === [7], 'committed cache not invalidated');
        $fixture->store->begin();
        require_condition($fixture->store->read_operation($operation_id) === $receipt, 'durable receipt changed');
        require_condition($fixture->store->lock_operation($operation_id, $request_hash) === $receipt, 'same-op changed');
        require_store_error(fn() => $fixture->store->lock_operation($operation_id, hash('sha256', 'different')), 'idempotency_conflict');
        $fixture->store->commit();
        foreach ($fixture->sql->calls as [$sql, $identifiers, $parameters]) {
            require_condition(!str_contains($sql, $value), 'external value interpolated into SQL');
            foreach ($identifiers as $identifier) { require_condition(in_array($identifier, $fixture->sql->tables(), true), 'untrusted table'); }
        }
    },
    'NULL committed reservation is sticky needs_reconciliation' => function () use ($operation_id, $request_hash): void {
        $fixture = new StoreFixture();
        $fixture->sql->operations[$operation_id] = ['request_hash' => $request_hash, 'receipt' => null];
        $fixture->store->begin();
        require_store_error(fn() => $fixture->store->lock_operation($operation_id, $request_hash), 'needs_reconciliation');
        require_store_error(fn() => $fixture->store->read_operation($operation_id), 'needs_reconciliation');
        $fixture->store->rollback();
    },
    'partial delete/insert failure rolls back through real store control path' => function () use ($operation_id, $request_hash): void {
        $fixture = new StoreFixture();
        $fixture->sql->meta_rows = [['meta_id' => 1, 'meta_key' => '_seo_master_meta_title', 'meta_value' => 'Before']];
        $before = $fixture->sql->meta_rows;
        $fixture->store->begin();
        $fixture->store->lock_operation($operation_id, $request_hash);
        $fixture->store->snapshot(7);
        $fixture->sql->fail_meta_insert = true;
        require_store_error(fn() => $fixture->store->write_fields(7, ['title' => ['state' => 'value', 'value' => 'After']]), 'storage_failure');
        $fixture->store->rollback();
        require_condition($fixture->sql->meta_rows === $before && $fixture->sql->operations === [], 'partial write retained');
    },
    'invalid internal field states reject the entire batch before SQL writes' => function (): void {
        $fixture = new StoreFixture();
        $fixture->store->begin();
        $fixture->store->snapshot(7);
        $fixture->sql->calls = [];
        require_store_error(fn() => $fixture->store->write_fields(7, [
            'title' => ['state' => 'value', 'value' => 'Valid first field'],
            'description' => ['state' => 'invented'],
        ]), 'invalid_schema');
        require_condition($fixture->sql->meta_rows === [] && $fixture->sql->calls === [], 'invalid batch reached mutation SQL');
        $fixture->store->rollback();
    },
    'ambiguous commit never becomes success' => function () use ($operation_id, $request_hash): void {
        $fixture = new StoreFixture();
        $fixture->store->begin();
        $fixture->store->lock_operation($operation_id, $request_hash);
        $fixture->sql->fail_commit = true;
        require_store_error(fn() => $fixture->store->commit(), 'needs_reconciliation');
        $fixture->store->rollback();
        require_condition($fixture->sql->operations === [], 'uncommitted reservation retained');
    },
    'meta ambiguity and unpublished target reject' => function (): void {
        $fixture = new StoreFixture();
        $fixture->sql->meta_rows = [
            ['meta_id' => 1, 'meta_key' => '_seo_master_meta_title', 'meta_value' => ''],
            ['meta_id' => 2, 'meta_key' => '_seo_master_meta_title', 'meta_value' => 'duplicate'],
        ];
        $fixture->store->begin();
        require_store_error(fn() => $fixture->store->snapshot(7), 'needs_reconciliation');
        $fixture->store->rollback();
        $fixture->sql->post_status = 'draft';
        $fixture->store->begin();
        require_store_error(fn() => $fixture->store->snapshot(7), 'target_not_found');
        $fixture->store->rollback();
    },
    'schema code is opt-in only and defines unique InnoDB tables' => function (): void {
        $fixture = new StoreFixture();
        $fixture->store->install_schema();
        $ddl = array_filter($fixture->sql->calls, static fn(array $call): bool => str_starts_with($call[0], 'CREATE TABLE'));
        require_condition(count($ddl) === 2, 'schema count mismatch');
        foreach ($ddl as [$sql]) { require_condition(str_contains($sql, 'ENGINE=InnoDB') && str_contains($sql, 'PRIMARY KEY'), 'non-durable schema'); }
    },
];
$tests += [
    'post-commit cache failure requires reconciliation and retains durable recovery' => function () use ($operation_id, $request_hash): void {
        $fixture = new StoreFixture();
        $fixture->store->begin();
        $fixture->store->lock_operation($operation_id, $request_hash);
        $fixture->store->snapshot(7);
        $fixture->store->write_fields(7, ['title' => ['state' => 'value', 'value' => 'Committed']]);
        $receipt = ['kind' => 'receipt', 'operation_id' => $operation_id];
        $fixture->store->save_operation($operation_id, $request_hash, $receipt);
        $fixture->fail_cache = true;
        require_store_error(fn() => $fixture->store->commit(), 'needs_reconciliation');
        $fixture->store->rollback();
        require_condition(count($fixture->sql->meta_rows) === 1 && isset($fixture->sql->operations[$operation_id]), 'cache failure lost committed state');
        $fixture->fail_cache = false;
        $fixture->store->begin();
        require_condition($fixture->store->read_operation($operation_id) === $receipt, 'post-commit receipt lost');
        $fixture->store->commit();
    },
    'exact restore uses separate validators without weakening ordinary set' => function (): void {
        $fixture = new StoreFixture();
        require_store_error(fn() => $fixture->store->restore_fields(7, ['title' => ['state' => 'value', 'value' => '']]), 'needs_reconciliation');
        $fixture->store->begin();
        require_store_error(fn() => $fixture->store->restore_fields(7, ['title' => ['state' => 'missing']]), 'needs_reconciliation');
        $fixture->store->snapshot(7);
        $restored = [
            'title' => ['state' => 'value', 'value' => ''],
            'description' => ['state' => 'value', 'value' => str_repeat('L', 3000)],
            'jsonld' => ['state' => 'value', 'value' => 'Legacy 😀 \\ raw string'],
        ];
        $fixture->store->restore_fields(7, $restored);
        require_condition($fixture->store->snapshot(7)['fields'] === $restored, 'restore transformed bytes');
        require_store_error(fn() => $fixture->store->write_fields(7, ['title' => ['state' => 'value', 'value' => '']]), 'invalid_schema');
        require_store_error(fn() => $fixture->store->write_fields(7, ['description' => $restored['description']]), 'invalid_schema');
        $fixture->store->rollback();
        require_condition($fixture->sql->meta_rows === [], 'restore rollback retained fields');
    },
    'restore validates complete batch before first DELETE' => function (): void {
        $fixture = new StoreFixture();
        $fixture->store->begin();
        $fixture->store->snapshot(7);
        foreach ([
            ['state' => 'value', 'value' => 'O:8:"Injected":0:{}'],
            ['state' => 'value', 'value' => '<script>unsafe</script>'],
            ['state' => 'value', 'value' => str_repeat('L', 8193)],
            ['state' => 'value', 'value' => "bad\0value"],
            ['state' => 'invented'],
        ] as $invalid) {
            $fixture->sql->calls = [];
            require_store_error(fn() => $fixture->store->restore_fields(7, [
                'title' => ['state' => 'value', 'value' => 'Valid first'], 'description' => $invalid,
            ]), 'unsupported_snapshot');
            require_condition($fixture->sql->calls === [] && $fixture->sql->meta_rows === [], 'unsafe restore reached SQL');
        }
        $fixture->store->rollback();
    },
    'service actual-store seam locks guard by PK and blocks new id after reversal' => function (): void {
        $fixture = new StoreFixture();
        $project = '11111111-1111-4111-8111-111111111111';
        $site = '22222222-2222-4222-8222-222222222222';
        $original_id = '33333333-3333-4333-8333-333333333333';
        $reverse_id = '77777777-7777-4777-8777-777777777777';
        $secret = bin2hex(random_bytes(32));
        $service = new SM_V2_Service(new SM_V2_Config($project, $site, 'test-key', $secret, bin2hex(random_bytes(32)), true), $fixture->store, '0.3.0-dev');
        $send = function (array $command, string $route) use ($service, $project, $site, $secret): array {
            $body = sm_v2_canonical($command);
            $headers = [
                'x-seo-project-id' => $project, 'x-seo-site-id' => $site, 'x-seo-key-id' => 'test-key',
                'x-seo-timestamp' => '1793715600', 'x-seo-nonce' => bin2hex(random_bytes(16)),
                'x-seo-operation-id' => $command['operation_id'],
            ];
            $headers['x-seo-signature'] = hash_hmac('sha256', sm_v2_signing_message('POST', $route, $body, $headers), $secret);
            return $service->handle('POST', $route, $body, $headers, 1793715600);
        };
        $before = ['entity_id' => 7, 'post_type' => 'post', 'post_status' => 'publish', 'fields' => [
            'title' => ['state' => 'missing'], 'description' => ['state' => 'missing'], 'jsonld' => ['state' => 'missing'],
        ]];
        $after = $before;
        $after['fields']['title'] = ['state' => 'value', 'value' => 'Native title'];
        $command = [
            'project_id' => $project, 'site_id' => $site, 'entity_id' => 7, 'operation_id' => $original_id,
            'task_id' => '44444444-4444-4444-8444-444444444444', 'proposal_version' => '55555555-5555-4555-8555-555555555555',
            'approval_ref' => '66666666-6666-4666-8666-666666666666', 'before_digest' => sm_v2_digest($before),
            'expected_after_digest' => sm_v2_digest($after), 'changes' => [['op' => 'set', 'field' => 'title', 'value' => 'Native title']],
        ];
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $original = $send($command, '/seo-master/v2/apply');
        $original_row = $fixture->sql->operations[$original_id];
        $reverse = [
            'project_id' => $project, 'site_id' => $site, 'entity_id' => 7, 'operation_id' => $reverse_id,
            'task_id' => $command['task_id'], 'proposal_version' => $command['proposal_version'], 'approval_ref' => $command['approval_ref'],
            'reason' => 'Native seam restore', 'original_operation_id' => $original_id, 'original_receipt_id' => $original['receipt_id'],
            'original_request_hash' => $original['request_hash'], 'original_proposal_hash' => $original['proposal_hash'],
            'before_digest' => $original['after_digest'], 'expected_after_digest' => $original['before_digest'],
        ];
        $proposal = $reverse;
        unset($proposal['operation_id'], $proposal['approval_ref']);
        $reverse['proposal_hash'] = hash('sha256', "SEO-MASTER-ROLLBACK-PROPOSAL-V2\n" . sm_v2_canonical($proposal));
        $identity = sm_v2_guard_identity($original);
        foreach ([null, '{"kind":"receipt"}', '{invalid json'] as $corrupt_guard) {
            $fixture->sql->operations[$identity['guard_id']] = ['request_hash' => $identity['identity_digest'], 'receipt' => $corrupt_guard];
            $fixture->sql->calls = [];
            require_store_error(fn() => $send($reverse, '/seo-master/v2/rollback'), 'needs_reconciliation');
            require_condition(!isset($fixture->sql->operations[$reverse_id]) && $fixture->sql->operations[$original_id] === $original_row, 'bad guard left new-op reservation or changed original');
            foreach ($fixture->sql->calls as [$sql]) { require_condition(!str_starts_with($sql, 'DELETE FROM') && !str_starts_with($sql, 'SELECT ID, post_type'), 'bad guard reached target'); }
            unset($fixture->sql->operations[$identity['guard_id']]);
        }
        $fixture->sql->calls = [];
        $receipt = $send($reverse, '/seo-master/v2/rollback');
        require_condition($receipt['outcome'] === 'rollback_applied_unverified' && $fixture->sql->meta_rows === [], 'actual-store inverse failed');
        require_condition($fixture->sql->operations[$original_id] === $original_row && count($fixture->sql->operations) === 3, 'original changed or guard missing');
        $locks = array_values(array_filter($fixture->sql->calls, static fn(array $call): bool =>
            str_starts_with($call[0], 'INSERT INTO %i (operation_id') ||
            str_starts_with($call[0], 'SELECT receipt FROM') || str_starts_with($call[0], 'SELECT ID, post_type')));
        require_condition($locks[0][2][0] === $reverse_id && $locks[1][2][0] === $original_id &&
            substr($locks[2][2][0], 14, 1) === '8' && $locks[3][2][0] === 7, 'lock order new/original/guard/target changed');
        $fixture->sql->meta_rows = [['meta_id' => 1, 'meta_key' => '_seo_master_meta_title', 'meta_value' => 'Native title']];
        $reverse['operation_id'] = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
        $fixture->sql->calls = [];
        require_store_error(fn() => $send($reverse, '/seo-master/v2/rollback'), 'rollback_already_consumed');
        require_condition(count($fixture->sql->operations) === 3 && count($fixture->sql->meta_rows) === 1, 'new-id reservation or mutation survived conflict');
        foreach ($fixture->sql->calls as [$sql]) {
            require_condition(!str_starts_with($sql, 'SELECT ID, post_type') && !str_starts_with($sql, 'DELETE FROM') && !str_contains($sql, 'JSON_EXTRACT'), 'guard lookup scanned ledger or reached target');
        }
    },
];
$failures = [];
foreach ($tests as $name => $test) {
    try { $test(); } catch (Throwable $error) { $failures[] = "$name: {$error->getMessage()}"; }
}
foreach ($failures as $failure) { fwrite(STDERR, "FAIL: $failure\n"); }
printf("Actual store contracts: %d/%d passed (scripted SQL adapter, NOT MySQL/WP/lock evidence)\n", count($tests) - count($failures), count($tests));
exit($failures === [] ? 0 : 1);
