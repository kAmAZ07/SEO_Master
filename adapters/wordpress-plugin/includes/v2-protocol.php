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
