<?php
declare(strict_types=1);

define('ABSPATH', __DIR__);
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-protocol.php';
require __DIR__ . '/../../adapters/wordpress-plugin/includes/v2-service.php';

// Deliberately an in-memory contract double, NOT proof of WP/InnoDB/parallel/crash safety.
final class MemoryStore implements SM_V2_Store {
    public array $fields = ['title' => ['state' => 'missing'], 'description' => ['state' => 'missing'], 'jsonld' => ['state' => 'missing']];
    public array $operations = [];
    public array $nonces = [];
    public array $events = [];
    public int $writes = 0;
    public bool $fail_write = false;
    public bool $fail_storage = false;
    public bool $lose_commit_response = false;
    public bool $fail_rollback = false;
    public string $post_status = 'publish';
    private ?array $backup = null;
    public function check_storage(): void {
        if ($this->fail_storage) { throw new SM_V2_Error('unsupported_storage', 503); }
    }
    public function claim_nonce(string $key_id, string $nonce, int $expires): void {
        $key = $key_id . ':' . $nonce;
        if (isset($this->nonces[$key])) { throw new SM_V2_Error('replay', 409); }
        $this->nonces[$key] = $expires;
    }
    public function begin(): void { $this->events[] = 'begin'; $this->backup = [$this->fields, $this->operations, $this->writes]; }
    public function lock_operation(string $operation_id, string $request_hash): ?array {
        if (!isset($this->operations[$operation_id])) { return null; }
        if (!hash_equals($this->operations[$operation_id]['request_hash'], $request_hash)) {
            throw new SM_V2_Error('idempotency_conflict', 409);
        }
        return $this->operations[$operation_id]['receipt'];
    }
    public function snapshot(int $entity_id): array {
        if ($this->post_status !== 'publish') { throw new SM_V2_Error('target_not_found', 404); }
        return ['entity_id' => $entity_id, 'post_type' => 'post', 'post_status' => $this->post_status, 'fields' => $this->fields];
    }
    public function write_fields(int $entity_id, array $fields): void {
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
    public function commit(): void {
        $this->events[] = 'commit';
        $this->backup = null;
        if ($this->lose_commit_response) { throw new SM_V2_Error('needs_reconciliation', 503); }
    }
    public function rollback(): void {
        if ($this->fail_rollback) { throw new SM_V2_Error('storage_failure', 503); }
        if ($this->backup !== null) { [$this->fields, $this->operations, $this->writes] = $this->backup; $this->backup = null; }
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
    public function __construct() {
        $this->secret = bin2hex(random_bytes(32));
        $this->receipt_secret = bin2hex(random_bytes(32));
        $this->store = new MemoryStore();
        $config = new SM_V2_Config(self::PROJECT, self::SITE, 'test-key', $this->secret, $this->receipt_secret, true);
        $this->service = new SM_V2_Service($config, $this->store, '0.3.0-dev');
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
    'ten retries + lost response return identical durable signed receipt' => function (): void {
        $fixture = new ContractFixture();
        $command = $fixture->command();
        $receipt = $fixture->send($command);
        for ($attempt = 0; $attempt < 10; ++$attempt) { check($fixture->send($command) === $receipt, 'retry changed receipt'); }
        check($fixture->store->writes === 1, 'duplicate mutation');
        $route = '/seo-master/v2/operations/' . ContractFixture::OPERATION;
        $fixture->store->events = [];
        check($fixture->service->handle('GET', $route, '', $fixture->headers('GET', $route, '', ContractFixture::OPERATION), 1793715600) === $receipt, 'status lost receipt');
        check($fixture->store->events === ['begin', 'read', 'commit'], 'status receipt was read outside an explicit transaction');
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
$failures = [];
foreach ($tests as $name => $test) {
    try { $test(); } catch (Throwable $error) { $failures[] = "$name: {$error->getMessage()}"; }
}
foreach ($failures as $failure) { fwrite(STDERR, "FAIL: $failure\n"); }
printf("v2 contracts: %d/%d passed (memory store, not WP/DB evidence)\n", count($tests) - count($failures), count($tests));
exit($failures === [] ? 0 : 1);
