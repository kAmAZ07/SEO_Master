<?php
declare(strict_types=1);
if (!defined('ABSPATH')) { exit; }

final class SM_V2_Error extends RuntimeException {
    public function __construct(public string $error_code, public int $http_status) {
        parent::__construct($error_code);
    }
}
function sm_v2_convert_document($value) {
    if ($value instanceof stdClass) {
        $converted = [];
        foreach (get_object_vars($value) as $key => $entry) {
            // Protocol object names are fixed nonnumeric schema names. Do not silently collapse object keys into list indices.
            if (!is_string($key) || ctype_digit($key)) { throw new SM_V2_Error('invalid_schema', 400); }
            $converted[$key] = sm_v2_convert_document($entry);
        }
        return $converted;
    }
    if (is_array($value)) { return array_map('sm_v2_convert_document', $value); }
    return $value;
}

function sm_v2_uuid($value): bool {
    return is_string($value) && preg_match('/\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z/D', $value) === 1;
}

final class SM_V2_Config {
    public function __construct(
        private string $project, private string $site, private string $key_id,
        private string $secret, private string $receipt_secret, private bool $trusted_gateway, private bool $allow_jsonld = true
    ) {
        if (!sm_v2_uuid($project) || !sm_v2_uuid($site) ||
            preg_match('/\A[A-Za-z0-9_-]{1,64}\z/D', $key_id) !== 1 ||
            preg_match('/\A[0-9a-f]{64,128}\z/D', $secret) !== 1 ||
            preg_match('/\A[0-9a-f]{64,128}\z/D', $receipt_secret) !== 1 ||
            count(array_unique(str_split($secret))) < 8 ||
            count(array_unique(str_split($receipt_secret))) < 8 ||
            hash_equals($secret, $receipt_secret)) {
            throw new SM_V2_Error('not_paired', 503);
        }
    }
    public function project(): string { return $this->project; }
    public function site(): string { return $this->site; }
    public function key_id(): string { return $this->key_id; }
    public function can_publish(): bool { return $this->trusted_gateway; }
    public function can_write_jsonld(): bool { return $this->allow_jsonld; }
    public function verify(string $message, string $signature): bool {
        return hash_equals(hash_hmac('sha256', $message, $this->secret), $signature);
    }
    public function sign_receipt(array $receipt): string {
        return sm_v2_receipt_signature($receipt, $this->receipt_secret);
    }
    public function sign_guard(array $guard): string {
        return sm_v2_guard_signature($guard, $this->receipt_secret);
    }
}

function sm_v2_is_list(array $values): bool {
    return $values === [] || array_keys($values) === range(0, count($values) - 1);
}

function sm_v2_canonical($value): string {
    if (is_array($value)) {
        if (!sm_v2_is_list($value)) { ksort($value, SORT_STRING); }
        $encoded = [];
        foreach ($value as $key => $entry) {
            $encoded[] = (sm_v2_is_list($value) ? '' : sm_v2_canonical((string) $key) . ':') . sm_v2_canonical($entry);
        }
        return sm_v2_is_list($value) ? '[' . implode(',', $encoded) . ']' : '{' . implode(',', $encoded) . '}';
    }
    if (!is_string($value) && !is_int($value) && !is_bool($value) && $value !== null) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    try {
        return json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_LINE_TERMINATORS | JSON_THROW_ON_ERROR);
    } catch (JsonException $error) { throw new SM_V2_Error('invalid_schema', 400); }
}
function sm_v2_digest(array $snapshot): string { return hash('sha256', sm_v2_canonical($snapshot)); }
function sm_v2_proposal_hash(array $command): string {
    $proposal = $command;
    unset($proposal['operation_id'], $proposal['approval_ref'], $proposal['proposal_hash']);
    return hash('sha256', "SEO-MASTER-PROPOSAL-V2\n" . sm_v2_canonical($proposal));
}
function sm_v2_receipt_signature(array $receipt, string $secret): string {
    $key = hash_hmac('sha256', 'SEO-MASTER-RECEIPT-KEY-V2', $secret, true);
    return hash_hmac('sha256', "SEO-MASTER-RECEIPT-V2\n" . sm_v2_canonical($receipt), $key);
}
function sm_v2_signing_message(string $method, string $route, string $body, array $headers): string {
    return implode("\n", [
        'SEO-MASTER-HMAC-V2', $method, $route, hash('sha256', $body),
        $headers['x-seo-timestamp'] ?? '', $headers['x-seo-nonce'] ?? '', $headers['x-seo-key-id'] ?? '',
        $headers['x-seo-project-id'] ?? '', $headers['x-seo-site-id'] ?? '', $headers['x-seo-operation-id'] ?? '',
    ]);
}

// Native JSON decoding accepts duplicate names; explicitly reject them, including escaped aliases.
function sm_v2_decode(string $body, int $max_bytes = 16384): array {
    if (strlen($body) > $max_bytes || preg_match('//u', $body) !== 1 || str_contains($body, "\0")) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    try { $decoded = json_decode($body, false, 16, JSON_THROW_ON_ERROR); }
    catch (JsonException $error) { throw new SM_V2_Error('invalid_schema', 400); }
    if (!($decoded instanceof stdClass)) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    // Preserve native JSON kinds until compound-schema validation. An object {"0": ...} is NOT a changes array.
    if (property_exists($decoded, 'changes')) {
        if (!is_array($decoded->changes)) { throw new SM_V2_Error('invalid_schema', 400); }
        foreach ($decoded->changes as $operation) {
            if (!($operation instanceof stdClass)) { throw new SM_V2_Error('invalid_schema', 400); }
        }
    }
    sm_v2_reject_nuls($decoded);
    $stack = [];
    $length = strlen($body);
    for ($index = 0; $index < $length; ++$index) {
        $character = $body[$index];
        if ($character === '{' || $character === '[') {
            $stack[] = ['object' => $character === '{', 'key' => true, 'seen' => []];
        } elseif ($character === '}' || $character === ']') {
            array_pop($stack);
        } elseif ($character === ',' && $stack !== []) {
            $stack[count($stack) - 1]['key'] = true;
        } elseif ($character === '"') {
            $start = $index++;
            while ($index < $length && $body[$index] !== '"') {
                if ($body[$index] === '\\') { ++$index; }
                ++$index;
            }
            $frame = count($stack) - 1;
            if ($frame >= 0 && $stack[$frame]['object'] && $stack[$frame]['key']) {
                $key = json_decode(substr($body, $start, $index - $start + 1), true, 2, JSON_THROW_ON_ERROR);
                if (isset($stack[$frame]['seen'][$key])) { throw new SM_V2_Error('invalid_schema', 400); }
                $stack[$frame]['seen'][$key] = true;
                $stack[$frame]['key'] = false;
            }
        }
    }
    return sm_v2_convert_document($decoded);
}
function sm_v2_reject_nuls($value): void {
    if (is_string($value) && str_contains($value, "\0")) { throw new SM_V2_Error('invalid_schema', 400); }
    if ($value instanceof stdClass) { $value = get_object_vars($value); }
    if (is_array($value)) {
        foreach ($value as $key => $entry) { sm_v2_reject_nuls($key); sm_v2_reject_nuls($entry); }
    }
}

function sm_v2_keys(array $object, array $required, array $optional = []): void {
    if (array_diff($required, array_keys($object)) !== [] ||
        array_diff(array_keys($object), array_merge($required, $optional)) !== []) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
}
function sm_v2_text($value, int $max_bytes): string {
    if (!is_string($value) || $value === '' || trim($value) === '' || strlen($value) > $max_bytes ||
        preg_match('//u', $value) !== 1 || preg_match('/[<>\x00-\x1f\x7f]/u', $value) === 1 ||
        preg_match('/\A(?:N;|[aObisdCErR]:)/', trim($value)) === 1) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    return $value;
}
function sm_v2_jsonld($value): string {
    $json = sm_v2_text($value, 8192);
    $schema = sm_v2_decode($json);
    sm_v2_keys($schema, ['@context', '@type', 'name'], ['description']);
    if ($schema['@context'] !== 'https://schema.org' ||
        !in_array($schema['@type'], ['WebPage', 'Article', 'Organization'], true)) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    sm_v2_text($schema['name'], 512);
    if (array_key_exists('description', $schema)) { sm_v2_text($schema['description'], 2000); }
    // Preserve approved JSON bytes; no hidden normalization after approval.
    return $json;
}
function sm_v2_rollback_proposal_hash(array $command): string {
    unset($command['operation_id'], $command['approval_ref'], $command['proposal_hash']);
    return hash('sha256', "SEO-MASTER-ROLLBACK-PROPOSAL-V2\n" . sm_v2_canonical($command));
}
function sm_v2_receipt_id(string $operation, string $request_hash): string {
    return hash('sha256', "SEO-MASTER-RECEIPT-ID-V2\n" . $operation . "\n" . $request_hash);
}
function sm_v2_guard_signature(array $guard, string $secret): string {
    $key = hash_hmac('sha256', 'SEO-MASTER-ROLLBACK-GUARD-KEY-V2', $secret, true);
    return hash_hmac('sha256', "SEO-MASTER-ROLLBACK-GUARD-V2\n" . sm_v2_canonical($guard), $key);
}
function sm_v2_guard_identity(array $verified_original): array {
    $references = [
        'project_id' => $verified_original['project_id'], 'site_id' => $verified_original['site_id'],
        'entity_id' => $verified_original['entity_id'], 'original_operation_id' => $verified_original['operation_id'],
        'original_receipt_id' => $verified_original['receipt_id'], 'original_request_hash' => $verified_original['request_hash'],
    ];
    $digest = hash('sha256', "SEO-MASTER-ROLLBACK-GUARD-IDENTITY-V2\n" . sm_v2_canonical($references));
    $uuid = substr($digest, 0, 32);
    $uuid[12] = '8';
    $uuid[16] = dechex((hexdec($uuid[16]) & 3) | 8);
    return [
        'guard_id' => substr($uuid, 0, 8) . '-' . substr($uuid, 8, 4) . '-' . substr($uuid, 12, 4) . '-' . substr($uuid, 16, 4) . '-' . substr($uuid, 20, 12),
        'identity_digest' => $digest, 'references' => $references,
    ];
}
function sm_v2_validate_changes($changes): array {
    if (!is_array($changes) || !sm_v2_is_list($changes) || count($changes) < 1 || count($changes) > 3) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    $fields = [];
    foreach ($changes as $change) {
        if (!is_array($change) || !isset($change['op'], $change['field']) ||
            !is_string($change['field']) || !in_array($change['field'], ['title', 'description', 'jsonld'], true) ||
            isset($fields[$change['field']])) {
            throw new SM_V2_Error('invalid_schema', 400);
        }
        if ($change['op'] === 'remove') {
            sm_v2_keys($change, ['op', 'field']);
            $fields[$change['field']] = ['state' => 'missing'];
        } elseif ($change['op'] === 'set') {
            sm_v2_keys($change, ['op', 'field', 'value']);
            $value = $change['field'] === 'jsonld' ? sm_v2_jsonld($change['value']) :
                sm_v2_text($change['value'], $change['field'] === 'title' ? 512 : 2000);
            $fields[$change['field']] = ['state' => 'value', 'value' => $value];
        } else { throw new SM_V2_Error('invalid_schema', 400); }
    }
    return $fields;
}

// Only immutable, verified historical snapshots may use this path; ordinary set remains stricter.
function sm_v2_restore_state($state): array {
    try {
        if (!is_array($state) || !isset($state['state'])) { throw new SM_V2_Error('invalid_schema', 400); }
        if ($state['state'] === 'missing') { sm_v2_keys($state, ['state']); return $state; }
        sm_v2_keys($state, ['state', 'value']);
        $value = $state['value'];
        if ($state['state'] !== 'value' || !is_string($value) || strlen($value) > 8192 ||
            preg_match('//u', $value) !== 1 || preg_match('/[<>\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u', $value) === 1 ||
            preg_match('/\A(?:N;|[aObisdCErR]:)/', trim($value)) === 1) { throw new SM_V2_Error('invalid_schema', 400); }
        return $state;
    } catch (SM_V2_Error $error) { throw new SM_V2_Error('unsupported_snapshot', 422); }
}
function sm_v2_validate_snapshot($snapshot, bool $must_restore = false): array {
    if (!is_array($snapshot)) { throw new SM_V2_Error('invalid_schema', 400); }
    sm_v2_keys($snapshot, ['entity_id', 'post_type', 'post_status', 'fields']);
    if (!is_int($snapshot['entity_id']) || $snapshot['entity_id'] < 1 || $snapshot['entity_id'] > 2147483647 ||
        !in_array($snapshot['post_type'], ['post', 'page'], true) || $snapshot['post_status'] !== 'publish' ||
        !is_array($snapshot['fields'])) { throw new SM_V2_Error('invalid_schema', 400); }
    sm_v2_keys($snapshot['fields'], ['title', 'description', 'jsonld']);
    foreach ($snapshot['fields'] as $state) {
        if ($must_restore) { sm_v2_restore_state($state); continue; }
        if (!is_array($state) || !isset($state['state'])) { throw new SM_V2_Error('invalid_schema', 400); }
        if ($state['state'] === 'missing') { sm_v2_keys($state, ['state']); continue; }
        sm_v2_keys($state, ['state', 'value']);
        if ($state['state'] !== 'value' || !is_string($state['value']) || strlen($state['value']) > 8192 ||
            str_contains($state['value'], "\0") || preg_match('//u', $state['value']) !== 1) {
            throw new SM_V2_Error('invalid_schema', 400);
        }
    }
    return $snapshot;
}
function sm_v2_validate_receipt(array $receipt): void {
    $rollback = ($receipt['operation_type'] ?? 'apply') === 'rollback';
    $additional = $rollback ? ['operation_type', 'original_operation_id', 'original_receipt_id', 'original_request_hash', 'original_proposal_hash', 'reason'] : [];
    sm_v2_keys($receipt, array_merge([
        'protocol', 'kind', 'receipt_id', 'operation_id', 'project_id', 'site_id', 'entity_id', 'task_id',
        'proposal_version', 'proposal_hash', 'approval_ref', 'request_hash', 'before_digest', 'after_digest',
        'before', 'after', 'plugin_version', 'key_id', 'applied_at', 'applied_fields', 'outcome', 'warnings', 'signature',
    ], $additional), $rollback ? [] : ['operation_type']);
    if ($receipt['protocol'] !== 'seo-master/v2' || $receipt['kind'] !== 'receipt' ||
        !in_array($receipt['operation_type'] ?? 'apply', ['apply', 'rollback'], true)) { throw new SM_V2_Error('invalid_schema', 400); }
    foreach (['operation_id', 'project_id', 'site_id', 'task_id', 'proposal_version', 'approval_ref'] as $field) {
        if (!sm_v2_uuid($receipt[$field])) { throw new SM_V2_Error('invalid_schema', 400); }
    }
    foreach (['receipt_id', 'proposal_hash', 'request_hash', 'before_digest', 'after_digest', 'signature'] as $field) {
        if (!is_string($receipt[$field]) || preg_match('/\A[0-9a-f]{64}\z/D', $receipt[$field]) !== 1) { throw new SM_V2_Error('invalid_schema', 400); }
    }
    if (!is_int($receipt['entity_id']) || $receipt['entity_id'] < 1 || $receipt['entity_id'] > 2147483647 ||
        !is_string($receipt['plugin_version']) || preg_match('/\A[A-Za-z0-9._-]{1,32}\z/D', $receipt['plugin_version']) !== 1 ||
        !is_string($receipt['key_id']) || preg_match('/\A[A-Za-z0-9_-]{1,64}\z/D', $receipt['key_id']) !== 1 ||
        !is_string($receipt['applied_at']) || preg_match('/\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\z/D', $receipt['applied_at']) !== 1 ||
        strtotime($receipt['applied_at']) === false || gmdate('Y-m-d\TH:i:s\Z', strtotime($receipt['applied_at'])) !== $receipt['applied_at'] ||
        !hash_equals(sm_v2_receipt_id($receipt['operation_id'], $receipt['request_hash']), $receipt['receipt_id'])) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    $before = sm_v2_validate_snapshot($receipt['before']);
    $after = sm_v2_validate_snapshot($receipt['after']);
    if ($before['entity_id'] !== $receipt['entity_id'] || $after['entity_id'] !== $receipt['entity_id'] ||
        $before['post_type'] !== $after['post_type'] ||
        !hash_equals(sm_v2_digest($before), $receipt['before_digest']) || !hash_equals(sm_v2_digest($after), $receipt['after_digest']) ||
        !is_array($receipt['applied_fields']) || !sm_v2_is_list($receipt['applied_fields']) ||
        !is_array($receipt['warnings']) || !sm_v2_is_list($receipt['warnings']) || count($receipt['warnings']) > 16) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    $changed = [];
    foreach (['title', 'description', 'jsonld'] as $field) {
        if ($before['fields'][$field] !== $after['fields'][$field]) { $changed[] = $field; }
    }
    foreach ($receipt['applied_fields'] as $field) {
        if (!is_string($field) || !in_array($field, ['title', 'description', 'jsonld'], true)) { throw new SM_V2_Error('invalid_schema', 400); }
    }
    $applied = $receipt['applied_fields'];
    sort($applied, SORT_STRING);
    sort($changed, SORT_STRING);
    if ($applied !== $changed || $receipt['outcome'] !== ($changed === [] ? 'no_op' : ($rollback ? 'rollback_applied_unverified' : 'applied_unverified'))) {
        throw new SM_V2_Error('invalid_schema', 400);
    }
    foreach ($receipt['warnings'] as $warning) { sm_v2_text($warning, 128); }
    if ($rollback) {
        if (!sm_v2_uuid($receipt['original_operation_id']) || $receipt['original_operation_id'] === $receipt['operation_id']) { throw new SM_V2_Error('invalid_schema', 400); }
        foreach (['original_receipt_id', 'original_request_hash', 'original_proposal_hash'] as $field) {
            if (!is_string($receipt[$field]) || preg_match('/\A[0-9a-f]{64}\z/D', $receipt[$field]) !== 1) { throw new SM_V2_Error('invalid_schema', 400); }
        }
        sm_v2_text($receipt['reason'], 1024);
    }
}
