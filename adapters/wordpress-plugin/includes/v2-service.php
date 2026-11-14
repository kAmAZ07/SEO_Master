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
    public function restore_fields(int $entity_id, array $fields): void;
    public function save_operation(string $operation_id, string $request_hash, array $receipt): void;
    public function read_operation(string $operation_id): ?array;
    public function schedule_cache_invalidation(int $entity_id): void;
    public function commit(): void;
    public function rollback(): void;
}

interface SM_V2_Target_Store extends SM_V2_Store {
    public function assert_target_url(int $entity_id, string $url): void;
}

final class SM_V2_Service {
    private Closure $clock;
    public function __construct(private SM_V2_Config $config, private SM_V2_Store $store, private string $plugin_version, ?Closure $clock = null) {
        $this->clock = $clock ?? static fn(): int => time();
    }

    public function handle(string $method, string $route, string $body, array $headers, int $now): array {
        $status_operation = null;
        if ($method === 'GET' && preg_match('~\A/seo-master/v2/operations/([0-9a-f-]{36})\z~D', $route, $match) === 1 && sm_v2_uuid($match[1])) {
            $status_operation = $match[1];
        } elseif ($method !== 'POST' || !in_array($route, [
            '/seo-master/v2/challenge', '/seo-master/v2/snapshot', '/seo-master/v2/dry-run', '/seo-master/v2/apply',
            '/seo-master/v2/rollback', '/seo-master/v2/rollback/dry-run',
            '/seo-master/v2/target-bound/apply', '/seo-master/v2/target-bound/dry-run',
            '/seo-master/v2/target-bound/rollback', '/seo-master/v2/target-bound/rollback/dry-run',
        ], true)) { throw new SM_V2_Error('invalid_route', 400); }
        if (strlen($body) > 16384) { throw new SM_V2_Error('request_too_large', 413); }
        $is_command = in_array($route, ['/seo-master/v2/dry-run', '/seo-master/v2/apply', '/seo-master/v2/rollback', '/seo-master/v2/rollback/dry-run', '/seo-master/v2/target-bound/apply', '/seo-master/v2/target-bound/dry-run', '/seo-master/v2/target-bound/rollback', '/seo-master/v2/target-bound/rollback/dry-run'], true);
        $operation = $headers['x-seo-operation-id'] ?? '';
        $this->authenticate($method, $route, $body, $headers, $now, $is_command || $status_operation !== null);
        if ($status_operation !== null) {
            if ($body !== '' || $operation !== $status_operation) { throw new SM_V2_Error('invalid_schema', 400); }
            return $this->transaction(function () use ($status_operation): array {
                $receipt = $this->store->read_operation($status_operation);
                if ($receipt === null || ($receipt['project_id'] ?? null) !== $this->config->project() ||
                    ($receipt['site_id'] ?? null) !== $this->config->site()) { throw new SM_V2_Error('operation_not_found', 404); }
                $receipt = $this->checked_receipt($receipt, $status_operation);
                if ($receipt['applied_fields'] !== []) { $this->store->schedule_cache_invalidation($receipt['entity_id']); }
                return $receipt;
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
        if (in_array($route, ['/seo-master/v2/rollback', '/seo-master/v2/rollback/dry-run', '/seo-master/v2/target-bound/rollback', '/seo-master/v2/target-bound/rollback/dry-run'], true)) {
            return $this->reverse($route, $payload, $operation, hash('sha256', $body), $now);
        }
        $target_bound = str_starts_with($route, '/seo-master/v2/target-bound/');
        $required = [
            'project_id', 'site_id', 'entity_id', 'operation_id', 'task_id', 'proposal_version',
            'proposal_hash', 'approval_ref', 'before_digest', 'expected_after_digest', 'changes',
        ];
        if ($target_bound) {
            $required = array_merge($required, ['target_url', 'authorization_deadline']);
            if (!is_string($payload['target_url'] ?? null) || strlen($payload['target_url']) > 2048 ||
                !is_int($payload['authorization_deadline'] ?? null)) { throw new SM_V2_Error('invalid_schema', 400); }
        }
        sm_v2_keys($payload, $required);
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
        $apply = in_array($route, ['/seo-master/v2/apply', '/seo-master/v2/target-bound/apply'], true);
        if ($apply && !$this->config->can_publish()) { throw new SM_V2_Error('trusted_gateway_required', 503); }
        $request_hash = hash('sha256', $body);
        return $this->transaction(function () use ($route, $payload, $fields, $request_hash, $now, $apply, $target_bound): array {
            if ($apply) {
                $existing = $this->store->lock_operation($payload['operation_id'], $request_hash);
                if ($existing !== null) {
                    $existing = $this->checked_receipt($existing, $payload['operation_id'], $request_hash);
                    if ($existing['applied_fields'] !== []) { $this->store->schedule_cache_invalidation($existing['entity_id']); }
                    return $existing;
                }
            }
            $before = $this->store->snapshot($payload['entity_id']);
            sm_v2_validate_snapshot($before, true);
            if ($target_bound) {
                if (!$this->store instanceof SM_V2_Target_Store) { throw new SM_V2_Error('unsupported_target_binding', 422); }
                $this->store->assert_target_url($payload['entity_id'], $payload['target_url']);
                $now = ($this->clock)();
                if ($now >= $payload['authorization_deadline']) { throw new SM_V2_Error('authorization_expired', 409); }
            }
            if (!hash_equals(sm_v2_digest($before), $payload['before_digest'])) { throw new SM_V2_Error('precondition_conflict', 409); }
            $after = $before;
            foreach ($fields as $field => $value) { $after['fields'][$field] = $value; }
            if (!hash_equals(sm_v2_digest($after), $payload['expected_after_digest'])) { throw new SM_V2_Error('after_digest_conflict', 409); }
            if (!$apply) {
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
            if ($target_bound && ($this->clock)() >= $payload['authorization_deadline']) { throw new SM_V2_Error('authorization_expired', 409); }
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
            sm_v2_validate_receipt($receipt);
            $this->store->save_operation($payload['operation_id'], $request_hash, $receipt);
            return $receipt;
        });
    }
    private function reverse(string $route, array $payload, string $operation, string $request_hash, int $now): array {
        $target_bound = str_starts_with($route, '/seo-master/v2/target-bound/');
        $required = [
            'project_id', 'site_id', 'entity_id', 'operation_id', 'task_id', 'proposal_version', 'proposal_hash', 'approval_ref',
            'reason', 'original_operation_id', 'original_receipt_id', 'original_request_hash', 'original_proposal_hash',
            'before_digest', 'expected_after_digest',
        ];
        if ($target_bound) {
            $required = array_merge($required, ['operation_type', 'target_url', 'authorization_deadline']);
            if (($payload['operation_type'] ?? null) !== 'rollback' || !is_string($payload['target_url'] ?? null) ||
                strlen($payload['target_url']) > 2048 || !is_int($payload['authorization_deadline'] ?? null)) {
                throw new SM_V2_Error('invalid_schema', 400);
            }
        }
        sm_v2_keys($payload, $required);
        foreach (['operation_id', 'task_id', 'proposal_version', 'approval_ref', 'original_operation_id'] as $field) {
            if (!sm_v2_uuid($payload[$field])) { throw new SM_V2_Error('invalid_schema', 400); }
        }
        foreach (['proposal_hash', 'original_receipt_id', 'original_request_hash', 'original_proposal_hash', 'before_digest', 'expected_after_digest'] as $field) {
            if (!is_string($payload[$field]) || preg_match('/\A[0-9a-f]{64}\z/D', $payload[$field]) !== 1) { throw new SM_V2_Error('invalid_schema', 400); }
        }
        sm_v2_text($payload['reason'], 1024);
        if ($payload['operation_id'] === $payload['original_operation_id']) { throw new SM_V2_Error('invalid_schema', 400); }
        if ($operation !== $payload['operation_id']) { throw new SM_V2_Error('scope_mismatch', 403); }
        if (!hash_equals(sm_v2_rollback_proposal_hash($payload), $payload['proposal_hash'])) { throw new SM_V2_Error('proposal_hash_conflict', 409); }
        $dry_run = in_array($route, ['/seo-master/v2/rollback/dry-run', '/seo-master/v2/target-bound/rollback/dry-run'], true);
        if (!$dry_run && !$this->config->can_publish()) { throw new SM_V2_Error('trusted_gateway_required', 503); }
        return $this->transaction(function () use ($payload, $request_hash, $now, $dry_run, $target_bound): array {
            if (!$dry_run) {
                $existing = $this->store->lock_operation($payload['operation_id'], $request_hash);
                if ($existing !== null) {
                    $existing = $this->checked_receipt($existing, $payload['operation_id'], $request_hash);
                    if (($existing['operation_type'] ?? null) !== 'rollback') { throw new SM_V2_Error('needs_reconciliation', 503); }
                    if ($existing['applied_fields'] !== []) { $this->store->schedule_cache_invalidation($existing['entity_id']); }
                    return $existing;
                }
            }
            $original = $this->store->read_operation($payload['original_operation_id']);
            if ($original === null || ($original['project_id'] ?? null) !== $this->config->project() ||
                ($original['site_id'] ?? null) !== $this->config->site() || ($original['entity_id'] ?? null) !== $payload['entity_id']) {
                throw new SM_V2_Error('operation_not_found', 404);
            }
            $original = $this->checked_receipt($original, $payload['original_operation_id']);
            if (($original['operation_type'] ?? 'apply') !== 'apply') { throw new SM_V2_Error('unsupported_original_operation', 422); }
            foreach (['receipt_id', 'request_hash', 'proposal_hash'] as $field) {
                if (!hash_equals($original[$field], $payload['original_' . $field])) { throw new SM_V2_Error('original_reference_conflict', 409); }
            }
            if ($target_bound && ($payload['task_id'] !== $original['task_id'] || $payload['proposal_version'] !== $original['proposal_version'] ||
                $payload['approval_ref'] !== $original['approval_ref'])) { throw new SM_V2_Error('original_reference_conflict', 409); }
            if (!hash_equals($original['after_digest'], $payload['before_digest']) ||
                !hash_equals($original['before_digest'], $payload['expected_after_digest'])) { throw new SM_V2_Error('original_reference_conflict', 409); }
            sm_v2_validate_snapshot($original['before'], true);
            if (in_array('jsonld', $original['applied_fields'], true) && !$this->config->can_write_jsonld()) { throw new SM_V2_Error('unsupported_field', 422); }
            $identity = sm_v2_guard_identity($original);
            try {
                $guard = $dry_run ? $this->store->read_operation($identity['guard_id']) :
                    $this->store->lock_operation($identity['guard_id'], $identity['identity_digest']);
            } catch (SM_V2_Error $error) {
                if ($error->error_code === 'idempotency_conflict') { throw new SM_V2_Error('needs_reconciliation', 503); }
                throw $error;
            }
            if ($guard !== null) {
                $this->check_consumed_guard($guard, $identity, $original);
                throw new SM_V2_Error('rollback_already_consumed', 409);
            }
            $before = $this->store->snapshot($payload['entity_id']);
            if ($target_bound) {
                if (!$this->store instanceof SM_V2_Target_Store) { throw new SM_V2_Error('unsupported_target_binding', 422); }
                $this->store->assert_target_url($payload['entity_id'], $payload['target_url']);
                $now = ($this->clock)();
                if ($now >= $payload['authorization_deadline']) { throw new SM_V2_Error('authorization_expired', 409); }
            }
            if (!hash_equals(sm_v2_digest($before), $original['after_digest'])) { throw new SM_V2_Error('precondition_conflict', 409); }
            $after = $original['before'];
            if ($dry_run) {
                return [
                    'outcome' => 'ready', 'operation_type' => 'rollback', 'before' => $before, 'after' => $after,
                    'before_digest' => sm_v2_digest($before), 'after_digest' => sm_v2_digest($after), 'proposal_hash' => $payload['proposal_hash'],
                    'original_operation_id' => $original['operation_id'], 'original_receipt_id' => $original['receipt_id'],
                    'warnings' => ['trusted_gateway_must_validate_hitl', 'public_html_not_verified'],
                ];
            }
            $changed = [];
            foreach (['title', 'description', 'jsonld'] as $field) {
                if ($before['fields'][$field] !== $after['fields'][$field]) { $changed[$field] = $after['fields'][$field]; }
            }
            if ($target_bound && ($this->clock)() >= $payload['authorization_deadline']) { throw new SM_V2_Error('authorization_expired', 409); }
            if ($changed !== []) { $this->store->restore_fields($payload['entity_id'], $changed); }
            if (!hash_equals(sm_v2_digest($this->store->snapshot($payload['entity_id'])), $original['before_digest'])) {
                throw new SM_V2_Error('storage_postcondition_failed', 503);
            }
            $receipt = [
                'protocol' => 'seo-master/v2', 'kind' => 'receipt', 'operation_type' => 'rollback',
                'receipt_id' => sm_v2_receipt_id($payload['operation_id'], $request_hash),
                'operation_id' => $payload['operation_id'], 'project_id' => $payload['project_id'], 'site_id' => $payload['site_id'],
                'entity_id' => $payload['entity_id'], 'task_id' => $payload['task_id'], 'proposal_version' => $payload['proposal_version'],
                'proposal_hash' => $payload['proposal_hash'], 'approval_ref' => $payload['approval_ref'], 'request_hash' => $request_hash,
                'before_digest' => sm_v2_digest($before), 'after_digest' => sm_v2_digest($after), 'before' => $before, 'after' => $after,
                'original_operation_id' => $original['operation_id'], 'original_receipt_id' => $original['receipt_id'],
                'original_request_hash' => $original['request_hash'], 'original_proposal_hash' => $original['proposal_hash'], 'reason' => $payload['reason'],
                'plugin_version' => $this->plugin_version, 'key_id' => $this->config->key_id(), 'applied_at' => gmdate('Y-m-d\TH:i:s\Z', $now),
                'applied_fields' => array_keys($changed), 'outcome' => $changed === [] ? 'no_op' : 'rollback_applied_unverified',
                'warnings' => ['trusted_gateway_must_validate_hitl', 'public_html_not_verified'],
            ];
            $receipt['signature'] = $this->config->sign_receipt($receipt);
            $receipt = sm_v2_decode(sm_v2_canonical($receipt), 131072);
            sm_v2_validate_receipt($receipt);
            $guard = [
                'protocol' => 'seo-master/v2', 'kind' => 'rollback_guard', 'guard_id' => $identity['guard_id'],
                'identity_digest' => $identity['identity_digest'], 'references' => $identity['references'],
                'rollback_operation_id' => $receipt['operation_id'], 'rollback_receipt_id' => $receipt['receipt_id'],
                'rollback_request_hash' => $request_hash,
            ];
            $guard['signature'] = $this->config->sign_guard($guard);
            $this->store->save_operation($payload['operation_id'], $request_hash, $receipt);
            $this->store->save_operation($identity['guard_id'], $identity['identity_digest'], $guard);
            return $receipt;
        });
    }

    private function check_consumed_guard(array $guard, array $identity, array $original): void {
        try {
            sm_v2_keys($guard, ['protocol', 'kind', 'guard_id', 'identity_digest', 'references', 'rollback_operation_id', 'rollback_receipt_id', 'rollback_request_hash', 'signature']);
            $unsigned = $guard;
            unset($unsigned['signature']);
            if ($guard['protocol'] !== 'seo-master/v2' || $guard['kind'] !== 'rollback_guard' || $guard['guard_id'] !== $identity['guard_id'] ||
                !is_string($guard['identity_digest']) || !hash_equals($identity['identity_digest'], $guard['identity_digest']) ||
                !is_array($guard['references']) || sm_v2_canonical($guard['references']) !== sm_v2_canonical($identity['references']) ||
                !sm_v2_uuid($guard['rollback_operation_id']) || !is_string($guard['rollback_request_hash']) ||
                preg_match('/\A[0-9a-f]{64}\z/D', $guard['rollback_request_hash']) !== 1 ||
                !is_string($guard['rollback_receipt_id']) || !hash_equals(sm_v2_receipt_id($guard['rollback_operation_id'], $guard['rollback_request_hash']), $guard['rollback_receipt_id']) ||
                !is_string($guard['signature']) || !hash_equals($this->config->sign_guard($unsigned), $guard['signature'])) {
                throw new SM_V2_Error('needs_reconciliation', 503);
            }
            // Bounded PRIMARY KEY lookup, never an unindexed JSON/full-ledger search.
            $receipt = $this->store->read_operation($guard['rollback_operation_id']);
            if ($receipt === null) { throw new SM_V2_Error('needs_reconciliation', 503); }
            $receipt = $this->checked_receipt($receipt, $guard['rollback_operation_id'], $guard['rollback_request_hash']);
            if (($receipt['operation_type'] ?? null) !== 'rollback' || $receipt['receipt_id'] !== $guard['rollback_receipt_id'] ||
                $receipt['original_operation_id'] !== $original['operation_id'] || $receipt['original_receipt_id'] !== $original['receipt_id'] ||
                $receipt['original_request_hash'] !== $original['request_hash'] || $receipt['original_proposal_hash'] !== $original['proposal_hash'] ||
                $receipt['before_digest'] !== $original['after_digest'] || $receipt['after_digest'] !== $original['before_digest']) {
                throw new SM_V2_Error('needs_reconciliation', 503);
            }
        } catch (SM_V2_Error $error) { throw new SM_V2_Error('needs_reconciliation', 503); }
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
        try { sm_v2_validate_receipt($receipt); }
        catch (SM_V2_Error $error) { throw new SM_V2_Error('needs_reconciliation', 503); }
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
