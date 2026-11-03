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
    public array $cache_attempts = [];
    public bool $fail_cache = false;
    public function __construct(?RollbackFaultSQL $faults = null) {
        $this->sql = $faults === null ? new ScriptedSQL() : $faults->inner;
        $this->store = new SM_V2_WPDB_Store($faults ?? $this->sql, function (int $entity_id): void {
            require_condition(!$this->sql->in_transaction && $this->sql->calls[array_key_last($this->sql->calls)][0] === 'COMMIT', 'cache callback ran before COMMIT');
            $this->cache_attempts[] = $entity_id;
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

// Decorates the existing SQL fixture; even intercepted failing attempts remain visible in the trace.
final class RollbackFaultSQL implements SM_V2_SQL {
    public string $failure = '';
    public bool $triggered = false;
    public array $calls = [];
    public function __construct(public ScriptedSQL $inner) {}
    public function tables(): array { return $this->inner->tables(); }
    public function character_set(): string { return $this->inner->character_set(); }
    public function query(string $sql, array $identifiers = [], array $parameters = []): array|int {
        $this->calls[] = [$sql, $identifiers, $parameters];
        if (str_starts_with($sql, 'UPDATE %i SET receipt') &&
            (($this->failure === 'guard_save' && substr($parameters[1], 14, 1) === '8') ||
             ($this->failure === 'reverse_receipt_save' && $parameters[1] === RollbackStoreFixture::REVERSE))) {
            $this->triggered = true;
            throw new SM_V2_Error('storage_failure', 503);
        }
        if ($this->failure === 'restore_readback' && str_starts_with($sql, 'INSERT INTO %i (post_id') &&
            $parameters[1] === '_seo_master_meta_title') {
            $this->triggered = true;
            $parameters[2] .= '#';
        }
        $result = $this->inner->query($sql, $identifiers, $parameters);
        if ($sql === 'COMMIT' && $this->failure === 'lost_commit') {
            $this->triggered = true;
            throw new SM_V2_Error('storage_failure', 503);
        }
        return $result;
    }
}
final class RollbackStoreFixture {
    public const PROJECT = '11111111-1111-4111-8111-111111111111';
    public const SITE = '22222222-2222-4222-8222-222222222222';
    public const ORIGINAL = '33333333-3333-4333-8333-333333333333';
    public const REVERSE = '77777777-7777-4777-8777-777777777777';
    public const OTHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
    public StoreFixture $fixture;
    public RollbackFaultSQL $faults;
    public array $original;
    public array $apply;
    public array $reverse;
    public array $identity;
    public array $applied_rows;
    private array $original_row;
    private SM_V2_Service $service;
    private string $secret;
    private string $receipt_secret;
    public function __construct(bool $no_op = false) {
        $this->faults = new RollbackFaultSQL(new ScriptedSQL());
        $this->fixture = new StoreFixture($this->faults);
        $values = ['title' => $no_op ? 'Approved title' : '', 'description' => " legacy\tline\nCR\r😀\\" . str_repeat('L', 3000), 'jsonld' => str_repeat('L', 8192)];
        $before = ['entity_id' => 7, 'post_type' => 'post', 'post_status' => 'publish', 'fields' => []];
        foreach ($values as $field => $value) {
            $before['fields'][$field] = ['state' => 'value', 'value' => $value];
            $this->fixture->sql->meta_rows[] = [
                'meta_id' => count($this->fixture->sql->meta_rows) + 1,
                'meta_key' => '_seo_master_' . ($field === 'jsonld' ? 'schema_jsonld' : 'meta_' . $field), 'meta_value' => $value,
            ];
        }
        $this->secret = bin2hex(random_bytes(32));
        $this->receipt_secret = bin2hex(random_bytes(32));
        $this->service = new SM_V2_Service(new SM_V2_Config(
            self::PROJECT, self::SITE, 'fault-test-key', $this->secret, $this->receipt_secret, true,
        ), $this->fixture->store, '0.3.0-dev');
        $after = $before;
        $changes = [];
        $approved = $no_op ? ['title' => 'Approved title'] : ['title' => 'Approved title', 'description' => 'Approved description',
            'jsonld' => '{"@context":"https://schema.org","@type":"Article","name":"Approved"}'];
        foreach ($approved as $field => $value) {
            $after['fields'][$field] = ['state' => 'value', 'value' => $value];
            $changes[] = ['op' => 'set', 'field' => $field, 'value' => $value];
        }
        $command = [
            'project_id' => self::PROJECT, 'site_id' => self::SITE, 'entity_id' => 7, 'operation_id' => self::ORIGINAL,
            'task_id' => '44444444-4444-4444-8444-444444444444', 'proposal_version' => '55555555-5555-4555-8555-555555555555',
            'approval_ref' => '66666666-6666-4666-8666-666666666666', 'before_digest' => sm_v2_digest($before),
            'expected_after_digest' => sm_v2_digest($after), 'changes' => $changes,
        ];
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $this->apply = $command;
        $this->original = $this->send($command, '/seo-master/v2/apply');
        require_condition(sm_v2_canonical($this->original['before']) === sm_v2_canonical($before), 'original lost exact legacy before');
        $this->original_row = $this->fixture->sql->operations[self::ORIGINAL];
        $this->applied_rows = $this->fixture->sql->meta_rows;
        $this->identity = sm_v2_guard_identity($this->original);
        $this->reverse = [
            'project_id' => self::PROJECT, 'site_id' => self::SITE, 'entity_id' => 7, 'operation_id' => self::REVERSE,
            'task_id' => $command['task_id'], 'proposal_version' => $command['proposal_version'], 'approval_ref' => $command['approval_ref'],
            'reason' => 'Mandatory actual-store fault regression', 'original_operation_id' => self::ORIGINAL,
            'original_receipt_id' => $this->original['receipt_id'], 'original_request_hash' => $this->original['request_hash'],
            'original_proposal_hash' => $this->original['proposal_hash'], 'before_digest' => $this->original['after_digest'],
            'expected_after_digest' => $this->original['before_digest'],
        ];
        $this->reverse['proposal_hash'] = sm_v2_rollback_proposal_hash($this->reverse);
        $this->fixture->invalidated = [];
        $this->fixture->cache_attempts = [];
    }
    public function send(array $command, string $route = '/seo-master/v2/rollback'): array {
        return $this->request('POST', $route, sm_v2_canonical($command), $command['operation_id'] ?? '-');
    }
    public function status(string $operation_id): array {
        return $this->request('GET', '/seo-master/v2/operations/' . $operation_id, '', $operation_id);
    }
    private function request(string $method, string $route, string $body, string $operation_id): array {
        $this->faults->calls = $this->fixture->sql->calls = [];
        $headers = [
            'x-seo-project-id' => self::PROJECT, 'x-seo-site-id' => self::SITE, 'x-seo-key-id' => 'fault-test-key',
            'x-seo-timestamp' => '1793715600', 'x-seo-nonce' => bin2hex(random_bytes(16)), 'x-seo-operation-id' => $operation_id,
        ];
        $headers['x-seo-signature'] = hash_hmac('sha256', sm_v2_signing_message($method, $route, $body, $headers), $this->secret);
        return $this->service->handle($method, $route, $body, $headers, 1793715600);
    }
    public function require_original(): void {
        require_condition($this->fixture->sql->operations[self::ORIGINAL] === $this->original_row, 'original receipt was changed');
        require_condition(!$this->fixture->sql->in_transaction, 'business transaction leaked');
    }
    public function require_restored(): void {
        $actual = [];
        foreach ($this->fixture->sql->meta_rows as $row) { $actual[$row['meta_key']] = $row['meta_value']; }
        $fields = $this->original['before']['fields'];
        require_condition(count($this->fixture->sql->meta_rows) === 3 && $actual === [
            '_seo_master_meta_title' => $fields['title']['value'],
            '_seo_master_meta_description' => $fields['description']['value'],
            '_seo_master_schema_jsonld' => $fields['jsonld']['value'],
        ], 'restore changed exact empty/control/Unicode/backslash/8192-byte legacy values');
        require_condition(count($this->fixture->sql->operations) === 3, 'receipt or single-use guard missing');
        foreach ($this->fixture->sql->operations as $row) { require_condition(is_string($row['receipt']), 'NULL reservation leaked'); }
        $this->require_original();
    }
    public function restore_trace(): array {
        return [
            'START TRANSACTION', 'reserve:' . self::REVERSE, 'lock:' . self::REVERSE, 'read:' . self::ORIGINAL,
            'reserve:' . $this->identity['guard_id'], 'lock:' . $this->identity['guard_id'], 'target', 'meta',
            'delete:_seo_master_meta_title', 'insert:_seo_master_meta_title',
            'delete:_seo_master_meta_description', 'insert:_seo_master_meta_description',
            'delete:_seo_master_schema_jsonld', 'insert:_seo_master_schema_jsonld', 'target', 'meta',
        ];
    }
    public function require_trace(array $expected): void {
        $actual = [];
        foreach ($this->faults->calls as [$sql, $identifiers, $parameters]) {
            if (in_array($sql, ['START TRANSACTION', 'COMMIT', 'ROLLBACK'], true)) { $actual[] = $sql; }
            foreach ([
                'INSERT INTO %i (operation_id' => ['reserve:', 0], 'SELECT request_hash, receipt' => ['lock:', 0],
                'SELECT receipt FROM' => ['read:', 0], 'UPDATE %i SET receipt' => ['save:', 1],
                'DELETE FROM %i WHERE post_id' => ['delete:', 1], 'INSERT INTO %i (post_id' => ['insert:', 1],
            ] as $prefix => [$label, $index]) {
                if (str_starts_with($sql, $prefix)) { $actual[] = $label . $parameters[$index]; }
            }
            if (str_starts_with($sql, 'SELECT ID, post_type')) { $actual[] = 'target'; }
            if (str_starts_with($sql, 'SELECT meta_id, meta_key')) { $actual[] = 'meta'; }
            require_condition(!str_contains($sql, 'JSON_EXTRACT') && !preg_match('/\A(?:CREATE|ALTER)/', $sql), 'unbounded ledger scan or DDL');
        }
        require_condition($actual === $expected, 'actual-store lock/target/write/save/transaction call order changed');
    }
    public function resign_guard_reference(string $reference): void {
        $row = &$this->fixture->sql->operations[$this->identity['guard_id']];
        $guard = sm_v2_decode($row['receipt'], 131072);
        $guard['references'][$reference] = $reference === 'entity_id' ? 8 :
            (in_array($reference, ['original_receipt_id', 'original_request_hash'], true) ? str_repeat('e', 64) : 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee');
        unset($guard['signature']);
        $guard['signature'] = sm_v2_guard_signature($guard, $this->receipt_secret);
        $row['receipt'] = sm_v2_canonical($guard);
    }
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
foreach (['guard_save' => 'storage_failure', 'reverse_receipt_save' => 'storage_failure',
    'restore_readback' => 'storage_postcondition_failed'] as $failure => $code) {
    $tests['rollback tail failure ' . $failure . ' is atomic and exact-body retry restores legacy before'] = function () use ($failure, $code): void {
        $probe = new RollbackStoreFixture();
        $rows = $probe->fixture->sql->meta_rows;
        $operations = $probe->fixture->sql->operations;
        $nonces = count($probe->fixture->sql->nonces);
        $probe->faults->failure = $failure;
        require_store_error(fn() => $probe->send($probe->reverse), $code);
        require_condition($probe->faults->triggered, 'tail failure seam was not reached');
        $trace = $probe->restore_trace();
        if ($failure !== 'restore_readback') { $trace[] = 'save:' . RollbackStoreFixture::REVERSE; }
        if ($failure === 'guard_save') { $trace[] = 'save:' . $probe->identity['guard_id']; }
        $probe->require_trace([...$trace, 'ROLLBACK']);
        require_condition($probe->fixture->sql->meta_rows === $rows && $probe->fixture->sql->operations === $operations &&
            $probe->fixture->invalidated === [], 'tail failure retained fields/receipt/guard/reservation or invalidated cache');
        require_condition(count($probe->fixture->sql->nonces) === $nonces + 1, 'failed authenticated attempt lost nonce claim');
        $probe->require_original();
        $probe->faults->failure = '';
        $receipt = $probe->send($probe->reverse);
        $probe->require_trace([...$probe->restore_trace(), 'save:' . RollbackStoreFixture::REVERSE, 'save:' . $probe->identity['guard_id'], 'COMMIT']);
        require_condition(sm_v2_canonical($receipt['after']) === sm_v2_canonical($probe->original['before']) &&
            $probe->fixture->invalidated === [7], 'retry lost legacy bytes or post-commit cache invalidation');
        $probe->require_restored();
    };
}
foreach (['lost_commit', 'cache_failure'] as $failure) {
    $tests['rollback ' . $failure . ' recovers same signed receipt before target and rejects new id'] = function () use ($failure): void {
        $probe = new RollbackStoreFixture();
        $probe->faults->failure = $failure === 'lost_commit' ? $failure : '';
        $probe->fixture->fail_cache = $failure === 'cache_failure';
        require_store_error(fn() => $probe->send($probe->reverse), 'needs_reconciliation');
        require_condition($failure !== 'lost_commit' || $probe->faults->triggered, 'post-commit fault was not reached');
        $trace = [...$probe->restore_trace(), 'save:' . RollbackStoreFixture::REVERSE, 'save:' . $probe->identity['guard_id'], 'COMMIT'];
        $probe->require_trace($failure === 'lost_commit' ? [...$trace, 'ROLLBACK'] : $trace);
        $probe->require_restored();
        $operations = $probe->fixture->sql->operations;
        $durable = $operations[RollbackStoreFixture::REVERSE]['receipt'];
        $probe->faults->failure = '';
        $probe->fixture->fail_cache = false;
        $probe->fixture->sql->meta_rows[0]['meta_value'] = 'later manual drift';
        $drifted = $probe->fixture->sql->meta_rows;
        $receipt = $probe->send($probe->reverse);
        $probe->require_trace(['START TRANSACTION', 'reserve:' . RollbackStoreFixture::REVERSE, 'lock:' . RollbackStoreFixture::REVERSE, 'COMMIT']);
        require_condition(sm_v2_canonical($receipt) === $durable && $receipt['operation_type'] === 'rollback' &&
            $probe->fixture->sql->meta_rows === $drifted && $probe->fixture->sql->operations === $operations &&
            $probe->fixture->invalidated === [7], 'same-id recovery changed receipt, repeated restore or failed cache healing');
        $probe->fixture->sql->meta_rows = $probe->applied_rows;
        $other = $probe->reverse;
        $other['operation_id'] = RollbackStoreFixture::OTHER;
        require_store_error(fn() => $probe->send($other), 'rollback_already_consumed');
        $probe->require_trace([
            'START TRANSACTION', 'reserve:' . RollbackStoreFixture::OTHER, 'lock:' . RollbackStoreFixture::OTHER,
            'read:' . RollbackStoreFixture::ORIGINAL, 'reserve:' . $probe->identity['guard_id'], 'lock:' . $probe->identity['guard_id'],
            'read:' . RollbackStoreFixture::REVERSE, 'ROLLBACK',
        ]);
        require_condition($probe->fixture->sql->meta_rows === $probe->applied_rows &&
            $probe->fixture->sql->operations === $operations && $probe->fixture->invalidated === [7], 'consumed guard reached target or leaked new reservation');
        $probe->require_original();
    };
}
foreach (['project_id', 'site_id', 'entity_id', 'original_operation_id', 'original_receipt_id', 'original_request_hash'] as $reference) {
    $tests['correctly signed guard with mismatched ' . $reference . ' reconciles before target'] = function () use ($reference): void {
        $probe = new RollbackStoreFixture();
        $probe->send($probe->reverse);
        $probe->require_restored();
        $probe->resign_guard_reference($reference);
        $operations = $probe->fixture->sql->operations;
        $rows = $probe->fixture->sql->meta_rows;
        $other = $probe->reverse;
        $other['operation_id'] = RollbackStoreFixture::OTHER;
        require_store_error(fn() => $probe->send($other), 'needs_reconciliation');
        $probe->require_trace([
            'START TRANSACTION', 'reserve:' . RollbackStoreFixture::OTHER, 'lock:' . RollbackStoreFixture::OTHER,
            'read:' . RollbackStoreFixture::ORIGINAL, 'reserve:' . $probe->identity['guard_id'], 'lock:' . $probe->identity['guard_id'], 'ROLLBACK',
        ]);
        require_condition($probe->fixture->sql->operations === $operations && $probe->fixture->sql->meta_rows === $rows &&
            $probe->fixture->invalidated === [7], 'signed identity mismatch changed ledger/fields/cache or leaked reservation');
        $probe->require_original();
    };
}
$tests['cache scheduling is transaction-only bounded deduplicated and rollback-safe'] = function (): void {
    $fixture = new StoreFixture();
    require_store_error(fn() => $fixture->store->schedule_cache_invalidation(7), 'needs_reconciliation');
    require_condition($fixture->sql->calls === [], 'outside-transaction scheduling reached SQL');
    $fixture->store->begin();
    $calls = $fixture->sql->calls;
    foreach ([0, -1, 2147483648] as $entity_id) {
        require_store_error(fn() => $fixture->store->schedule_cache_invalidation($entity_id), 'invalid_schema');
    }
    $fixture->store->schedule_cache_invalidation(7);
    $fixture->store->schedule_cache_invalidation(7);
    require_condition($fixture->sql->calls === $calls && $fixture->cache_attempts === [], 'scheduling performed target SQL or callback before commit');
    $fixture->store->rollback();
    $fixture->store->begin();
    $fixture->store->commit();
    require_condition($fixture->cache_attempts === [], 'rollback retained scheduled callback');
    $fixture->store->begin();
    $fixture->store->schedule_cache_invalidation(7);
    $fixture->store->schedule_cache_invalidation(7);
    $fixture->store->schedule_cache_invalidation(2147483647);
    $fixture->store->commit();
    require_condition($fixture->invalidated === [7, 2147483647] && $fixture->sql->meta_rows === [] &&
        $fixture->sql->operations === [], 'deduplication or scheduling-only semantics changed');
};
$tests['scheduled cache callbacks wait for confirmed COMMIT and resume after exception'] = function (): void {
    $fixture = new StoreFixture();
    $fixture->store->begin();
    $fixture->store->schedule_cache_invalidation(7);
    $fixture->sql->fail_commit = true;
    require_store_error(fn() => $fixture->store->commit(), 'needs_reconciliation');
    require_condition($fixture->cache_attempts === [], 'failed COMMIT invoked cache callback');
    $fixture->store->rollback();
    $fixture->sql->fail_commit = false;
    $fixture->store->begin();
    $fixture->store->commit();
    require_condition($fixture->cache_attempts === [], 'failed transaction leaked scheduled cache callback');
    $fixture->store->begin();
    $fixture->store->schedule_cache_invalidation(7);
    $fixture->fail_cache = true;
    require_store_error(fn() => $fixture->store->commit(), 'needs_reconciliation');
    $fixture->store->rollback();
    $fixture->fail_cache = false;
    $fixture->store->begin();
    $fixture->store->schedule_cache_invalidation(7);
    $fixture->store->commit();
    require_condition($fixture->cache_attempts === [7, 7] && $fixture->invalidated === [7], 'cache exception was hidden or explicit retry did not resume');
};
foreach (['status_apply', 'status_rollback', 'retry_apply', 'retry_rollback'] as $path) {
    $tests['verified ' . $path . ' heals after repeated cache failures without target or ledger mutations'] = function () use ($path): void {
        $probe = new RollbackStoreFixture();
        $is_rollback = str_ends_with($path, 'rollback');
        if ($is_rollback) { $probe->send($probe->reverse); }
        $operation = $is_rollback ? RollbackStoreFixture::REVERSE : RollbackStoreFixture::ORIGINAL;
        $recover = str_starts_with($path, 'status') ? fn() => $probe->status($operation) :
            ($is_rollback ? fn() => $probe->send($probe->reverse) : fn() => $probe->send($probe->apply, '/seo-master/v2/apply'));
        $operations = $probe->fixture->sql->operations;
        $probe->fixture->sql->meta_rows[0]['meta_value'] = 'later manual drift';
        $rows = $probe->fixture->sql->meta_rows;
        $probe->fixture->cache_attempts = $probe->fixture->invalidated = [];
        $probe->fixture->fail_cache = true;
        for ($attempt = 1; $attempt <= 2; ++$attempt) {
            require_store_error($recover, 'needs_reconciliation');
            require_condition(count($probe->fixture->cache_attempts) === $attempt && $probe->fixture->invalidated === [],
                'recovery did not resume post-commit callback or falsely reported cache success');
        }
        $probe->fixture->fail_cache = false;
        $receipt = $recover();
        $probe->require_trace(str_starts_with($path, 'status') ? ['START TRANSACTION', 'read:' . $operation, 'COMMIT'] :
            ['START TRANSACTION', 'reserve:' . $operation, 'lock:' . $operation, 'COMMIT']);
        require_condition($probe->fixture->cache_attempts === [7, 7, 7] && $probe->fixture->invalidated === [7] &&
            sm_v2_canonical($receipt) === $operations[$operation]['receipt'] &&
            $probe->fixture->sql->operations === $operations && $probe->fixture->sql->meta_rows === $rows,
            'recovery changed receipt/ledger/fields or did not schedule current-entity cache invalidation');
        $probe->require_original();
    };
}
$tests['no-op apply and rollback recovery do not invalidate cache for unrelated changes'] = function (): void {
    $probe = new RollbackStoreFixture(true);
    require_condition($probe->original['outcome'] === 'no_op' && $probe->original['applied_fields'] === [], 'fixture apply is not no-op');
    $receipt = $probe->send($probe->reverse);
    require_condition($receipt['outcome'] === 'no_op' && $receipt['applied_fields'] === [], 'fixture rollback is not no-op');
    $operations = $probe->fixture->sql->operations;
    $probe->fixture->sql->meta_rows[0]['meta_value'] = 'unrelated later value';
    $rows = $probe->fixture->sql->meta_rows;
    $probe->fixture->fail_cache = true;
    foreach ([
        fn() => $probe->send($probe->apply, '/seo-master/v2/apply'), fn() => $probe->send($probe->reverse),
        fn() => $probe->status(RollbackStoreFixture::ORIGINAL), fn() => $probe->status(RollbackStoreFixture::REVERSE),
    ] as $recover) { require_condition($recover()['outcome'] === 'no_op', 'no-op recovery changed outcome'); }
    require_condition($probe->fixture->cache_attempts === [] && $probe->fixture->sql->operations === $operations &&
        $probe->fixture->sql->meta_rows === $rows, 'no-op recovery invalidated cache or restored unrelated later changes');
    $probe->require_original();
};
$failures = [];
foreach ($tests as $name => $test) {
    try { $test(); } catch (Throwable $error) { $failures[] = "$name: {$error->getMessage()}"; }
}
foreach ($failures as $failure) { fwrite(STDERR, "FAIL: $failure\n"); }
printf("Actual store contracts: %d/%d passed (scripted SQL adapter, NOT MySQL/WP/lock evidence)\n", count($tests) - count($failures), count($tests));
exit($failures === [] ? 0 : 1);
