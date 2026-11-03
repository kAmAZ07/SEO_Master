<?php
declare(strict_types=1);
if (!defined('ABSPATH')) { exit; }

interface SM_V2_Store {
    public function check_storage(): void;
    public function claim_nonce(string $key_id, string $nonce, int $expires): void;
    public function begin(): void;
    public function lock_operation(string $operation_id, string $request_hash): ?array;
    public function snapshot(int $entity_id): array;
    public function write_fields(int $entity_id, array $fields): void;
    public function save_operation(string $operation_id, string $request_hash, array $receipt): void;
    public function read_operation(string $operation_id): ?array;
    public function commit(): void;
    public function rollback(): void;
}

final class SM_V2_Service {
    public function __construct(private SM_V2_Config $config, private SM_V2_Store $store, private string $plugin_version) {}

    public function handle(string $method, string $route, string $body, array $headers, int $now): array {
        $status_operation = null;
        if ($method === 'GET' && preg_match('~\A/seo-master/v2/operations/([0-9a-f-]{36})\z~D', $route, $match) === 1 && sm_v2_uuid($match[1])) {
            $status_operation = $match[1];
        } elseif ($method !== 'POST' || !in_array($route, [
            '/seo-master/v2/challenge', '/seo-master/v2/snapshot', '/seo-master/v2/dry-run', '/seo-master/v2/apply',
        ], true)) { throw new SM_V2_Error('invalid_route', 400); }
        if (strlen($body) > 16384) { throw new SM_V2_Error('request_too_large', 413); }
        $is_command = in_array($route, ['/seo-master/v2/dry-run', '/seo-master/v2/apply'], true);
        $operation = $headers['x-seo-operation-id'] ?? '';
        $this->authenticate($method, $route, $body, $headers, $now, $is_command || $status_operation !== null);
        if ($status_operation !== null) {
            if ($body !== '' || $operation !== $status_operation) { throw new SM_V2_Error('invalid_schema', 400); }
            return $this->transaction(function () use ($status_operation): array {
                $receipt = $this->store->read_operation($status_operation);
                if ($receipt === null || ($receipt['project_id'] ?? null) !== $this->config->project() ||
                    ($receipt['site_id'] ?? null) !== $this->config->site()) { throw new SM_V2_Error('operation_not_found', 404); }
                return $this->checked_receipt($receipt, $status_operation);
            });
        }
        $payload = sm_v2_decode($body);
        if (($payload['project_id'] ?? null) !== $this->config->project() ||
            ($payload['site_id'] ?? null) !== $this->config->site()) { throw new SM_V2_Error('scope_mismatch', 403); }
        if ($route === '/seo-master/v2/challenge') {
            sm_v2_keys($payload, ['project_id', 'site_id', 'challenge']);
            if (!is_string($payload['challenge']) || preg_match('/\A[0-9a-f]{64}\z/D', $payload['challenge']) !== 1) {
                throw new SM_V2_Error('invalid_schema', 400);
            }
            $response = [
                'protocol' => 'seo-master/v2', 'kind' => 'challenge', 'project_id' => $this->config->project(),
                'site_id' => $this->config->site(), 'key_id' => $this->config->key_id(),
                'challenge' => $payload['challenge'], 'issued_at' => gmdate('Y-m-d\TH:i:s\Z', $now),
                'expires_at' => gmdate('Y-m-d\TH:i:s\Z', $now + 60), 'plugin_version' => $this->plugin_version,
            ];
            $response['signature'] = $this->config->sign_receipt($response);
            return $response;
        }
        if (!isset($payload['entity_id']) || !is_int($payload['entity_id']) || $payload['entity_id'] < 1 || $payload['entity_id'] > 2147483647) {
            throw new SM_V2_Error('invalid_schema', 400);
        }
        if ($route === '/seo-master/v2/snapshot') {
            sm_v2_keys($payload, ['project_id', 'site_id', 'entity_id']);
            return $this->transaction(function () use ($payload): array {
                $snapshot = $this->store->snapshot($payload['entity_id']);
                return ['snapshot' => $snapshot, 'digest' => sm_v2_digest($snapshot)];
            });
        }
        sm_v2_keys($payload, [
            'project_id', 'site_id', 'entity_id', 'operation_id', 'task_id', 'proposal_version',
            'proposal_hash', 'approval_ref', 'before_digest', 'expected_after_digest', 'changes',
        ]);
        foreach (['operation_id', 'task_id', 'proposal_version', 'approval_ref'] as $field) {
            if (!sm_v2_uuid($payload[$field])) { throw new SM_V2_Error('invalid_schema', 400); }
        }
        foreach (['proposal_hash', 'before_digest', 'expected_after_digest'] as $field) {
            if (!is_string($payload[$field]) || preg_match('/\A[0-9a-f]{64}\z/D', $payload[$field]) !== 1) {
                throw new SM_V2_Error('invalid_schema', 400);
            }
        }
        if ($operation !== $payload['operation_id']) { throw new SM_V2_Error('scope_mismatch', 403); }
        $fields = sm_v2_validate_changes($payload['changes']);
        if (isset($fields['jsonld']) && !$this->config->can_write_jsonld()) { throw new SM_V2_Error('unsupported_field', 422); }
        if (!hash_equals(sm_v2_proposal_hash($payload), $payload['proposal_hash'])) { throw new SM_V2_Error('proposal_hash_conflict', 409); }
        if ($route === '/seo-master/v2/apply' && !$this->config->can_publish()) { throw new SM_V2_Error('trusted_gateway_required', 503); }
        $request_hash = hash('sha256', $body);
        return $this->transaction(function () use ($route, $payload, $fields, $request_hash, $now): array {
            if ($route === '/seo-master/v2/apply') {
                $existing = $this->store->lock_operation($payload['operation_id'], $request_hash);
                if ($existing !== null) { return $this->checked_receipt($existing, $payload['operation_id'], $request_hash); }
            }
            $before = $this->store->snapshot($payload['entity_id']);
            if (!hash_equals(sm_v2_digest($before), $payload['before_digest'])) { throw new SM_V2_Error('precondition_conflict', 409); }
            $after = $before;
            foreach ($fields as $field => $value) { $after['fields'][$field] = $value; }
            if (!hash_equals(sm_v2_digest($after), $payload['expected_after_digest'])) { throw new SM_V2_Error('after_digest_conflict', 409); }
            if ($route === '/seo-master/v2/dry-run') {
                return [
                    'outcome' => 'ready', 'before' => $before, 'after' => $after,
                    'before_digest' => sm_v2_digest($before), 'after_digest' => sm_v2_digest($after),
                    'proposal_hash' => $payload['proposal_hash'], 'warnings' => ['trusted_gateway_must_validate_hitl', 'public_html_not_verified'],
                ];
            }
            $changed = [];
            foreach ($fields as $field => $value) {
                if ($before['fields'][$field] !== $value) { $changed[$field] = $value; }
            }
            if ($changed !== []) { $this->store->write_fields($payload['entity_id'], $changed); }
            // Read back under the same locks. Silent truncation, escaping, triggers or mixed state cannot be called applied.
            if (!hash_equals(sm_v2_digest($this->store->snapshot($payload['entity_id'])), $payload['expected_after_digest'])) {
                throw new SM_V2_Error('storage_postcondition_failed', 503);
            }
            $receipt = [
                'protocol' => 'seo-master/v2', 'kind' => 'receipt',
                'receipt_id' => hash('sha256', "SEO-MASTER-RECEIPT-ID-V2\n" . $payload['operation_id'] . "\n" . $request_hash),
                'operation_id' => $payload['operation_id'], 'project_id' => $payload['project_id'], 'site_id' => $payload['site_id'],
                'entity_id' => $payload['entity_id'], 'task_id' => $payload['task_id'], 'proposal_version' => $payload['proposal_version'],
                'proposal_hash' => $payload['proposal_hash'], 'approval_ref' => $payload['approval_ref'], 'request_hash' => $request_hash,
                'before_digest' => sm_v2_digest($before), 'after_digest' => sm_v2_digest($after), 'before' => $before, 'after' => $after,
                'plugin_version' => $this->plugin_version, 'key_id' => $this->config->key_id(), 'applied_at' => gmdate('Y-m-d\TH:i:s\Z', $now),
                'applied_fields' => array_keys($changed), 'outcome' => $changed === [] ? 'no_op' : 'applied_unverified',
                'warnings' => ['trusted_gateway_must_validate_hitl', 'public_html_not_verified'],
            ];
            $receipt['signature'] = $this->config->sign_receipt($receipt);
            $receipt = sm_v2_decode(sm_v2_canonical($receipt), 131072);
            $this->store->save_operation($payload['operation_id'], $request_hash, $receipt);
            return $receipt;
        });
    }

    private function authenticate(string $method, string $route, string $body, array $headers, int $now, bool $has_operation): void {
        $patterns = [
            'x-seo-key-id' => '/\A[A-Za-z0-9_-]{1,64}\z/D', 'x-seo-timestamp' => '/\A[0-9]{10}\z/D',
            'x-seo-nonce' => '/\A[A-Za-z0-9_-]{32,64}\z/D', 'x-seo-signature' => '/\A[0-9a-f]{64}\z/D',
        ];
        foreach ($patterns as $header => $pattern) {
            if (!isset($headers[$header]) || !is_string($headers[$header]) || preg_match($pattern, $headers[$header]) !== 1) {
                throw new SM_V2_Error('authentication_failed', 401);
            }
        }
        if (($headers['x-seo-project-id'] ?? null) !== $this->config->project() ||
            ($headers['x-seo-site-id'] ?? null) !== $this->config->site() ||
            $headers['x-seo-key-id'] !== $this->config->key_id() ||
            ($has_operation ? !sm_v2_uuid($headers['x-seo-operation-id'] ?? null) : ($headers['x-seo-operation-id'] ?? null) !== '-') ||
            abs($now - (int) $headers['x-seo-timestamp']) > 300 ||
            !$this->config->verify(sm_v2_signing_message($method, $route, $body, $headers), $headers['x-seo-signature'])) {
            throw new SM_V2_Error('authentication_failed', 401);
        }
        $this->store->check_storage();
        // Claimed in its own committed statement, before the business transaction. Failure/retry consumes the nonce.
        $this->store->claim_nonce($headers['x-seo-key-id'], $headers['x-seo-nonce'], (int) $headers['x-seo-timestamp'] + 301);
    }

    private function checked_receipt(array $receipt, string $operation_id, ?string $request_hash = null): array {
        $signature = $receipt['signature'] ?? null;
        $unsigned = $receipt;
        unset($unsigned['signature']);
        if (!is_string($signature) || ($receipt['kind'] ?? null) !== 'receipt' ||
            ($receipt['operation_id'] ?? null) !== $operation_id || ($receipt['project_id'] ?? null) !== $this->config->project() ||
            ($receipt['site_id'] ?? null) !== $this->config->site() ||
            ($request_hash !== null && ($receipt['request_hash'] ?? null) !== $request_hash) ||
            !hash_equals($this->config->sign_receipt($unsigned), $signature)) {
            throw new SM_V2_Error('needs_reconciliation', 503);
        }
        return $receipt;
    }

    private function transaction(callable $action): array {
        $this->store->begin();
        try {
            $result = $action();
            $this->store->commit();
            return $result;
        } catch (Throwable $error) {
            try { $this->store->rollback(); }
            catch (Throwable $rollback_error) { throw new SM_V2_Error('needs_reconciliation', 503); }
            if ($error instanceof SM_V2_Error) { throw $error; }
            throw new SM_V2_Error('internal_failure', 500);
        }
    }
}
