<?php
declare(strict_types=1);

define('ABSPATH', __DIR__);
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-protocol.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-service.php';

// Deliberately an in-memory contract double, NOT proof of WP/InnoDB/parallel/crash safety.
final class MemoryStore implements SM_V2_Target_Store {
    public string $target_url = 'https://example.com/catalog';
    public ?Closure $target_lock_callback = null;
    public function assert_target_url(int $entity_id, string $url): void {
        if ($this->target_lock_callback !== null) { ($this->target_lock_callback)(); }
        if ($url !== $this->target_url) { throw new SM_V2_Error('target_url_conflict', 409); }
    }
    public array $fields = ['title' => ['state' => 'missing'], 'description' => ['state' => 'missing'], 'jsonld' => ['state' => 'missing']];
    public array $operations = [];
    public array $nonces = [];
    public array $events = [];
    public int $writes = 0;
    public bool $fail_write = false;
    public bool $fail_storage = false;
    public bool $lose_commit_response = false;
    public bool $fail_rollback = false;
    public array $invalidated = [];
    private array $scheduled_cache = [];
    private bool $in_transaction = false;
    public string $post_status = 'publish';
    private ?array $backup = null;
    public function check_storage(): void {
        if ($this->fail_storage) { throw new SM_V2_Error('unsupported_storage', 503); }
    }
    public function restore_fields(int $entity_id, array $fields): void { $this->write_fields($entity_id, $fields); }
    public function claim_nonce(string $key_id, string $nonce, int $expires): void {
        $key = $key_id . ':' . $nonce;
        if (isset($this->nonces[$key])) { throw new SM_V2_Error('replay', 409); }
        $this->nonces[$key] = $expires;
    }
    public function begin(): void { $this->events[] = 'begin'; $this->in_transaction = true; $this->backup = [$this->fields, $this->operations, $this->writes]; }
    public function lock_operation(string $operation_id, string $request_hash): ?array {
        $this->events[] = 'lock:' . $operation_id;
        if (!isset($this->operations[$operation_id])) { return null; }
        if (!hash_equals($this->operations[$operation_id]['request_hash'], $request_hash)) {
            throw new SM_V2_Error('idempotency_conflict', 409);
        }
        return $this->operations[$operation_id]['receipt'];
    }
    public function snapshot(int $entity_id): array {
        $this->events[] = 'snapshot';
        if ($this->post_status !== 'publish') { throw new SM_V2_Error('target_not_found', 404); }
        return ['entity_id' => $entity_id, 'post_type' => 'post', 'post_status' => $this->post_status, 'fields' => $this->fields];
    }
    public function write_fields(int $entity_id, array $fields): void {
        $this->events[] = 'write';
        foreach ($fields as $field => $value) {
            $this->fields[$field] = $value;
            ++$this->writes;
            if ($this->fail_write) { throw new SM_V2_Error('storage_failure', 503); }
        }
    }
    public function save_operation(string $operation_id, string $request_hash, array $receipt): void {
        $this->operations[$operation_id] = ['request_hash' => $request_hash, 'receipt' => $receipt];
    }
    public function read_operation(string $operation_id): ?array { $this->events[] = 'read'; return $this->operations[$operation_id]['receipt'] ?? null; }
    public function schedule_cache_invalidation(int $entity_id): void {
        if (!$this->in_transaction) { throw new SM_V2_Error('needs_reconciliation', 503); }
        if ($entity_id < 1 || $entity_id > 2147483647) { throw new SM_V2_Error('invalid_schema', 400); }
        $this->events[] = 'schedule:' . $entity_id;
        $this->scheduled_cache[$entity_id] = true;
    }
    public function commit(): void {
        $this->events[] = 'commit';
        $this->backup = null;
        $this->in_transaction = false;
        if ($this->lose_commit_response) { throw new SM_V2_Error('needs_reconciliation', 503); }
        foreach (array_keys($this->scheduled_cache) as $entity_id) {
            $this->events[] = 'invalidate:' . $entity_id;
            $this->invalidated[] = $entity_id;
        }
        $this->scheduled_cache = [];
    }
    public function rollback(): void {
        if ($this->fail_rollback) { throw new SM_V2_Error('storage_failure', 503); }
        if ($this->backup !== null) { [$this->fields, $this->operations, $this->writes] = $this->backup; $this->backup = null; }
        $this->in_transaction = false;
        $this->scheduled_cache = [];
    }
}

final class ContractFixture {
    public const PROJECT = '11111111-1111-4111-8111-111111111111';
    public const SITE = '22222222-2222-4222-8222-222222222222';
    public const OPERATION = '33333333-3333-4333-8333-333333333333';
    public MemoryStore $store;
    public SM_V2_Service $service;
    public string $secret;
    public string $receipt_secret;
    public int $now = 1793715600;
    public function __construct() {
        $this->secret = bin2hex(random_bytes(32));
        $this->receipt_secret = bin2hex(random_bytes(32));
        $this->store = new MemoryStore();
        $config = new SM_V2_Config(self::PROJECT, self::SITE, 'test-key', $this->secret, $this->receipt_secret, true);
        $this->service = new SM_V2_Service($config, $this->store, '0.3.0-dev', fn(): int => $this->now);
    }
    public function command(): array {
        $before = $this->store->snapshot(7);
        $after = $before;
        $after['fields']['title'] = ['state' => 'value', 'value' => 'Точный "SEO" \\ title 😀'];
        $command = [
            'project_id' => self::PROJECT, 'site_id' => self::SITE, 'entity_id' => 7,
            'operation_id' => self::OPERATION, 'task_id' => '44444444-4444-4444-8444-444444444444',
            'proposal_version' => '55555555-5555-4555-8555-555555555555',
            'approval_ref' => '66666666-6666-4666-8666-666666666666',
            'before_digest' => sm_v2_digest($before), 'expected_after_digest' => sm_v2_digest($after),
            'changes' => [['op' => 'set', 'field' => 'title', 'value' => $after['fields']['title']['value']]],
        ];
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        return $command;
    }
    public function headers(string $method, string $route, string $body, string $operation = '-', ?string $nonce = null): array {
        $headers = [
            'x-seo-project-id' => self::PROJECT, 'x-seo-site-id' => self::SITE, 'x-seo-key-id' => 'test-key',
            'x-seo-timestamp' => '1793715600', 'x-seo-nonce' => $nonce ?? bin2hex(random_bytes(16)),
            'x-seo-operation-id' => $operation,
        ];
        $headers['x-seo-signature'] = hash_hmac('sha256', sm_v2_signing_message($method, $route, $body, $headers), $this->secret);
        return $headers;
    }
    public function send(array $command, string $route = '/seo-master/v2/apply'): array {
        $body = json_encode($command, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        return $this->service->handle('POST', $route, $body, $this->headers('POST', $route, $body, $command['operation_id'] ?? '-'), 1793715600);
    }
}
function expect_error(callable $action, string $code): void {
    try { $action(); } catch (SM_V2_Error $error) {
        if ($error->error_code !== $code) { throw new RuntimeException("Expected $code; got {$error->error_code}"); }
        return;
    }
    throw new RuntimeException("Expected $code, request succeeded");
}
function check(bool $condition, string $message): void {
    if (!$condition) { throw new RuntimeException($message); }
}
$tests = [
    'target-bound command checks URL under the write transaction' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $command['target_url'] = $fixture->store->target_url;
        $command['authorization_deadline'] = $fixture->now + 60;
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $fixture->store->target_url .= '/moved';
        expect_error(fn() => $fixture->send($command, '/seo-master/v2/target-bound/apply'), 'target_url_conflict');
        check($fixture->store->writes === 0 && $fixture->store->operations === [], 'URL drift wrote data');
    },
    'approval expiring while CMS locks wait never writes' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $command['target_url'] = $fixture->store->target_url;
        $command['authorization_deadline'] = $fixture->now + 60;
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $fixture->store->target_lock_callback = function () use ($fixture): void { $fixture->now += 60; };
        expect_error(fn() => $fixture->send($command, '/seo-master/v2/target-bound/apply'), 'authorization_expired');
        check($fixture->store->writes === 0 && $fixture->store->operations === [], 'expired command wrote data');
    },
    'target-bound replay returns original receipt after deadline without another write' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $command['target_url'] = $fixture->store->target_url;
        $command['authorization_deadline'] = $fixture->now + 60;
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $receipt = $fixture->send($command, '/seo-master/v2/target-bound/apply');
        $fixture->now += 61;
        check($fixture->send($command, '/seo-master/v2/target-bound/apply') === $receipt, 'replay changed receipt');
        check($fixture->store->writes === 1, 'replay wrote twice');
    },
    'ASCII numeric object guards preserve mixed Unicode empty and compound shapes without ctype' => function (): void {
        foreach (['0', '00', '01', '9', '1234567890', '18446744073709551616', str_repeat('9', 1024)] as $key) {
            $body = '{"' . $key . '":"value"}';
            expect_error(fn() => sm_v2_decode($body), 'invalid_schema');
            expect_error(fn() => sm_v2_decode('{"outer":' . $body . '}'), 'invalid_schema');
            expect_error(fn() => sm_v2_decode('{"outer":[' . $body . ']}'), 'invalid_schema');
        }
        foreach (['', '0a', 'a0', '+1', '1.0', '10e2', '0x1', '1 ', ' 1', '١', '１', '²', 'ключ😀'] as $key) {
            $body = json_encode((object) [$key => 'exact 😀'], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
            $decoded = sm_v2_decode($body);
            check(array_key_exists($key, $decoded) && $decoded[$key] === 'exact 😀', 'nondigit name was reclassified or normalized');
            expect_error(fn() => sm_v2_keys($decoded, ['expected_schema_key']), 'invalid_schema');
        }
        foreach ([
            '{"key":"bad\u0000value"}', '{"bad\u0000key":"value"}', "{\"key\":\"bad\0value\"}",
            '[]', 'null', '1', '{"changes":{}}', '{"changes":{"0":{"op":"remove","field":"title"}}}',
            '{"changes":[["remove","title"]]}', '{"outer":{"01":"value"}}',
        ] as $body) {
            expect_error(fn() => sm_v2_decode($body), 'invalid_schema');
        }
        check(sm_v2_decode('{}') === [] && sm_v2_decode('{"outer":{}}') === ['outer' => []], 'empty object conversion changed');
        $decoded = sm_v2_decode('{"changes":[{"op":"remove","field":"title"}]}');
        check(sm_v2_validate_changes($decoded['changes']) === ['title' => ['state' => 'missing']], 'real operation objects stopped working');
    },
    'ten retries + lost response return identical durable signed receipt' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $receipt = $fixture->send($command);
        for ($attempt = 0; $attempt < 10; ++$attempt) { check($fixture->send($command) === $receipt, 'retry changed receipt'); }
        check($fixture->store->writes === 1, 'duplicate mutation');
        $route = '/seo-master/v2/operations/' . ContractFixture::OPERATION;
        $fixture->store->events = [];
        check($fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', ContractFixture::OPERATION), 1793715600) === $receipt, 'status lost receipt');
        check($fixture->store->events === ['begin', 'read', 'schedule:7', 'commit', 'invalidate:7'], 'verified status did not schedule cache invalidation within transaction after receipt read');
        $signature = $receipt['signature'];
        unset($receipt['signature']);
        check(hash_equals(sm_v2_receipt_signature($receipt, $fixture->receipt_secret), $signature), 'receipt signature invalid');
        check($receipt['before']['fields']['title'] === ['state' => 'missing'], 'lost missing state');
        check($receipt['after']['fields']['title']['value'] === $command['changes'][0]['value'], 'lost exact Unicode/slashes');
    },
    'same operation different bytes gives 409 before mutation' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $fixture->send($command);
        $command['approval_ref'] = '77777777-7777-4777-8777-777777777777';
        expect_error(fn() => $fixture->send($command), 'idempotency_conflict');
        check($fixture->store->writes === 1, 'conflict wrote fields');
    },
    'durable replay guard rejects same transport nonce' => function (): void {
        $fixture = new ContractFixture();
        $route = '/seo-master/v2/snapshot';
        $body = json_encode(['project_id' => ContractFixture::PROJECT, 'site_id' => ContractFixture::SITE, 'entity_id' => 7], JSON_THROW_ON_ERROR);
        $headers = $fixture->headers('POST', $route, $body);
        $fixture->service->handle('POST', $route, $body, $headers, 1793715600);
        expect_error(fn() => $fixture->service->handle('POST', $route, $body, $headers, 1793715600), 'replay');
    },
    'signature scope freshness and route fail closed' => function (): void {
        $fixture = new ContractFixture();
        $route = '/seo-master/v2/snapshot';
        $body = '{}';
        foreach (['x-seo-signature', 'x-seo-project-id', 'x-seo-site-id', 'x-seo-key-id', 'x-seo-operation-id'] as $header) {
            $headers = $fixture->headers('POST', $route, $body);
            $headers[$header] = str_repeat('0', 64);
            expect_error(fn() => $fixture->service->handle('POST', $route, $body, $headers, 1793715600), 'authentication_failed');
        }
        expect_error(fn() => $fixture->service->handle('POST', $route, $body, $fixture->headers('POST', $route, $body), 1793716000), 'authentication_failed');
        expect_error(fn() => $fixture->service->handle('POST', $route . '?x=1', $body, [], 1793715600), 'invalid_route');
    },
    'dry run leaves all business state and operation ledger unchanged' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $before = $fixture->store->fields;
        $result = $fixture->send($command, '/seo-master/v2/dry-run');
        check($result['outcome'] === 'ready', 'dry-run not ready');
        check($fixture->store->fields === $before && $fixture->store->operations === [] && $fixture->store->writes === 0, 'dry-run mutated');
    },
    'CAS conflict and expected-after mismatch write nothing' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $fixture->store->fields['description'] = ['state' => 'value', 'value' => 'Manual edit'];
        expect_error(fn() => $fixture->send($command), 'precondition_conflict');
        check($fixture->store->writes === 0 && $fixture->store->operations === [], 'CAS mutation');
        $command = $fixture->command();
        $command['expected_after_digest'] = str_repeat('0', 64);
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        expect_error(fn() => $fixture->send($command), 'after_digest_conflict');
    },
    'partial write failure rolls back fields and ledger' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $before = $fixture->store->fields;
        $fixture->store->fail_write = true;
        expect_error(fn() => $fixture->send($command), 'storage_failure');
        check($fixture->store->fields === $before && $fixture->store->operations === [], 'rollback incomplete');
        $fixture->store->fail_write = false;
        check($fixture->send($command)['outcome'] === 'applied_unverified', 'retry after rollback failed');
    },
    'unsupported storage and unpublished target reject' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $fixture->store->fail_storage = true;
        expect_error(fn() => $fixture->send($command), 'unsupported_storage');
        $fixture->store->fail_storage = false;
        $fixture->store->post_status = 'draft';
        expect_error(fn() => $fixture->send($command), 'target_not_found');
    },
    'strict schema rejects malformed changes before any write' => function (): void {
        $invalid_changes = [
            [['op' => 'unknown', 'field' => 'title', 'value' => 'X']],
            [['op' => 'set', 'field' => 'h1', 'value' => 'X']],
            [['op' => 'set', 'field' => 'title']],
            [['op' => 'set', 'field' => 'title', 'value' => '']],
            [['op' => 'set', 'field' => 'title', 'value' => null]],
            [['op' => 'set', 'field' => 'title', 'value' => 42]],
            [['op' => 'set', 'field' => 'title', 'value' => "nul\0byte"]],
            [['op' => 'set', 'field' => 'title', 'value' => '<img src=x onerror=alert(1)>']],
            [['op' => 'set', 'field' => 'title', 'value' => 'O:8:"Injected":0:{}']],
            [['op' => 'set', 'field' => 'description', 'value' => str_repeat('x', 2001)]],
            [['op' => 'remove', 'field' => 'title', 'value' => 'unexpected']],
            [['op' => 'set', 'field' => 'title', 'value' => 'X', 'extra' => true]],
            [['op' => 'set', 'field' => 'title', 'value' => 'X'], ['op' => 'remove', 'field' => 'title']],
            [['op' => 'set', 'field' => 'jsonld', 'value' => '{"@context":"https://schema.org","@type":"WebPage","name":"</script>"}']],
            [['op' => 'set', 'field' => 'jsonld', 'value' => '{"@context":"https://schema.org","@type":"WebPage","extra":"X"}']],
        ];
        foreach ($invalid_changes as $changes) {
            $fixture = new ContractFixture();
            $command = $fixture->command();
            $command['changes'] = $changes;
            $command['proposal_hash'] = sm_v2_proposal_hash($command);
            expect_error(fn() => $fixture->send($command), 'invalid_schema');
            check($fixture->store->writes === 0 && $fixture->store->operations === [], 'invalid input wrote fields');
        }
    },
    'proposal hash and body identity cannot be substituted' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $command['proposal_hash'] = str_repeat('0', 64);
        expect_error(fn() => $fixture->send($command), 'proposal_hash_conflict');
        $command = $fixture->command();
        $command['site_id'] = '77777777-7777-4777-8777-777777777777';
        expect_error(fn() => $fixture->send($command), 'scope_mismatch');
    },
    'incomplete pairing or absent trusted gateway cannot enable publication' => function (): void {
        expect_error(fn() => new SM_V2_Config('', ContractFixture::SITE, 'key', bin2hex(random_bytes(32)), bin2hex(random_bytes(32)), true), 'not_paired');
        $fixture = new ContractFixture();
        $config = new SM_V2_Config(ContractFixture::PROJECT, ContractFixture::SITE, 'test-key', $fixture->secret, $fixture->receipt_secret, false);
        $fixture->service = new SM_V2_Service($config, $fixture->store, '0.3.0-dev');
        expect_error(fn() => $fixture->send($fixture->command()), 'trusted_gateway_required');
        check($fixture->send($fixture->command(), '/seo-master/v2/dry-run')['outcome'] === 'ready', 'publication flag disabled safe dry-run');
    },
    'no-op and explicit remove preserve exact missing/value semantics' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $command['changes'] = [['op' => 'remove', 'field' => 'title']];
        $command['expected_after_digest'] = $command['before_digest'];
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        check($fixture->send($command)['outcome'] === 'no_op' && $fixture->store->writes === 0, 'no-op caused a write');
        $fixture = new ContractFixture();
        $fixture->store->fields['title'] = ['state' => 'value', 'value' => ''];
        $command = $fixture->command();
        $command['changes'] = [['op' => 'remove', 'field' => 'title']];
        $after = $fixture->store->snapshot(7);
        $after['fields']['title'] = ['state' => 'missing'];
        $command['expected_after_digest'] = sm_v2_digest($after);
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $receipt = $fixture->send($command);
        check($receipt['before']['fields']['title'] === ['state' => 'value', 'value' => ''], 'empty was confused with missing');
        check($receipt['after']['fields']['title'] === ['state' => 'missing'], 'remove failed');
    },
    'ambiguous commit is not reported applied and same-op status recovers' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $fixture->store->lose_commit_response = true;
        expect_error(fn() => $fixture->send($command), 'needs_reconciliation');
        check($fixture->store->writes === 1, 'simulated commit not durable');
        $fixture->store->lose_commit_response = false;
        $route = '/seo-master/v2/operations/' . ContractFixture::OPERATION;
        $receipt = $fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', ContractFixture::OPERATION), 1793715600);
        check($receipt['outcome'] === 'applied_unverified', 'lost-response receipt missing');
        check($fixture->send($command) === $receipt && $fixture->store->writes === 1, 'ambiguous commit replayed');
    },
    'rollback failure is explicitly needs_reconciliation' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $fixture->store->fail_write = true;
        $fixture->store->fail_rollback = true;
        expect_error(fn() => $fixture->send($command), 'needs_reconciliation');
    },
    'duplicate escaped JSON keys and malformed bodies reject' => function (): void {
        foreach (['{"name":"a","name":"b"}', '{"name":"a","\\u006eame":"b"}', '{"outer":{"x":1,"x":2}}', '[]', 'null', '{"x":NaN}', '{"x":"\\u0000"}'] as $body) {
            expect_error(fn() => sm_v2_decode($body), 'invalid_schema');
        }
        check(sm_v2_decode('{"name":"x,y:{z}","nested":{"name":"fine"}}')['name'] === 'x,y:{z}', 'valid JSON parser rejected quoted punctuation');
    },
    'challenge is signed scoped expiring and non-mutating' => function (): void {
        $fixture = new ContractFixture();
        $route = '/seo-master/v2/challenge';
        $body = json_encode(['project_id' => ContractFixture::PROJECT, 'site_id' => ContractFixture::SITE, 'challenge' => bin2hex(random_bytes(32))], JSON_THROW_ON_ERROR);
        $challenge = $fixture->service->handle('POST', $route, $body, $fixture->headers('POST', $route, $body), 1793715600);
        $signature = $challenge['signature'];
        unset($challenge['signature']);
        check(hash_equals($signature, sm_v2_receipt_signature($challenge, $fixture->receipt_secret)), 'challenge signature invalid');
        check(strtotime($challenge['expires_at']) - strtotime($challenge['issued_at']) === 60, 'challenge expiry');
        check($fixture->store->writes === 0 && $fixture->store->operations === [], 'challenge mutated business state');
    },
    'JSONLD roundtrip preserves JSON quotes and slashes and integration guard' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $json = '{"@context":"https://schema.org","@type":"Article","name":"Текст \\"quote\\" \\\\ path 😀"}';
        $command['changes'][] = ['op' => 'set', 'field' => 'jsonld', 'value' => $json];
        $after = $fixture->store->snapshot(7);
        $after['fields']['title'] = ['state' => 'value', 'value' => $command['changes'][0]['value']];
        $after['fields']['jsonld'] = ['state' => 'value', 'value' => $json];
        $command['expected_after_digest'] = sm_v2_digest($after);
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $receipt = $fixture->send($command);
        check($receipt['after']['fields']['jsonld']['value'] === $json, 'JSON string changed');
        $fixture = new ContractFixture();
        $config = new SM_V2_Config(ContractFixture::PROJECT, ContractFixture::SITE, 'test-key', $fixture->secret, $fixture->receipt_secret, true, false);
        $fixture->service = new SM_V2_Service($config, $fixture->store, '0.3.0-dev');
        expect_error(fn() => $fixture->send($command), 'unsupported_field');
    },
    'raw numeric-key objects are not command arrays even with a matching proposal hash' => function (): void {
        foreach ([false, true] as $is_reordered) {
            $fixture = new ContractFixture();
            $command = $fixture->command();
            if ($is_reordered) {
                $command['changes'][] = ['op' => 'remove', 'field' => 'description'];
                $command['proposal_hash'] = sm_v2_proposal_hash($command);
            }
            $flags = JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR;
            $changes = json_encode($command['changes'], $flags);
            $numeric_object = $is_reordered ?
                '{"1":' . json_encode($command['changes'][1], $flags) . ',"0":' . json_encode($command['changes'][0], $flags) . '}' :
                '{"0":' . json_encode($command['changes'][0], $flags) . '}';
            $body = str_replace('"changes":' . $changes, '"changes":' . $numeric_object, json_encode($command, $flags));
            $route = '/seo-master/v2/apply';
            expect_error(fn() => $fixture->service->handle('POST', $route, $body, $fixture->headers('POST', $route, $body, ContractFixture::OPERATION), 1793715600), 'invalid_schema');
            check($fixture->store->writes === 0 && $fixture->store->operations === [], 'numeric object caused a mutation');
        }
    },
    'tampered durable receipt is never returned as authentic' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $fixture->send($command);
        $fixture->store->operations[ContractFixture::OPERATION]['receipt']['entity_id'] = 99;
        expect_error(fn() => $fixture->send($command), 'needs_reconciliation');
    },
];
function rollback_hash(array $command): string {
    unset($command['operation_id'], $command['approval_ref'], $command['proposal_hash']);
    return hash('sha256', "SEO-MASTER-ROLLBACK-PROPOSAL-V2\n" . sm_v2_canonical($command));
}
function rollback_command(array $original, string $operation = '77777777-7777-4777-8777-777777777777'): array {
    $command = [
        'project_id' => $original['project_id'], 'site_id' => $original['site_id'], 'entity_id' => $original['entity_id'],
        'operation_id' => $operation, 'task_id' => '88888888-8888-4888-8888-888888888888',
        'proposal_version' => '99999999-9999-4999-8999-999999999999', 'approval_ref' => 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'reason' => 'Одобренное восстановление точного before',
        'original_operation_id' => $original['operation_id'], 'original_receipt_id' => $original['receipt_id'],
        'original_request_hash' => $original['request_hash'], 'original_proposal_hash' => $original['proposal_hash'],
        'before_digest' => $original['after_digest'], 'expected_after_digest' => $original['before_digest'],
    ];
    $command['proposal_hash'] = rollback_hash($command);
    return $command;
}
function resign_original(ContractFixture $fixture): array {
    $original = $fixture->store->operations[ContractFixture::OPERATION]['receipt'];
    unset($original['signature']);
    $original['signature'] = sm_v2_receipt_signature($original, $fixture->receipt_secret);
    $fixture->store->operations[ContractFixture::OPERATION]['receipt'] = $original;
    return $original;
}
function guard_id(ContractFixture $fixture): string {
    $guards = array_filter($fixture->store->operations, fn(array $entry): bool => ($entry['receipt']['kind'] ?? null) === 'rollback_guard');
    check(count($guards) === 1, 'exactly one durable guard required');
    return array_key_first($guards);
}
$tests += [
    'rollback restores missing empty Unicode and bounded legacy states exactly' => function (): void {
        foreach ([['state' => 'missing'], ['state' => 'value', 'value' => ''], ['state' => 'value', 'value' => "😀 old \\\\ \"quote\""], ['state' => 'value', 'value' => str_repeat('L', 1024)]] as $state) {
            $fixture = new ContractFixture();
            $fixture->store->fields['title'] = $state;
            $original = $fixture->send($fixture->command());
            $immutable = $fixture->store->operations[ContractFixture::OPERATION];
            $receipt = $fixture->send(rollback_command($original), '/seo-master/v2/rollback');
            check($receipt['outcome'] === 'rollback_applied_unverified' && $receipt['operation_type'] === 'rollback', 'rollback mislabelled');
            check(sm_v2_canonical($fixture->store->fields) === sm_v2_canonical($original['before']['fields']), 'inverse lost exact legacy state');
            check($receipt['before'] === $original['after'] && $receipt['after'] === $original['before'], 'inverse snapshots incorrect');
            check($receipt['original_receipt_id'] === $original['receipt_id'], 'receipt linkage absent');
            check($fixture->store->operations[ContractFixture::OPERATION] === $immutable, 'original receipt changed');
            $identifier = guard_id($fixture);
            check(preg_match('/\A[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z/D', $identifier) === 1 && !sm_v2_uuid($identifier), 'guard lacks reserved RFC UUIDv8 namespace');
        }
    },
    'rollback dry-run writes neither fields operation nor guard' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $operations = $fixture->store->operations;
        $writes = $fixture->store->writes;
        $result = $fixture->send(rollback_command($original), '/seo-master/v2/rollback/dry-run');
        check($result['outcome'] === 'ready' && $result['after'] === $original['before'], 'rollback preview incorrect');
        check($fixture->store->operations === $operations && $fixture->store->writes === $writes &&
            sm_v2_canonical($fixture->store->fields) === sm_v2_canonical($original['after']['fields']), 'rollback dry-run mutated');
    },
    'same rollback id exact-body replay returns receipt before later drift CAS' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $command = rollback_command($original);
        $receipt = $fixture->send($command, '/seo-master/v2/rollback');
        $fixture->store->fields['title'] = ['state' => 'value', 'value' => 'Later manual edit'];
        $writes = $fixture->store->writes;
        for ($index = 0; $index < 10; ++$index) { check($fixture->send($command, '/seo-master/v2/rollback') === $receipt, 'rollback retry changed receipt'); }
        check($fixture->store->writes === $writes, 'rollback replay wrote');
        $command['approval_ref'] = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
        expect_error(fn() => $fixture->send($command, '/seo-master/v2/rollback'), 'idempotency_conflict');
    },
    'durable single-use guard blocks new rollback id after return to original after' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $fixture->send(rollback_command($original), '/seo-master/v2/rollback');
        $fixture->store->fields = $original['after']['fields'];
        $writes = $fixture->store->writes;
        $fixture->store->events = [];
        expect_error(fn() => $fixture->send(rollback_command($original, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'), '/seo-master/v2/rollback'), 'rollback_already_consumed');
        check($fixture->store->writes === $writes && $fixture->store->fields === $original['after']['fields'], 'guard allowed second reversal');
        check(!in_array('snapshot', $fixture->store->events, true) && !in_array('write', $fixture->store->events, true), 'consumed guard checked after target access');
    },
    'rollback whole-snapshot drift rejects before any field or guard persists' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $fixture->store->fields['description'] = ['state' => 'value', 'value' => 'Manual change'];
        $operations = $fixture->store->operations;
        expect_error(fn() => $fixture->send(rollback_command($original), '/seo-master/v2/rollback'), 'precondition_conflict');
        check($fixture->store->operations === $operations && $fixture->store->writes === 1, 'conflict left a reservation');
    },
    'unsafe before blocks new apply but ordinary set still rejects empty and oversize' => function (): void {
        foreach (['O:8:"Injected":0:{}', 'a:0:{}', 'N;', '<script>unsafe</script>', "bad\x01value", str_repeat('X', 8193)] as $unsafe) {
            $fixture = new ContractFixture();
            $fixture->store->fields['description'] = ['state' => 'value', 'value' => $unsafe];
            expect_error(fn() => $fixture->send($fixture->command()), 'unsupported_snapshot');
            check($fixture->store->writes === 0 && $fixture->store->operations === [], 'irreversible before was applied');
        }
        foreach (['', str_repeat('X', 513)] as $value) {
            $fixture = new ContractFixture();
            $command = $fixture->command();
            $command['changes'][0]['value'] = $value;
            $command['proposal_hash'] = sm_v2_proposal_hash($command);
            expect_error(fn() => $fixture->send($command), 'invalid_schema');
        }
    },
    'historical unsupported original fails closed without restoring gadgets' => function (): void {
        $fixture = new ContractFixture();
        $fixture->send($fixture->command());
        $fixture->store->operations[ContractFixture::OPERATION]['receipt']['before']['fields']['title'] = ['state' => 'value', 'value' => 'O:8:"Injected":0:{}'];
        $original = $fixture->store->operations[ContractFixture::OPERATION]['receipt'];
        $fixture->store->operations[ContractFixture::OPERATION]['receipt']['before_digest'] = sm_v2_digest($original['before']);
        $original = resign_original($fixture);
        expect_error(fn() => $fixture->send(rollback_command($original), '/seo-master/v2/rollback'), 'unsupported_snapshot');
        check($fixture->store->writes === 1 && count($fixture->store->operations) === 1, 'historical unsafe state restored');
    },
    'rollback schema self-reference inverse injection and proposal domain reject' => function (): void {
        foreach (['reason', 'inverse', 'self', 'reserved', 'domain'] as $case) {
            $fixture = new ContractFixture();
            $original = $fixture->send($fixture->command());
            $command = rollback_command($original);
            if ($case === 'reason') { $command['reason'] = ''; }
            if ($case === 'inverse') { $command['inverse'] = ['title' => 'caller supplied']; }
            if ($case === 'self') { $command['original_operation_id'] = $command['operation_id']; }
            if ($case === 'reserved') { $command['original_operation_id'] = '12345678-1234-8234-8234-123456789abc'; }
            $command['proposal_hash'] = $case === 'domain' ? sm_v2_proposal_hash($command) : rollback_hash($command);
            expect_error(fn() => $fixture->send($command, '/seo-master/v2/rollback'), $case === 'domain' ? 'proposal_hash_conflict' : 'invalid_schema');
            check($fixture->store->writes === 1 && count($fixture->store->operations) === 1, 'invalid rollback wrote');
        }
    },
    'rollback original references scope target and tampered original fail closed' => function (): void {
        foreach (['missing', 'reference', 'entity', 'scope', 'signature'] as $case) {
            $fixture = new ContractFixture();
            $original = $fixture->send($fixture->command());
            $command = rollback_command($original);
            if ($case === 'missing') { $command['original_operation_id'] = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'; }
            if ($case === 'reference') { $command['original_receipt_id'] = str_repeat('0', 64); }
            if ($case === 'entity') { $command['entity_id'] = 8; }
            if ($case === 'scope') { $fixture->store->operations[ContractFixture::OPERATION]['receipt']['site_id'] = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'; }
            if ($case === 'signature') { $fixture->store->operations[ContractFixture::OPERATION]['receipt']['signature'] = str_repeat('0', 64); }
            $command['proposal_hash'] = rollback_hash($command);
            $code = $case === 'signature' ? 'needs_reconciliation' : ($case === 'reference' ? 'original_reference_conflict' : 'operation_not_found');
            expect_error(fn() => $fixture->send($command, '/seo-master/v2/rollback'), $code);
            check($fixture->store->writes === 1, 'bad original wrote fields');
        }
    },
    'signed but semantically malformed original is not inverse authority' => function (): void {
        foreach (['digest', 'state', 'proposal', 'applied_fields'] as $case) {
            $fixture = new ContractFixture();
            $command = rollback_command($fixture->send($fixture->command()));
            $original =& $fixture->store->operations[ContractFixture::OPERATION]['receipt'];
            if ($case === 'digest') { $original['before_digest'] = str_repeat('0', 64); }
            if ($case === 'state') { $original['before']['fields']['title'] = ['state' => 'unknown']; }
            if ($case === 'proposal') { $original['proposal_hash'] = 'invalid'; }
            if ($case === 'applied_fields') { $original['applied_fields'] = ['title', 'title']; }
            unset($original);
            resign_original($fixture);
            expect_error(fn() => $fixture->send($command, '/seo-master/v2/rollback'), 'needs_reconciliation');
            check($fixture->store->writes === 1, 'malformed original wrote');
        }
    },
    'guard context collision corrupt record and missing first rollback reconcile' => function (): void {
        foreach (['context', 'kind', 'identity', 'missing_receipt', 'hash_collision'] as $case) {
            $fixture = new ContractFixture();
            $original = $fixture->send($fixture->command());
            $command = rollback_command($original);
            $fixture->send($command, '/seo-master/v2/rollback');
            $identifier = guard_id($fixture);
            $guard =& $fixture->store->operations[$identifier]['receipt'];
            if ($case === 'kind') { $guard['kind'] = 'receipt'; }
            if ($case === 'identity') { $guard['identity_digest'] = str_repeat('0', 64); }
            if ($case === 'context') {
                $unsigned = $guard;
                unset($unsigned['signature']);
                $guard['signature'] = sm_v2_receipt_signature($unsigned, $fixture->receipt_secret);
            }
            if ($case === 'missing_receipt') { unset($fixture->store->operations[$command['operation_id']]); }
            if ($case === 'hash_collision') { $fixture->store->operations[$identifier]['request_hash'] = str_repeat('0', 64); }
            unset($guard);
            $fixture->store->fields = $original['after']['fields'];
            expect_error(fn() => $fixture->send(rollback_command($original, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'), '/seo-master/v2/rollback'), 'needs_reconciliation');
            check($fixture->store->writes === 2, 'corrupt guard allowed a write');
        }
    },
    'failed reverse write rolls back reverse receipt guard and every field' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $operations = $fixture->store->operations;
        $fixture->store->fail_write = true;
        expect_error(fn() => $fixture->send(rollback_command($original), '/seo-master/v2/rollback'), 'storage_failure');
        check(sm_v2_canonical($fixture->store->fields) === sm_v2_canonical($original['after']['fields']) &&
            $fixture->store->operations === $operations && $fixture->store->writes === 1, 'reverse rollback partial');
        $fixture->store->fail_write = false;
        check($fixture->send(rollback_command($original), '/seo-master/v2/rollback')['outcome'] === 'rollback_applied_unverified', 'safe retry after failed rollback rejected');
    },
    'ambiguous rollback commit recovers same signed receipt and consumed guard' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $command = rollback_command($original);
        $fixture->store->lose_commit_response = true;
        expect_error(fn() => $fixture->send($command, '/seo-master/v2/rollback'), 'needs_reconciliation');
        $fixture->store->lose_commit_response = false;
        $route = '/seo-master/v2/operations/' . $command['operation_id'];
        $receipt = $fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', $command['operation_id']), 1793715600);
        check($receipt['outcome'] === 'rollback_applied_unverified' && $fixture->send($command, '/seo-master/v2/rollback') === $receipt, 'rollback receipt not recovered');
        $fixture->store->fields = $original['after']['fields'];
        expect_error(fn() => $fixture->send(rollback_command($original, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'), '/seo-master/v2/rollback'), 'rollback_already_consumed');
        check($fixture->store->writes === 2, 'lost commit caused second mutation');
    },
    'no-op rollback consumes its original without inflating mutations' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $command['changes'] = [['op' => 'remove', 'field' => 'title']];
        $command['expected_after_digest'] = $command['before_digest'];
        $command['proposal_hash'] = sm_v2_proposal_hash($command);
        $original = $fixture->send($command);
        $receipt = $fixture->send(rollback_command($original), '/seo-master/v2/rollback');
        check($receipt['outcome'] === 'no_op' && $receipt['operation_type'] === 'rollback' && $fixture->store->writes === 0, 'no-op rollback inflated mutations');
        expect_error(fn() => $fixture->send(rollback_command($original, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'), '/seo-master/v2/rollback'), 'rollback_already_consumed');
        check($fixture->store->writes === 0, 'no-op consumed guard wrote');
    },
    'guard namespace is private and a guard cannot become an ordinary receipt' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $fixture->send(rollback_command($original), '/seo-master/v2/rollback');
        $identifier = guard_id($fixture);
        $route = '/seo-master/v2/operations/' . $identifier;
        expect_error(fn() => $fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', $identifier), 1793715600), 'invalid_route');
        $command = rollback_command($original, $identifier);
        expect_error(fn() => $fixture->send($command, '/seo-master/v2/rollback'), 'authentication_failed');
        $fixture->store->operations[ContractFixture::OPERATION]['receipt'] = $fixture->store->operations[$identifier]['receipt'];
        $route = '/seo-master/v2/operations/' . ContractFixture::OPERATION;
        expect_error(fn() => $fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', ContractFixture::OPERATION), 1793715600), 'operation_not_found');
    },
    'rollback trusted-gateway route signature and eligibility remain mandatory' => function (): void {
        $fixture = new ContractFixture();
        $original = $fixture->send($fixture->command());
        $command = rollback_command($original);
        $fixture->service = new SM_V2_Service(new SM_V2_Config(ContractFixture::PROJECT, ContractFixture::SITE, 'test-key', $fixture->secret, $fixture->receipt_secret, false), $fixture->store, '0.3.0-dev');
        expect_error(fn() => $fixture->send($command, '/seo-master/v2/rollback'), 'trusted_gateway_required');
        check($fixture->send($command, '/seo-master/v2/rollback/dry-run')['outcome'] === 'ready', 'safe rollback preview requires publish flag');
        $body = sm_v2_canonical($command);
        $headers = $fixture->headers('POST', '/seo-master/v2/rollback/dry-run', $body, $command['operation_id']);
        expect_error(fn() => $fixture->service->handle('POST', '/seo-master/v2/rollback', $body, $headers, 1793715600), 'authentication_failed');
        check($fixture->store->writes === 1 && count($fixture->store->operations) === 1, 'policy failure mutated');
    },
];
$tests['unverified receipts never schedule cache effects on status or same-id recovery'] = function (): void {
    foreach (['status', 'apply', 'rollback'] as $path) {
        foreach (['scope', 'signature', 'schema', 'cross_kind', 'rollback_type'] as $case) {
            if ($case === 'rollback_type' && $path !== 'rollback') { continue; }
            $fixture = new ContractFixture();
            $command = $fixture->command();
            $original = $fixture->send($command);
            if ($path === 'rollback') {
                $command = rollback_command($original);
                $fixture->send($command, '/seo-master/v2/rollback');
            }
            $operation = $command['operation_id'];
            $receipt = &$fixture->store->operations[$operation]['receipt'];
            if ($case === 'scope') { $receipt['site_id'] = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'; }
            if ($case === 'signature') { $receipt['signature'] = str_repeat('0', 64); }
            if ($case === 'schema') { $receipt['entity_id'] = 0; }
            if ($case === 'cross_kind') { $receipt['kind'] = 'rollback_guard'; }
            if ($case === 'rollback_type') {
                foreach (['operation_type', 'original_operation_id', 'original_receipt_id', 'original_request_hash', 'original_proposal_hash', 'reason'] as $field) { unset($receipt[$field]); }
                $receipt['outcome'] = 'applied_unverified';
                unset($receipt['signature']);
                $receipt['signature'] = sm_v2_receipt_signature($receipt, $fixture->receipt_secret);
            }
            unset($receipt);
            $fixture->store->events = [];
            $fixture->store->invalidated = [];
            $action = $path === 'status' ? function () use ($fixture, $operation): array {
                $route = '/seo-master/v2/operations/' . $operation;
                return $fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', $operation), 1793715600);
            } : fn() => $fixture->send($command, $path === 'apply' ? '/seo-master/v2/apply' : '/seo-master/v2/rollback');
            expect_error($action, $case === 'scope' && $path === 'status' ? 'operation_not_found' : 'needs_reconciliation');
            check($fixture->store->invalidated === [] &&
                array_filter($fixture->store->events, static fn(string $event): bool => str_starts_with($event, 'schedule:')) === [],
                'unverified/wrong-type receipt scheduled cache invalidation');
        }
    }
};
$tests['original guard dry-run conflict and missing status do not schedule recovery invalidation'] = function (): void {
    $fixture = new ContractFixture();
    $command = $fixture->command();
    $original = $fixture->send($command);
    $reverse = rollback_command($original);
    foreach (['dry-run', 'rollback/dry-run'] as $route) {
        $fixture->store->events = [];
        $preview = $route === 'dry-run' ? $fixture->command() : $reverse;
        check($fixture->send($preview, '/seo-master/v2/' . $route)['outcome'] === 'ready', 'preview failed');
        check(array_filter($fixture->store->events, static fn(string $event): bool => str_starts_with($event, 'schedule:')) === [], 'dry-run or original lookup scheduled invalidation');
    }
    $fixture->store->events = [];
    $fixture->send($reverse, '/seo-master/v2/rollback');
    check(array_filter($fixture->store->events, static fn(string $event): bool => str_starts_with($event, 'schedule:')) === [], 'new rollback original lookup scheduled recovery invalidation');
    $fixture->store->events = [];
    expect_error(fn() => $fixture->send(rollback_command($original, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'), '/seo-master/v2/rollback'), 'rollback_already_consumed');
    check(array_filter($fixture->store->events, static fn(string $event): bool => str_starts_with($event, 'schedule:')) === [], 'consumed guard or linked receipt scheduled recovery invalidation');
    $fixture->store->events = [];
    $command['approval_ref'] = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
    expect_error(fn() => $fixture->send($command), 'idempotency_conflict');
    $missing = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
    $route = '/seo-master/v2/operations/' . $missing;
    expect_error(fn() => $fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', $missing), 1793715600), 'operation_not_found');
    check($fixture->store->invalidated === [] &&
        array_filter($fixture->store->events, static fn(string $event): bool => str_starts_with($event, 'schedule:')) === [], 'conflict/missing status scheduled invalidation');
};
$failures = [];
foreach ($tests as $name => $test) {
    try { $test(); } catch (Throwable $error) { $failures[] = "$name: {$error->getMessage()}"; }
}
foreach ($failures as $failure) { fwrite(STDERR, "FAIL: $failure\n"); }
printf("v2 contracts: %d/%d passed (memory store, not WP/DB evidence)\n", count($tests) - count($failures), count($tests));
exit($failures === [] ? 0 : 1);
